import time
import json
import boto3
import requests
from typing import Optional, List, Tuple
from bs4 import BeautifulSoup
from schemas.movies import MoviesSearch, MovieResult

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table("movies")

# Base URL for Letterboxd
DOMAIN = "https://letterboxd.com/"

# 6 hours for TTL
TTL = 21600


def get_movies(search: MoviesSearch) -> List[MovieResult]:
    """
    Retrieve the four most recent movies for the given username.
    First, check the DynamoDB cache. If the cached data is fresh (TTL not exceeded)
    or the most recent movie hasn’t changed, return the cached data.
    Otherwise, scrape fresh data, update the cache, and return it.
    """
    username = search.username
    now = int(time.time())

    
    cached_item = table.get_item(Key={'username': username}).get('Item')
    if cached_item:
        last_updated = cached_item.get('last_updated', 0)
        cached_movies = cached_item.get('movies', [])

        
        if now - last_updated < TTL:
            return [MovieResult(**movie) for movie in cached_movies]

        
        fresh_first = get_latest_movie(username)
        if fresh_first and cached_movies:
            
            if cached_movies[0]['letterboxd_url'] == fresh_first[1]:
                
                table.update_item(
                    Key={'username': username},
                    UpdateExpression="SET last_updated = :now",
                    ExpressionAttributeValues={":now": now}
                )
                return [MovieResult(**movie) for movie in cached_movies]

    
    movie_info = get_movies_process(username, 4)
    movies_to_cache = [
        {'title': title, 'letterboxd_url': letterboxd_url, 'poster_url': poster_url}
        for title, letterboxd_url, poster_url in movie_info
    ]
    
    cache_item = {
        'username': username,
        'movies': movies_to_cache,
        'last_updated': now
    }
    table.put_item(Item=cache_item)

    return [MovieResult(**movie) for movie in movies_to_cache]


def get_latest_movie(username: str) -> Optional[Tuple[str, str, Optional[str]]]:
    """
    Quickly fetch only the most recent movie from the user's Letterboxd profile.
    Returns a tuple of (title, movie_url, poster_url) or None if an error occurs.
    """
    profile_slug = f"{username}/films/by/date/"
    profile_url = DOMAIN + profile_slug
    movie_urls = get_n_movies(profile_url, 1)
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


def get_n_movies(profile_url: str, n: int) -> List[str]:
    """
    Scrape the given profile URL until n film URLs are found.
    Returns a list of full URLs.
    """
    film_url_list = []
    while len(film_url_list) < n:
        response = requests.get(profile_url)
        if response.status_code != 200:
            print("Error retrieving URL:", profile_url)
            break

        profile_soup = BeautifulSoup(response.content, "html.parser")
        film_list = profile_soup.find('ul', class_='poster-list')
        if film_list is None:
            print("No film list found at:", profile_url)
            break

        films = film_list.find_all('li')
        for film in films:
            film_div = film.find('div')
            if film_div:
                film_card = film_div.get('data-target-link')
                if film_card:
                    film_url_list.append(DOMAIN + film_card)
                    if len(film_url_list) == n:
                        return film_url_list

        next_button = profile_soup.find('a', class_='next')
        if next_button is None:
            break
        profile_url = DOMAIN + next_button['href']

    return film_url_list


def get_movie_poster_url(movie_soup: BeautifulSoup) -> Optional[str]:
    """
    Extract the poster URL from the movie page's JSON-LD script tag.
    """
    script_tag = movie_soup.select_one('script[type="application/ld+json"]')
    if script_tag is None:
        return None
    try:
        json_text = script_tag.text.strip()
        # Some pages wrap the JSON in comments; remove them.
        if json_text.startswith("/*"):
            json_text = json_text.split("*/", 1)[-1].strip()
            json_text = json_text.split("/*", 1)[0].strip()
        json_obj = json.loads(json_text)
        return json_obj.get('image', None)
    except Exception as e:
        print("Error parsing JSON from movie page:", e)
        return None


def get_movie_title(movie_soup: BeautifulSoup) -> str:
    """
    Extract the movie title from the page's Open Graph meta tag.
    """
    meta_tag = movie_soup.select_one('meta[property="og:title"]')
    if meta_tag:
        return meta_tag.get("content", "").strip()
    return "Unknown Title"


def get_movies_process(username: str, n: int) -> List[tuple]:
    """
    Scrape the user's profile for n movies and return a list of tuples.
    Each tuple contains (title, letterboxd_url, poster_url).
    """
    profile_slug = f"{username}/films/by/date/"
    profile_url = DOMAIN + profile_slug

    movie_urls = get_n_movies(profile_url, n)
    movies = []
    for movie_url in movie_urls:
        response = requests.get(movie_url)
        if response.status_code != 200:
            print("Error retrieving film page:", movie_url)
            continue
        movie_soup = BeautifulSoup(response.content, "html.parser")
        title = get_movie_title(movie_soup)
        poster_url = get_movie_poster_url(movie_soup)
        movies.append((title, movie_url, poster_url))
    return movies
