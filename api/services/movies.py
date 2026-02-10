import time
import json
import boto3
import requests
import logging
import random
import re
import asyncio
import aiohttp
from datetime import datetime
from email.utils import parsedate_to_datetime
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, List, Tuple, Dict, Any
from xml.etree import ElementTree as ET
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
RSS_DIRECTOR_ENRICH_LIMIT = 15
LETTERBOXD_NS = "https://letterboxd.com"
WATCHED_DATE_PATTERN = re.compile(r"^Watched on (?P<date>.+)\.$")
WATCHED_SHORT_DATE_PATTERN = re.compile(r"^Watched (?P<date>\d{1,2} [A-Za-z]{3} \d{4})$")
REVIEWED_ON_PATTERN = re.compile(r"^Reviewed on (?P<date>.+)$")

def _extract_movie_urls_from_page(page_soup: BeautifulSoup) -> List[str]:
    """
    Extract all movie links from a Letterboxd listing page.
    Uses attribute selectors so minor DOM wrapper changes don't break parsing.
    """
    urls: List[str] = []
    seen = set()
    selectors = [
        "ul.poster-list li div[data-target-link]",
        "li.poster-container div[data-target-link]",
    ]

    for selector in selectors:
        for node in page_soup.select(selector):
            target_link = node.get("data-target-link")
            if not target_link:
                continue
            movie_url = DOMAIN + target_link.lstrip("/")
            if "/film/" not in movie_url or movie_url in seen:
                continue
            seen.add(movie_url)
            urls.append(movie_url)

        if urls:
            break

    return urls

def _get_next_page_url(page_soup: BeautifulSoup) -> Optional[str]:
    """
    Return the absolute URL for the next pagination page, if present.
    """
    next_link = page_soup.select_one("div.pagination a.next[href], a.next[href]")
    if not next_link:
        return None

    href = next_link.get("href")
    if not href:
        return None

    return DOMAIN + href.lstrip("/")

def _normalize_review_url_to_film_url(username: str, url: str) -> str:
    """
    Convert a user review URL (/username/film/<slug>/) to a canonical film URL.
    """
    user_review_prefix = f"{DOMAIN}{username}/film/"
    if url.startswith(user_review_prefix):
        slug = url[len(user_review_prefix):].strip("/")
        if slug:
            return f"{DOMAIN}film/{slug}/"
    return url

def _clean_json_ld_text(raw_text: str) -> str:
    """
    Remove common comment wrappers around JSON-LD content.
    """
    json_text = (raw_text or "").strip()
    if json_text.startswith("/*"):
        json_text = json_text.split("*/", 1)[-1].strip()
        json_text = json_text.split("/*", 1)[0].strip()
    return json_text

def _get_movie_schema(movie_soup: BeautifulSoup) -> Optional[Dict[str, Any]]:
    """
    Parse the film page JSON-LD payload.
    """
    script_tag = movie_soup.find("script", attrs={"type": "application/ld+json"})
    if script_tag is None:
        return None

    try:
        json_text = _clean_json_ld_text(script_tag.text)
        parsed = json.loads(json_text)
        if isinstance(parsed, dict):
            return parsed
    except Exception as exc:
        logger.error("Error parsing movie JSON-LD: %s", exc)
    return None

def _extract_directors_from_schema(schema: Optional[Dict[str, Any]]) -> List[str]:
    """
    Extract director names from JSON-LD schema.
    """
    if not schema:
        return []

    director_data = schema.get("director")
    if not director_data:
        return []

    if not isinstance(director_data, list):
        director_data = [director_data]

    directors: List[str] = []
    for person in director_data:
        if isinstance(person, dict):
            name = person.get("name")
        elif isinstance(person, str):
            name = person.strip()
        else:
            name = None
        if name and name not in directors:
            directors.append(name)

    return directors

def _extract_release_year_from_schema(schema: Optional[Dict[str, Any]]) -> Optional[str]:
    """
    Extract release year from available JSON-LD date fields.
    """
    if not schema:
        return None

    for field_name in ("datePublished", "dateCreated"):
        field_value = schema.get(field_name)
        if isinstance(field_value, str) and len(field_value) >= 4 and field_value[:4].isdigit():
            return field_value[:4]

    released_event = schema.get("releasedEvent")
    if isinstance(released_event, dict):
        released_event = [released_event]

    if isinstance(released_event, list):
        for event in released_event:
            if not isinstance(event, dict):
                continue
            start_date = event.get("startDate")
            if isinstance(start_date, str) and len(start_date) >= 4 and start_date[:4].isdigit():
                return start_date[:4]

    return None

def _parse_rss_title(raw_title: str) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Parse RSS title text into movie title, release year, and rating.
    Example: "Marty Supreme, 2025 - ★★★★"
    """
    title_text = (raw_title or "").strip()
    rating: Optional[str] = None

    if " - " in title_text:
        maybe_title, maybe_rating = title_text.rsplit(" - ", 1)
        if any(symbol in maybe_rating for symbol in ("★", "½")):
            title_text = maybe_title.strip()
            rating = maybe_rating.strip()

    release_year: Optional[str] = None
    year_match = re.search(r",\s*(\d{4})$", title_text)
    if year_match:
        release_year = year_match.group(1)
        title_text = title_text[:year_match.start()].strip()

    return title_text or "Unknown Title", release_year, rating

def _parse_review_date_from_watched_text(text: str) -> Optional[str]:
    """
    Parse "Watched on Monday December 15, 2025." into ISO date.
    """
    match = WATCHED_DATE_PATTERN.match((text or "").strip())
    if not match:
        return None

    raw_date = match.group("date")
    try:
        return datetime.strptime(raw_date, "%A %B %d, %Y").strftime("%Y-%m-%d")
    except ValueError:
        return None

def _parse_date_text_to_iso(date_text: str) -> Optional[str]:
    """
    Parse multiple date formats encountered on Letterboxd pages/feeds.
    """
    value = (date_text or "").strip().rstrip(".")
    if not value:
        return None

    for fmt in ("%A %B %d, %Y", "%d %b %Y", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None

def _extract_review_date_from_text(text: str) -> Optional[str]:
    """
    Extract review/watch date from common textual patterns.
    """
    normalized = (text or "").strip()
    if not normalized:
        return None

    watched_long = _parse_review_date_from_watched_text(normalized)
    if watched_long:
        return watched_long

    watched_short = WATCHED_SHORT_DATE_PATTERN.match(normalized)
    if watched_short:
        return _parse_date_text_to_iso(watched_short.group("date"))

    reviewed_on = REVIEWED_ON_PATTERN.match(normalized)
    if reviewed_on:
        return _parse_date_text_to_iso(reviewed_on.group("date"))

    return None

def _extract_review_date_from_rss_item(item: ET.Element, description_soup: BeautifulSoup) -> Optional[str]:
    """
    Pull review/watch date from structured RSS fields first, then description text.
    """
    watched_date = item.findtext(f"{{{LETTERBOXD_NS}}}watchedDate")
    if watched_date:
        return watched_date.strip()

    pub_date = item.findtext("pubDate")
    if pub_date:
        try:
            return parsedate_to_datetime(pub_date).date().isoformat()
        except Exception:
            parsed_pub = _parse_date_text_to_iso(pub_date)
            if parsed_pub:
                return parsed_pub

    for paragraph in description_soup.find_all("p"):
        date_from_text = _extract_review_date_from_text(paragraph.get_text(" ", strip=True))
        if date_from_text:
            return date_from_text

    return None

def _fetch_movie_page_metadata(movie_url: str) -> Dict[str, Any]:
    """
    Best-effort film page fetch for director/year enrichment.
    Uses retries=0 to avoid long request stalls during fallback mode.
    """
    response = make_request(movie_url, retries=0)
    if response is None or response.status_code != 200:
        return {}

    movie_soup = BeautifulSoup(response.content, "html.parser")
    return {
        "director": get_movie_director(movie_soup),
        "release_year": get_release_year(movie_soup),
        "poster_url": get_movie_poster_url(movie_soup),
    }

def _is_unknown_director_value(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    cleaned = [str(item).strip() for item in value]
    return cleaned == ["Unknown Director"]

def _extract_movies_from_rss(username: str, max_movies: int = MAX_MOVIES_PER_REQUEST) -> List[Dict[str, Any]]:
    """
    Fallback parser for Letterboxd RSS feed when HTML pages are blocked.
    """
    rss_url = f"{DOMAIN}{username}/rss/"
    logger.info("Attempting RSS fallback for %s using %s", username, rss_url)
    response = make_request(rss_url)
    if response is None:
        logger.error("RSS fallback failed for %s: no HTTP response", username)
        return []

    if response.status_code != 200:
        logger.error("RSS fallback failed for %s: status %s", username, response.status_code)
        return []

    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as exc:
        logger.error("RSS fallback parse failed for %s: %s", username, exc)
        return []

    channel = root.find("channel")
    if channel is None:
        return []

    movies: List[Dict[str, Any]] = []
    for index, item in enumerate(channel.findall("item")[:max_movies]):
        raw_title = item.findtext("title", default="")
        review_url = item.findtext("link", default="")
        description_html = item.findtext("description", default="")
        parsed_title, parsed_release_year, rating = _parse_rss_title(raw_title)

        description_soup = BeautifulSoup(description_html, "html.parser")
        poster_tag = description_soup.find("img")
        poster_url = poster_tag.get("src") if poster_tag else None

        review_date = _extract_review_date_from_rss_item(item, description_soup)
        release_year = item.findtext(f"{{{LETTERBOXD_NS}}}filmYear") or parsed_release_year

        review_text = None
        for paragraph in description_soup.find_all("p"):
            text = paragraph.get_text(" ", strip=True)
            if not text:
                continue
            parsed_date = _extract_review_date_from_text(text)
            if parsed_date and review_date is None:
                review_date = parsed_date
                continue
            if review_text is None:
                review_text = text

        film_url = _normalize_review_url_to_film_url(username, review_url)
        directors = ["Unknown Director"]
        if film_url and index < RSS_DIRECTOR_ENRICH_LIMIT:
            metadata = _fetch_movie_page_metadata(film_url)
            metadata_directors = metadata.get("director")
            if metadata_directors and not _is_unknown_director_value(metadata_directors):
                directors = metadata_directors
            if not release_year and metadata.get("release_year"):
                release_year = metadata["release_year"]
            if not poster_url and metadata.get("poster_url"):
                poster_url = metadata["poster_url"]

        movies.append({
            "title": parsed_title,
            "letterboxd_url": film_url or review_url,
            "poster_url": poster_url,
            "rating": rating,
            "director": directors,
            "review": review_text,
            "release_year": release_year,
            "review_date": review_date,
            "review_url": review_url or None,
        })

    logger.info("RSS fallback returned %s movie(s) for %s", len(movies), username)
    return movies

def _merge_rss_with_cached(rss_movies: List[Dict[str, Any]], cached_movies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Keep RSS ordering for newest entries and preserve extra fields from cache.
    """
    cached_by_url: Dict[str, Dict[str, Any]] = {}
    for movie in cached_movies:
        movie_url = movie.get("letterboxd_url")
        if movie_url:
            cached_by_url[movie_url] = dict(movie)

    merged: List[Dict[str, Any]] = []
    for rss_movie in rss_movies:
        movie_url = rss_movie.get("letterboxd_url")
        base = cached_by_url.pop(movie_url, {}) if movie_url else {}
        merged_movie = dict(base)
        for key, value in rss_movie.items():
            if key == "director" and _is_unknown_director_value(value):
                existing_director = merged_movie.get("director")
                if existing_director and not _is_unknown_director_value(existing_director):
                    continue
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            if isinstance(value, list) and not value:
                continue
            merged_movie[key] = value
        merged.append(merged_movie)

    merged.extend(cached_by_url.values())
    return merged

def _get_cached_item(username: str) -> Optional[Dict[str, Any]]:
    """
    Read movie cache from DynamoDB, returning None if storage is unavailable.
    """
    try:
        return table.get_item(Key={'username': username}).get('Item')
    except Exception as exc:
        logger.warning("DynamoDB cache read failed for %s: %s", username, exc)
        return None

def _put_cached_item(cache_item: Dict[str, Any]) -> None:
    """
    Best-effort cache write; failures should not break API responses.
    """
    try:
        table.put_item(Item=cache_item)
    except Exception as exc:
        logger.warning("DynamoDB cache write failed for %s: %s", cache_item.get('username'), exc)

def _delete_cached_item(username: str) -> None:
    """
    Best-effort cache delete for force backfills.
    """
    try:
        table.delete_item(Key={'username': username})
    except Exception as exc:
        logger.warning("DynamoDB cache delete failed for %s: %s", username, exc)

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
            if response is None:
                logger.error("Error retrieving URL: %s, no HTTP response", current_url)
                break
            if response.status_code != 200:
                logger.error("Error retrieving URL: %s, status: %s", current_url, response.status_code)
                if response.status_code == 403:
                    logger.warning("Letterboxd blocked HTML scraping (403). RSS fallback will be used if available.")
                break
                
            soup = BeautifulSoup(response.content, "html.parser")
            page_urls = _extract_movie_urls_from_page(soup)

            poster_items = soup.select("ul.poster-list li, li.poster-container")
            logger.info(f"Found {len(poster_items)} poster item(s) on page {current_page}")
            if poster_items and not page_urls:
                logger.warning(
                    "Found poster items but no movie links on page %s (%s). Markup may have changed.",
                    current_page,
                    current_url,
                )
            
            # Add URLs from this page
            all_film_urls.extend(page_urls)
            logger.info(f"Added {len(page_urls)} film URLs from page {current_page}")
            
            # Look for next page link
            next_url = _get_next_page_url(soup)
            if next_url:
                current_url = next_url
                current_page += 1
            else:
                logger.info("No more pagination links found, reached end of list")
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
            if response is None:
                logger.error("Error retrieving ratings page: %s, no HTTP response", current_url)
                break
            if response.status_code != 200:
                logger.error("Error retrieving ratings page: %s, status: %s", current_url, response.status_code)
                break
                
            soup = BeautifulSoup(response.content, "html.parser")
            
            # Find all film items on the page
            film_items = soup.select('li.poster-container') or soup.select('ul.poster-list li')
            
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
            next_url = _get_next_page_url(soup)
            if next_url:
                current_url = next_url
                current_page += 1
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
    json_obj = _get_movie_schema(movie_soup)
    if json_obj is None:
        return None
    return json_obj.get("image", None)

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
        
        # Try JSON-LD release fields
        schema_year = _extract_release_year_from_schema(_get_movie_schema(movie_soup))
        if schema_year:
            return schema_year
                
        return None
    except Exception as e:
        logger.error(f"Error extracting release year: {e}")
        return None

def get_movie_director(movie_soup: BeautifulSoup) -> List[str]:
    """
    Extract the director(s) from the movie page.
    """
    schema_directors = _extract_directors_from_schema(_get_movie_schema(movie_soup))
    if schema_directors:
        return schema_directors

    credits = movie_soup.find("p", class_="credits")
    if credits:
        director_span = credits.find("span", class_="directorlist")
        if director_span:
            director_tags = director_span.find_all("a")
            directors = [tag.get_text(strip=True) for tag in director_tags]
            if directors:
                return directors

    # Newer layouts can omit the legacy credits classes but keep /director/ links.
    director_links = movie_soup.select("a[href*='/director/']")
    directors = []
    for link in director_links:
        director_name = link.get_text(strip=True)
        if director_name and director_name not in directors:
            directors.append(director_name)
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
        
        if review_response is not None and review_response.status_code == 200:
            review_soup = BeautifulSoup(review_response.content, "html.parser")
            review_div = review_soup.find('div', class_='js-review-body')
            if review_div:
                review_text = review_div.get_text(strip=True)
                
                # Try to get review date
                date_meta = review_soup.find('meta', property='og:article:published_time')
                if date_meta and date_meta.get('content'):
                    review_date = date_meta.get('content').split('T')[0]
                else:
                    for paragraph in review_soup.find_all("p"):
                        parsed_date = _extract_review_date_from_text(paragraph.get_text(" ", strip=True))
                        if parsed_date:
                            review_date = parsed_date
                            break
                
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
        if response is None:
            logger.error("Error retrieving film page: %s, no HTTP response", movie_url)
            return None
        if response.status_code != 200:
            logger.error("Error retrieving film page: %s, status: %s", movie_url, response.status_code)
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

    if not movie_urls:
        rss_movies = _extract_movies_from_rss(username, max_movies=max_movies)
        if rss_movies:
            logger.info("Using RSS fallback movie data for %s", username)
            merged_movies = _merge_rss_with_cached(rss_movies, movies)
            cache_item = {
                'username': username,
                'movies': merged_movies,
                'last_updated': int(time.time()),
                'is_complete': False
            }
            _put_cached_item(cache_item)
            return merged_movies
    
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
        _put_cached_item(cache_item)
    
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
    cached_item = _get_cached_item(username)
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
        existing_item = _get_cached_item(username)
        
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
            _delete_cached_item(username)
        
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
