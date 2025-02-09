import time
import json
import boto3
import requests
from typing import Optional, List, Tuple
from bs4 import BeautifulSoup
from schemas.movies import MoviesSearch, MovieResult


dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table("movies")


DOMAIN = "https://letterboxd.com/"
TTL = 21600

def get_n_movies(profile_url: str, n: int) -> Tuple[List[str], BeautifulSoup]:
    """
    Scrape the given profile URL until n film URLs are found.
    Returns a tuple (list_of_movie_urls, profile_page_soup).
    """
    film_url_list = []
    response = requests.get(profile_url)
    if response.status_code != 200:
        print("Error retrieving URL:", profile_url)
    profile_soup = BeautifulSoup(response.content, "html.parser")
    film_list = profile_soup.find('ul', class_='poster-list')
    if film_list is None:
        print("No film list found at:", profile_url)
    films = film_list.find_all('li')
    for film in films:
        film_div = film.find('div')
        if film_div:
            film_card = film_div.get('data-target-link')
            if film_card:
                film_url_list.append(DOMAIN + film_card)
                if len(film_url_list) == n:
                    return film_url_list, profile_soup
    return film_url_list, profile_soup

def get_user_rating(profile_soup: BeautifulSoup) -> List[str]:
    """
    Extracts user ratings from the profile page.
    """
    ratings = []
    rating_elements = profile_soup.select('span.rating')
    for rating in rating_elements:
        ratings.append(rating.get_text(strip=True))
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
        print("Error parsing JSON from movie page:", e)
        return None

def get_movie_title(movie_soup: BeautifulSoup) -> str:
    """
    Extract the movie title from the page's og:title meta tag.
    """
    meta_tag = movie_soup.select_one('meta[property="og:title"]')
    if meta_tag:
        return meta_tag.get("content", "").strip()
    return "Unknown Title"

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

def get_movies_process_full(username: str, n: int) -> List[tuple]:
    """
    Scrape the user's Letterboxd profile for n movies and return a list of tuples.
    Each tuple contains (title, letterboxd_url, poster_url, rating, director).
    """
    profile_slug = f"{username}/films/by/date/"
    profile_url = DOMAIN + profile_slug
    movie_urls, profile_soup = get_n_movies(profile_url, n)
    ratings = get_user_rating(profile_soup)
    movies = []
    for i, movie_url in enumerate(movie_urls):
        response = requests.get(movie_url)
        if response.status_code != 200:
            print("Error retrieving film page:", movie_url)
            continue
        movie_soup = BeautifulSoup(response.content, "html.parser")
        title = get_movie_title(movie_soup)
        poster_url = get_movie_poster_url(movie_soup)
        director = get_movie_director(movie_soup)
        rating = ratings[i] if i < len(ratings) else None
        movies.append((title, movie_url, poster_url, rating, director))
    return movies

def get_latest_movie(username: str) -> Optional[Tuple[str, str, Optional[str]]]:
    """
    Quickly fetch only the most recent movie from the user's Letterboxd profile.
    Returns a tuple (title, movie_url, poster_url) or None on error.
    """
    profile_slug = f"{username}/films/by/date/"
    profile_url = DOMAIN + profile_slug
    movie_urls, _ = get_n_movies(profile_url, 1)
    if movie_urls:
        movie_url = movie_urls[0]
        response = requests.get(movie_url)
        if response.status_code != 200:
            print("Error retrieving film page:", movie_url)
            return None
        movie_soup = BeautifulSoup(response.content, "html.parser")
        title = get_movie_title(movie_soup)
        poster_url = get_movie_poster_url(movie_soup)
        return (title, movie_url, poster_url)
    return None

def get_movies(search: MoviesSearch) -> List[MovieResult]:
    """
    Retrieve (and cache) the four most recent movies for the given username.
    Returns a list of MovieResult objects including title, letterboxd_url, poster_url, rating, and director.
    """
    username = search.username
    now = int(time.time())
    
    # Check for a cached item
    cached_item = table.get_item(Key={'username': username}).get('Item')
    if cached_item:
        last_updated = cached_item.get('last_updated', 0)
        cached_movies = cached_item.get('movies', [])
        

        if now - last_updated < TTL:
            return [MovieResult(**movie) for movie in cached_movies]
        
        # If cache is stale, verify if the latest movie has changed
        fresh_first = get_latest_movie(username)
        if fresh_first and cached_movies:
            if cached_movies[0]['letterboxd_url'] == fresh_first[1]:
                # Update only the timestamp if nothing has changed
                table.update_item(
                    Key={'username': username},
                    UpdateExpression="SET last_updated = :now",
                    ExpressionAttributeValues={":now": now}
                )
                return [MovieResult(**movie) for movie in cached_movies]
    
    movie_info = get_movies_process_full(username, 4)
    movies_to_cache = [
        {
            'title': title,
            'letterboxd_url': letterboxd_url,
            'poster_url': poster_url,
            'rating': rating,
            'director': director,
        }
        for title, letterboxd_url, poster_url, rating, director in movie_info
    ]
    
    # Update the cache
    cache_item = {
        'username': username,
        'movies': movies_to_cache,
        'last_updated': now
    }
    table.put_item(Item=cache_item)
    
    return [MovieResult(**movie) for movie in movies_to_cache]
