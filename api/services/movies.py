import time
import json
import boto3
import requests
import logging
import random
import asyncio
import aiohttp
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, List, Tuple, Dict, Any
from bs4 import BeautifulSoup
from schemas.movies import MoviesSearch, MovieResult


# Get a logger for this module
logger = logging.getLogger(__name__)

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table("movies")


DOMAIN = "https://letterboxd.com/"
# 6 hours   
TTL = 21600

# Rate limiting constants
MIN_DELAY = 0.5  # Minimum delay between requests in seconds  
MAX_DELAY = 1.0  # Maximum delay between requests in seconds
RETRY_DELAY = 10  # Delay after a 429 error in seconds
MAX_RETRIES = 2  # Maximum number of retries for a request
SEARCHING_DELAY = 3
REQUEST_TIMEOUT = 30  # Timeout for individual requests
BATCH_SIZE = 5  # Process movies in smaller batches
MAX_MOVIES_PER_REQUEST = 50  # Limit movies processed per API call

def make_request(url: str, retries: int = MAX_RETRIES) -> Optional[requests.Response]:
    """
    Make a request with rate limiting to avoid 429 errors
    """
    for attempt in range(retries + 1):
        try:
            # Random delay between requests to avoid detection
            if attempt > 0:
                delay = random.uniform(MIN_DELAY, MAX_DELAY)
                logger.info(f"Waiting {delay:.2f} seconds before request")
                time.sleep(delay)
            
            response = requests.get(
                url,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Referer': DOMAIN,
                },
                timeout=REQUEST_TIMEOUT
            )
            
            if response.status_code == 429:
                if attempt < retries:
                    logger.warning(f"Rate limited (429), waiting {RETRY_DELAY} seconds before retry {attempt+1}/{retries}")
                    time.sleep(RETRY_DELAY)
                else:
                    logger.error(f"Rate limited (429) after {retries} retries, giving up on {url}")
                    return None
            else:
                return response
                
        except Exception as e:
            logger.error(f"Error making request to {url}: {e}")
            if attempt < retries:
                time.sleep(RETRY_DELAY)
            else:
                return None
    
    return None

def get_all_movie_urls(username: str, max_pages: int = 20) -> List[str]:
    """
    Get all movie URLs for a user by traversing pagination
    Returns a list of all film URLs found.
    """
    profile_slug = f"{username}/films/by/date/"
    base_url = DOMAIN + profile_slug
    
    all_film_urls = []
    current_page = 1
    current_url = base_url
    
    while current_page <= max_pages:
        logger.info(f"Fetching page {current_page}: {current_url}")
        
        try:
            response = make_request(current_url)
            if not response or response.status_code != 200:
                logger.error(f"Error retrieving URL: {current_url}, status: {response.status_code if response else 'None'}")
                break
                
            soup = BeautifulSoup(response.content, "html.parser")
            film_list = soup.find('ul', class_='poster-list')
            
            if film_list is None:
                logger.error(f"No film list found at: {current_url}")
                break
                
            # Extract film URLs from this page
            films = film_list.find_all('li')
            page_urls = []
            
            logger.info(f"Found {len(films)} films on page {current_page}")
            
            for film in films:
                film_div = film.find('div')
                if film_div:
                    film_card = film_div.get('data-target-link')
                    if film_card:
                        page_urls.append(DOMAIN + film_card.lstrip('/'))
            
            # Add URLs from this page
            all_film_urls.extend(page_urls)
            logger.info(f"Added {len(page_urls)} film URLs from page {current_page}")
            
            # Look for next page link
            pagination = soup.find('div', class_='pagination')
            if pagination:
                next_link = pagination.find('a', class_='next')
                if next_link and 'href' in next_link.attrs:
                    next_page = next_link['href']
                    current_url = DOMAIN + next_page.lstrip('/')
                    current_page += 1
                else:
                    # No more pages
                    logger.info("No more pagination links found, reached end of list")
                    break
            else:
                # No pagination found
                logger.info("No pagination found, single page of results")
                break
                
            if not page_urls:
                # No films found on this page
                logger.info("No films found on this page, stopping pagination")
                break
                
        except Exception as e:
            logger.error(f"Error processing page {current_page}: {e}")
            break
    
    logger.info(f"Retrieved {len(all_film_urls)} total film URLs across {current_page} page(s)")
    return all_film_urls

def get_user_ratings(username: str) -> Dict[str, str]:
    """
    Extracts user ratings from the profile pages, handling pagination
    Returns a dictionary mapping film URL to rating
    """
    ratings = {}
    profile_slug = f"{username}/films/by/date/"
    base_url = DOMAIN + profile_slug
    
    current_page = 1
    current_url = base_url
    max_pages = 20  # Reasonable limit to prevent infinite loops
    
    while current_page <= max_pages:
        logger.info(f"Fetching ratings from page {current_page}: {current_url}")
        
        try:
            response = make_request(current_url)
            if not response or response.status_code != 200:
                logger.error(f"Error retrieving ratings page: {current_url}")
                break
                
            soup = BeautifulSoup(response.content, "html.parser")
            
            # Find all film items on the page
            film_items = soup.select('li.poster-container')
            
            for item in film_items:
                try:
                    # Get the film URL
                    film_link = item.select_one('div[data-target-link]')
                    if film_link:
                        film_url = film_link.get('data-target-link')
                        if film_url:
                            film_url = DOMAIN + film_url.lstrip('/')
                            
                            # Get the rating if available
                            rating_span = item.select_one('span.rating')
                            rating = rating_span.get_text(strip=True) if rating_span else None
                            
                            if rating:
                                ratings[film_url] = rating
                except Exception as e:
                    logger.error(f"Error processing rating for film: {e}")
            
            logger.info(f"Found {len(ratings)} ratings so far")
            
            # Check for next page
            pagination = soup.find('div', class_='pagination')
            if pagination:
                next_link = pagination.find('a', class_='next')
                if next_link and 'href' in next_link.attrs:
                    next_page = next_link['href']
                    current_url = DOMAIN + next_page.lstrip('/')
                    current_page += 1
                else:
                    break
            else:
                break
                
        except Exception as e:
            logger.error(f"Error processing ratings page {current_page}: {e}")
            break
    
    logger.info(f"Retrieved {len(ratings)} total ratings across {current_page} page(s)")
    return ratings

def get_movie_poster_url(movie_soup: BeautifulSoup) -> Optional[str]:
    """
    Extract the poster URL from the movie page's JSON-LD script.
    """
    script_tag = movie_soup.select_one('script[type="application/ld+json"]')
    if script_tag is None:
        return None
    try:
        json_text = script_tag.text.strip()
        if json_text.startswith("/*"):
            # Remove comment markers if present
            json_text = json_text.split("*/", 1)[-1].strip()
            json_text = json_text.split("/*", 1)[0].strip()
        json_obj = json.loads(json_text)
        return json_obj.get('image', None)
    except Exception as e:
        logger.error(f"Error parsing JSON from movie page: {e}")
        return None

def get_movie_title(movie_soup: BeautifulSoup) -> str:
    """
    Extract the movie title from the page's og:title meta tag.
    """
    meta_tag = movie_soup.select_one('meta[property="og:title"]')
    if meta_tag:
        return meta_tag.get("content", "").strip()
    return "Unknown Title"

def get_release_year(movie_soup: BeautifulSoup) -> Optional[str]:
    """
    Extract the movie release year.
    """
    try:
        # Try to get from headline with year
        title_section = movie_soup.find('h2', class_='headline-2')
        if title_section:
            year_element = title_section.find('small')
            if year_element:
                return year_element.get_text(strip=True).strip('()')
        
        # Try to get from schema data
        script_tag = movie_soup.select_one('script[type="application/ld+json"]')
        if script_tag:
            try:
                json_text = script_tag.text.strip()
                if json_text.startswith("/*"):
                    # Remove comment markers if present
                    json_text = json_text.split("*/", 1)[-1].strip()
                    json_text = json_text.split("/*", 1)[0].strip()
                json_obj = json.loads(json_text)
                if 'datePublished' in json_obj:
                    return json_obj['datePublished'][:4]  # Get just the year
            except:
                pass
                
        return None
    except Exception as e:
        logger.error(f"Error extracting release year: {e}")
        return None

def get_movie_director(movie_soup: BeautifulSoup) -> List[str]:
    """
    Extract the director(s) from the movie page.
    """
    credits = movie_soup.find("p", class_="credits")
    if credits:
        director_span = credits.find("span", class_="directorlist")
        if director_span:
            director_tags = director_span.find_all("a")
            directors = [tag.get_text(strip=True) for tag in director_tags]
            if directors:
                return directors
    return ["Unknown Director"]

def get_movie_review(film_id: str, username: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Extract the user's review of the movie if available.
    Returns a tuple of (review_text, review_date, review_url)
    """
    try:
        review_text = None
        review_date = None
        review_url = f"{DOMAIN}{username}/film/{film_id}/"
        
        # Make request to the user's review page
        logger.info(f"Checking for review at {review_url}")
        review_response = make_request(review_url)
        
        if review_response and review_response.status_code == 200:
            review_soup = BeautifulSoup(review_response.content, "html.parser")
            review_div = review_soup.find('div', class_='js-review-body')
            if review_div:
                review_text = review_div.get_text(strip=True)
                
                # Try to get review date
                date_meta = review_soup.find('meta', property='og:article:published_time')
                if date_meta and date_meta.get('content'):
                    review_date = date_meta.get('content').split('T')[0]
                
                return review_text, review_date, review_url
        
        # No review found - return empty values but still a tuple
        return None, None, review_url
    
    except Exception as e:
        logger.error(f"Error processing review: {e}")
        # Always return a tuple with three elements to avoid unpacking errors
        return None, None, None

def process_movie_data(movie_url: str, username: str, rating: Optional[str] = None) -> Dict[str, Any]:
    """
    Process a single movie URL to extract all relevant data
    """
    logger.info(f"Processing movie: {movie_url}")
    
    try:
        response = make_request(movie_url)
        if not response or response.status_code != 200:
            logger.error(f"Error retrieving film page: {movie_url}, status: {response.status_code if response else 'None'}")
            return None
            
        film_id = movie_url.split('/')[-2]

        movie_soup = BeautifulSoup(response.content, "html.parser")
        title = get_movie_title(movie_soup)
        poster_url = get_movie_poster_url(movie_soup)
        director = get_movie_director(movie_soup)
        release_year = get_release_year(movie_soup)
        
        # Get review with better error handling
        try:
            review_text, review_date, review_url = get_movie_review(film_id, username)
        except Exception as e:
            logger.error(f"Error getting review for {title}, continuing with empty values: {str(e)}")
            review_text, review_date, review_url = None, None, None
        
        logger.info(f"Processed movie: {title} ({release_year if release_year else 'Unknown Year'}) directed by {director}")
        if review_text:
            logger.info(f"Found review from {review_date if review_date else 'unknown date'}: {review_text[:50]}...")
        
        return {
            'title': title,
            'letterboxd_url': movie_url,
            'poster_url': poster_url,
            'rating': rating,
            'director': director,
            'review': review_text,
            'release_year': release_year,
            'review_date': review_date,
            'review_url': review_url
        }
    except Exception as e:
        logger.error(f"Error processing movie {movie_url}: {str(e)}")
        return None

def get_all_movies(username: str, batch_size: int = BATCH_SIZE, existing_movies: List[Dict[str, Any]] = None, max_movies: int = MAX_MOVIES_PER_REQUEST) -> List[Dict[str, Any]]:
    """
    Get all movies for a user, including their ratings, processing in batches.
    If existing_movies is provided, only fetch and process new movies.
    """
    logger.info(f"Retrieving all movies for {username}")
    
    # Initialize with existing movies if provided
    movies = existing_movies or []
    existing_count = len(movies)
    existing_urls = {movie["letterboxd_url"] for movie in movies} if movies else set()
    logger.info(f"Starting with {existing_count} existing movies")
    
    # Get all movie URLs
    movie_urls = get_all_movie_urls(username)
    logger.info(f"Found {len(movie_urls)} total movies on Letterboxd")
    
    # Filter out movies we already have
    new_movie_urls = [url for url in movie_urls if url not in existing_urls]
    logger.info(f"Found {len(new_movie_urls)} new movies to process")
    
    # Limit the number of new movies to process per request
    if len(new_movie_urls) > max_movies:
        new_movie_urls = new_movie_urls[:max_movies]
        logger.info(f"Limiting to {max_movies} movies for this request to prevent timeout")
    
    # If no new movies, return existing ones
    if not new_movie_urls:
        logger.info("No new movies found, keeping existing movie data")
        return movies
    
    # Get all ratings
    ratings = get_user_ratings(username)
    
    # Process movies in batches to avoid rate limiting
    for i in range(0, len(new_movie_urls), batch_size):
        batch = new_movie_urls[i:i+batch_size]
        logger.info(f"Processing batch {i//batch_size + 1}/{(len(new_movie_urls) + batch_size - 1)//batch_size} ({len(batch)} movies)")
        
        for url in batch:
            rating = ratings.get(url)
            movie_data = process_movie_data(url, username, rating)
            if movie_data:
                movies.append(movie_data)
                
        # Add a delay between batches
        if i + batch_size < len(new_movie_urls):
            delay = SEARCHING_DELAY
            logger.info(f"Waiting {delay} seconds before processing next batch")
            time.sleep(delay)
    
    # Mark as complete
    if movies:
        cache_item = {
            'username': username,
            'movies': movies,
            'last_updated': int(time.time()),
            'is_complete': True
        }
        table.put_item(Item=cache_item)
    
    logger.info(f"Processed {len(movies)} total movies for {username} ({len(movies) - existing_count} new)")
    return movies

def get_movies(search: MoviesSearch) -> List[MovieResult]:
    """
    Retrieve (and cache) all movies for the given username.
    Returns a list of MovieResult objects.
    """
    username = search.username
    fast_mode = search.fast_mode
    now = int(time.time())
    
    logger.info(f"Retrieving movies for: {username} (fast_mode: {fast_mode})")
    
    # Check for a cached item
    cached_item = table.get_item(Key={'username': username}).get('Item')
    if cached_item:
        last_updated = cached_item.get('last_updated', 0)
        cached_movies = cached_item.get('movies', [])
        is_complete = cached_item.get('is_complete', True)
        
        logger.info(f"Found cached data with {len(cached_movies)} movies, age: {now - last_updated}s, complete: {is_complete}")

        # If fast mode, always return cached data
        if fast_mode:
            logger.info(f"Fast mode: returning {len(cached_movies)} cached movies")
            return [MovieResult(**movie) for movie in cached_movies]

        # If cache is recent, return it directly
        if now - last_updated < TTL:
            logger.info(f"Cache hit, returning {len(cached_movies)} movies")
            return [MovieResult(**movie) for movie in cached_movies]
            
        # If cache exists but is stale, we'll do a smart update
        logger.info(f"Cache is stale, performing smart update")
        movies_to_cache = get_all_movies(username, existing_movies=cached_movies)
    else:
        # Fast mode with no cache - return empty
        if fast_mode:
            logger.info("Fast mode: no cached data found, returning empty list")
            return []
            
        # No cache exists, fetch all movies
        logger.info(f"No cached data found, fetching all movies")
        movies_to_cache = get_all_movies(username)
    
    # No need to update cache here, as it's done in get_all_movies
    
    logger.info(f"Returning {len(movies_to_cache)} movies")
    return [MovieResult(**movie) for movie in movies_to_cache]

def backfill_movies(username: str, force: bool = False) -> Dict[str, Any]:
    """
    Force a complete refresh of all movies for a user.
    
    Parameters:
    - username: Letterboxd username to backfill
    - force: If True, delete existing data before backfilling
    
    Returns a dict with:
    - success: True/False
    - message: Status message
    - count: Number of movies processed
    """
    logger.info(f"Starting backfill for user: {username}")
    
    try:
        # Check if user already exists in database
        existing_item = table.get_item(Key={'username': username}).get('Item')
        
        if existing_item and not force:
            # User exists and force=False
            movie_count = len(existing_item.get('movies', []))
            logger.info(f"User {username} already exists with {movie_count} movies. Use force=True to override.")
            return {
                'success': False,
                'message': f"User {username} already exists with {movie_count} movies. Use force=True to override.",
                'count': movie_count
            }
        
        if existing_item and force:
            # User exists but we're forcing a refresh - delete the existing item
            logger.info(f"Forcing refresh for {username}, deleting existing record")
            table.delete_item(Key={'username': username})
        
        # Perform a full fetch of all movies
        logger.info(f"Fetching all movies for {username}")
        movies = get_all_movies(username)
        movie_count = len(movies)
        
        logger.info(f"Backfill complete for {username}: {movie_count} movies processed")
        return {
            'success': True,
            'message': f"Successfully backfilled {movie_count} movies for {username}",
            'count': movie_count
        }
        
    except Exception as e:
        error_msg = f"Error during backfill for {username}: {str(e)}"
        logger.error(error_msg)
        return {
            'success': False,
            'message': error_msg,
            'count': 0
        }
