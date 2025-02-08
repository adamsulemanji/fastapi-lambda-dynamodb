import os
import uuid
import boto3
from typing import Optional, List
from schemas.movies import MoviesSearch, MovieResult
from fastapi import HTTPException
from bs4 import BeautifulSoup
import requests
import json

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table("movies")
DOMAIN = "https://letterboxd.com/"


def get_movies(search: MoviesSearch) -> List[MovieResult]:
    username = search.username
    movie_info = get_movies_process(username, 4)

    return [MovieResult(title=title, letterboxd_url=letterboxd_url, poster_url=poster_url) for title, letterboxd_url, poster_url in movie_info]


def get_n_movies(profile_url: str, n: int) -> List[str]:
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
            film_card = film.find('div').get('data-target-link')
            film_url_list.append(DOMAIN + film_card)
            if len(film_url_list) == n:
                return film_url_list

        next_button = profile_soup.find('a', class_='next')
        if next_button is None:
            break
        profile_url = DOMAIN + next_button['href']

    return film_url_list


def get_movie_poster_url(movie_soup: BeautifulSoup) -> Optional[str]:
    script_tag = movie_soup.select_one('script[type="application/ld+json"]')
    if script_tag is None:
        return None
    try:
        json_text = script_tag.text.strip()
        if json_text.startswith("/*"):
            json_text = json_text.split("*/", 1)[-1].strip()
            json_text = json_text.split("/*", 1)[0].strip()
        json_obj = json.loads(json_text)
        return json_obj.get('image', None)
    except Exception as e:
        print("Error parsing JSON from movie page:", e)
        return None


def get_movie_title(movie_soup: BeautifulSoup) -> str:
    meta_tag = movie_soup.select_one('meta[property="og:title"]')
    if meta_tag:
        return meta_tag.get("content", "").strip()
    return "Unknown Title"


def get_movies_process(username: str, n: int) -> List[tuple]:
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
