import time
import json
import gzip
import boto3
import logging
import random
import re
import os
import threading
from datetime import datetime
from email.utils import parsedate_to_datetime
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, List, Tuple, Dict, Any, Callable
from xml.etree import ElementTree as ET
from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests
from schemas.movies import MoviesSearch, MovieResult


# Get a logger for this module
logger = logging.getLogger(__name__)

movies_table_name = os.environ.get("MOVIES_TABLE_NAME", "movies")
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(movies_table_name)

# DynamoDB items are capped at 400 KB, so large movie lists are offloaded to S3:
# the object holds the gzipped movies JSON and the DynamoDB item keeps the
# metadata plus a pointer (movies_s3_key) to it.
movies_cache_bucket_name = os.environ.get("MOVIES_CACHE_BUCKET_NAME")
S3_OFFLOAD_THRESHOLD_BYTES = int(os.environ.get("MOVIES_S3_OFFLOAD_THRESHOLD_BYTES", "300000"))
MOVIES_S3_KEY_ATTR = "movies_s3_key"
s3 = boto3.client("s3")


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
MAX_MOVIES_PER_REQUEST = 5000  # Limit movies processed per API call
RSS_DIRECTOR_ENRICH_LIMIT = 15
LETTERBOXD_NS = "https://letterboxd.com"
WATCHED_DATE_PATTERN = re.compile(r"^Watched on (?P<date>.+)\.$")
WATCHED_SHORT_DATE_PATTERN = re.compile(r"^Watched (?P<date>\d{1,2} [A-Za-z]{3} \d{4})$")
REVIEWED_ON_PATTERN = re.compile(r"^Reviewed on (?P<date>.+)$")
BROWSER_IMPERSONATIONS = ("chrome124", "chrome123", "chrome120")
MAX_LISTING_PAGES = 20  # Reasonable limit to prevent infinite pagination loops
# Number of film pages scraped concurrently. Keep this small: Letterboxd rate limits aggressively.
SCRAPE_CONCURRENCY = max(1, int(os.environ.get("MOVIES_SCRAPE_CONCURRENCY", "5")))
# Persist partial results every N batches so a timed-out job can resume instead of starting over.
CHECKPOINT_EVERY_BATCHES = 10

# Shared worker pool; its long-lived threads each keep one HTTP session (see _get_session).
_executor = ThreadPoolExecutor(max_workers=SCRAPE_CONCURRENCY, thread_name_prefix="movies-scrape")
_thread_local = threading.local()


def _get_session() -> Any:
    """
    Per-thread HTTP session so connections (and TLS handshakes) to Letterboxd are reused.
    """
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = curl_requests.Session()
        _thread_local.session = session
    return session


def is_block_page(body: Any) -> bool:
    if isinstance(body, str):
        return "Just a moment..." in body and "challenge-platform" in body
    return b"Just a moment..." in body and b"challenge-platform" in body

def _extract_listing_entries(page_soup: BeautifulSoup) -> Tuple[int, List[Tuple[str, Optional[str]]]]:
    """
    Extract (film_url, rating) pairs from a Letterboxd listing page in a single pass.
    Returns the number of poster items seen and the de-duplicated entries.
    Matches on the poster link attributes rather than wrapper classes, since
    Letterboxd renames those (poster-container -> griditem, ...) fairly often.
    """
    nodes = page_soup.select("li [data-target-link], li [data-item-link]")
    entries: List[Tuple[str, Optional[str]]] = []
    seen = set()
    for node in nodes:
        target_link = node.get("data-target-link") or node.get("data-item-link")
        if not target_link:
            continue
        movie_url = DOMAIN + target_link.lstrip("/")
        if "/film/" not in movie_url or movie_url in seen:
            continue
        seen.add(movie_url)
        item = node.find_parent("li")
        rating_span = item.select_one("span.rating") if item else None
        rating = rating_span.get_text(strip=True) if rating_span else None
        entries.append((movie_url, rating or None))

    return len(nodes), entries

def _build_films_page_url(username: str, page_number: int) -> str:
    """
    Build Letterboxd diary page URLs using the stable page pattern:
    /<username>/films/by/date/page/<n>/
    """
    base_url = f"{DOMAIN}{username}/films/by/date/"
    if page_number <= 1:
        return base_url
    return f"{base_url}page/{page_number}/"

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
    try:
        response = make_request(movie_url, retries=0)
        if response is None or response.status_code != 200:
            return {}
        return _parse_film_page(response.content)
    except Exception as exc:
        logger.warning("Metadata enrichment failed for %s: %s", movie_url, exc)
        return {}

def _is_unknown_director_value(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    cleaned = [str(item).strip() for item in value]
    return cleaned == ["Unknown Director"]

def _extract_movies_from_rss(username: str, max_movies: Optional[int] = MAX_MOVIES_PER_REQUEST) -> List[Dict[str, Any]]:
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

    items = channel.findall("item")
    if max_movies and max_movies > 0:
        items = items[:max_movies]

    # Enrich the newest entries with film page metadata, fetching the pages concurrently.
    film_urls = [
        _normalize_review_url_to_film_url(username, item.findtext("link", default=""))
        for item in items
    ]
    enrich_urls = [url for url in film_urls[:RSS_DIRECTOR_ENRICH_LIMIT] if url]
    metadata_by_url: Dict[str, Dict[str, Any]] = dict(
        zip(enrich_urls, _executor.map(_fetch_movie_page_metadata, enrich_urls))
    )

    movies: List[Dict[str, Any]] = []
    for index, item in enumerate(items):
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

        film_url = film_urls[index]
        directors = ["Unknown Director"]
        metadata = metadata_by_url.get(film_url)
        if metadata:
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

def _movies_s3_key(username: str) -> str:
    return f"movies/{username}.json.gz"

def _get_cached_item(username: str, include_movies: bool = True) -> Optional[Dict[str, Any]]:
    """
    Read movie cache from DynamoDB (following the S3 pointer for offloaded
    movie lists), returning None if storage is unavailable.
    Pass include_movies=False to skip the S3 read when only metadata is needed.
    """
    try:
        item = table.get_item(Key={'username': username}).get('Item')
        if not item:
            return None

        s3_key = item.get(MOVIES_S3_KEY_ATTR)
        if s3_key and include_movies:
            if not movies_cache_bucket_name:
                raise RuntimeError("MOVIES_CACHE_BUCKET_NAME is not configured but the cache is offloaded to S3")
            body = s3.get_object(Bucket=movies_cache_bucket_name, Key=s3_key)["Body"].read()
            item["movies"] = json.loads(gzip.decompress(body))
        return item
    except Exception as exc:
        logger.warning("Movie cache read failed for %s: %s", username, exc)
        return None

def _put_cached_item(cache_item: Dict[str, Any]) -> None:
    """
    Best-effort cache write; failures should not break API responses.
    Movie lists too large for a DynamoDB item are written to S3 instead.
    """
    username = cache_item.get('username')
    try:
        item = dict(cache_item)
        movies = item.get("movies") or []
        item["movie_count"] = len(movies)
        payload = json.dumps(movies, separators=(",", ":"), default=str).encode("utf-8")

        if len(payload) > S3_OFFLOAD_THRESHOLD_BYTES:
            if movies_cache_bucket_name:
                s3_key = _movies_s3_key(username)
                s3.put_object(
                    Bucket=movies_cache_bucket_name,
                    Key=s3_key,
                    Body=gzip.compress(payload),
                    ContentType="application/gzip",
                )
                item.pop("movies", None)
                item[MOVIES_S3_KEY_ATTR] = s3_key
                logger.info("Offloaded %s movies (%s bytes) for %s to S3", len(movies), len(payload), username)
            else:
                logger.warning(
                    "Movie list for %s is %s bytes and MOVIES_CACHE_BUCKET_NAME is not set; "
                    "the DynamoDB write will fail above 400 KB",
                    username,
                    len(payload),
                )

        table.put_item(Item=item)
    except Exception as exc:
        logger.warning("Movie cache write failed for %s: %s", username, exc)

def _delete_cached_item(username: str) -> None:
    """
    Best-effort cache delete for force backfills.
    """
    try:
        table.delete_item(Key={'username': username})
        if movies_cache_bucket_name:
            s3.delete_object(Bucket=movies_cache_bucket_name, Key=_movies_s3_key(username))
    except Exception as exc:
        logger.warning("Movie cache delete failed for %s: %s", username, exc)

def get_cached_movies(username: str) -> List[Dict[str, Any]]:
    """
    Return the cached movie list for a username (empty if there is no cache).
    """
    cached_item = _get_cached_item(username)
    return (cached_item or {}).get("movies", [])

def get_cached_movies_state(username: str, include_movies: bool = True) -> Dict[str, Any]:
    """
    Return cache metadata for a username without triggering scraping.
    With include_movies=False the "movies" list is left empty (no S3 read).
    """
    now = int(time.time())
    cached_item = _get_cached_item(username, include_movies=include_movies)
    if not cached_item:
        return {
            "exists": False,
            "is_stale": True,
            "is_complete": False,
            "last_updated": None,
            "movies": [],
        }

    last_updated = cached_item.get("last_updated", 0)
    cached_movies = cached_item.get("movies", [])
    is_complete = bool(cached_item.get("is_complete", True))
    is_stale = (now - last_updated) >= TTL or not is_complete
    return {
        "exists": True,
        "is_stale": is_stale,
        "is_complete": is_complete,
        "last_updated": last_updated,
        "movies": cached_movies,
    }

def make_request(url: str, retries: int = MAX_RETRIES) -> Optional[Any]:
    """
    Make a request over a reused session, retrying (with a different browser
    profile) on rate limits, 403s, Cloudflare challenges, and network errors.
    Returns None if no usable response could be obtained.
    """
    session = _get_session()
    for attempt in range(retries + 1):
        is_last_attempt = attempt >= retries
        impersonation = BROWSER_IMPERSONATIONS[attempt % len(BROWSER_IMPERSONATIONS)]
        try:
            response = session.get(
                url,
                headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer": DOMAIN,
                },
                timeout=REQUEST_TIMEOUT,
                impersonate=impersonation,
            )
        except Exception as e:
            logger.error(f"Error making request to {url}: {e}")
            if is_last_attempt:
                return None
        else:
            if is_block_page(response.content):
                if is_last_attempt:
                    logger.error("Cloudflare challenge persisted for %s after retries", url)
                    return None
                logger.warning(
                    "Cloudflare challenge page returned for %s using %s, retrying",
                    url,
                    impersonation,
                )
            elif response.status_code == 429:
                if is_last_attempt:
                    logger.error(f"Rate limited (429) after {retries} retries, giving up on {url}")
                    return None
                logger.warning(f"Rate limited (429), waiting {RETRY_DELAY} seconds before retry {attempt+1}/{retries}")
            elif response.status_code == 403 and not is_last_attempt:
                logger.warning(
                    "Received 403 for %s using %s, retrying with another browser profile",
                    url,
                    impersonation,
                )
            else:
                return response

        # Back off (with jitter) before the next attempt.
        time.sleep(RETRY_DELAY + random.uniform(MIN_DELAY, MAX_DELAY))

    return None

def get_film_listing(username: str, max_pages: int = MAX_LISTING_PAGES) -> Tuple[List[str], Dict[str, str]]:
    """
    Walk the user's paginated film listing once, collecting both the film URLs
    (newest first) and the user's ratings.
    Returns (film_urls, ratings) where ratings maps film URL -> rating text.
    """
    all_film_urls: List[str] = []
    seen_urls = set()
    ratings: Dict[str, str] = {}
    pages_fetched = 0

    for current_page in range(1, max_pages + 1):
        current_url = _build_films_page_url(username, current_page)
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

            pages_fetched += 1
            soup = BeautifulSoup(response.content, "html.parser")
            item_count, entries = _extract_listing_entries(soup)
            logger.info(f"Found {item_count} poster item(s) on page {current_page}")

            if not entries:
                if item_count:
                    logger.warning(
                        "Found poster items but no movie links on page %s (%s). Markup may have changed.",
                        current_page,
                        current_url,
                    )
                logger.info("No films found on this page, stopping pagination")
                break

            new_on_page = 0
            for movie_url, rating in entries:
                if rating:
                    ratings[movie_url] = rating
                if movie_url not in seen_urls:
                    seen_urls.add(movie_url)
                    all_film_urls.append(movie_url)
                    new_on_page += 1
            logger.info(
                "Added %s film URLs from page %s (%s duplicate(s) skipped)",
                new_on_page,
                current_page,
                len(entries) - new_on_page,
            )

            if current_page > 1 and not new_on_page:
                logger.info("No new film URLs found on page %s, stopping pagination", current_page)
                break

        except Exception as e:
            logger.error(f"Error processing page {current_page}: {e}")
            break

    logger.info(
        "Retrieved %s film URLs and %s ratings across %s page(s)",
        len(all_film_urls),
        len(ratings),
        pages_fetched,
    )
    return all_film_urls, ratings

def get_movie_poster_url(movie_soup: BeautifulSoup, schema: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """
    Extract the poster URL from the movie page's JSON-LD script.
    Pass an already-parsed schema to avoid re-parsing the JSON-LD.
    """
    json_obj = schema if schema is not None else _get_movie_schema(movie_soup)
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

def get_release_year(movie_soup: BeautifulSoup, schema: Optional[Dict[str, Any]] = None) -> Optional[str]:
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
        if schema is None:
            schema = _get_movie_schema(movie_soup)
        return _extract_release_year_from_schema(schema)
    except Exception as e:
        logger.error(f"Error extracting release year: {e}")
        return None

def get_movie_director(movie_soup: BeautifulSoup, schema: Optional[Dict[str, Any]] = None) -> List[str]:
    """
    Extract the director(s) from the movie page.
    """
    if schema is None:
        schema = _get_movie_schema(movie_soup)
    schema_directors = _extract_directors_from_schema(schema)
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

def _parse_film_page(content: Any) -> Dict[str, Any]:
    """
    Parse a film page once (HTML and JSON-LD) and extract all metadata from it.
    """
    movie_soup = BeautifulSoup(content, "html.parser")
    schema = _get_movie_schema(movie_soup)
    return {
        "title": get_movie_title(movie_soup),
        "poster_url": get_movie_poster_url(movie_soup, schema),
        "director": get_movie_director(movie_soup, schema),
        "release_year": get_release_year(movie_soup, schema),
    }

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

def process_movie_data(movie_url: str, username: str, rating: Optional[str] = None) -> Optional[Dict[str, Any]]:
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
        film = _parse_film_page(response.content)
        title = film["title"]

        # Get review with better error handling
        try:
            review_text, review_date, review_url = get_movie_review(film_id, username)
        except Exception as e:
            logger.error(f"Error getting review for {title}, continuing with empty values: {str(e)}")
            review_text, review_date, review_url = None, None, None

        logger.info(
            "Processed movie: %s (%s) directed by %s",
            title,
            film["release_year"] or "Unknown Year",
            film["director"],
        )
        if review_text:
            logger.info(f"Found review from {review_date if review_date else 'unknown date'}: {review_text[:50]}...")

        return {
            'title': title,
            'letterboxd_url': movie_url,
            'poster_url': film["poster_url"],
            'rating': rating,
            'director': film["director"],
            'review': review_text,
            'release_year': film["release_year"],
            'review_date': review_date,
            'review_url': review_url
        }
    except Exception as e:
        logger.error(f"Error processing movie {movie_url}: {str(e)}")
        return None

def _order_by_listing(movies: List[Dict[str, Any]], movie_urls: List[str]) -> List[Dict[str, Any]]:
    """
    Order movies to match the Letterboxd listing (newest first). Movies that are
    no longer in the listing keep their relative order at the end.
    """
    position = {url: index for index, url in enumerate(movie_urls)}
    return sorted(movies, key=lambda movie: position.get(movie.get("letterboxd_url"), len(position)))

def _save_movies(username: str, movies: List[Dict[str, Any]], is_complete: bool) -> None:
    _put_cached_item({
        'username': username,
        'movies': movies,
        'last_updated': int(time.time()),
        'is_complete': is_complete
    })

def get_all_movies(
    username: str,
    batch_size: int = BATCH_SIZE,
    existing_movies: List[Dict[str, Any]] = None,
    max_movies: Optional[int] = MAX_MOVIES_PER_REQUEST,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> List[Dict[str, Any]]:
    """
    Get all movies for a user, including their ratings, processing in batches.
    If existing_movies is provided, only fetch and process new movies.
    """
    logger.info(f"Retrieving all movies for {username}")

    def report(phase: str, message: str, processed: Optional[int] = None, total: Optional[int] = None) -> None:
        if not progress_callback:
            return
        progress: Dict[str, Any] = {"phase": phase, "message": message}
        if processed is not None:
            progress["processed_movies"] = processed
            progress["total_movies"] = total
        progress_callback(progress)

    # Initialize with existing movies if provided
    movies = list(existing_movies or [])
    existing_count = len(movies)
    existing_urls = {movie.get("letterboxd_url") for movie in movies}
    logger.info(f"Starting with {existing_count} existing movies")

    report("discovering_urls", "Discovering movie URLs from Letterboxd")

    # One pass over the listing pages yields both the film URLs and the ratings
    movie_urls, ratings = get_film_listing(username)
    logger.info(f"Found {len(movie_urls)} total movies on Letterboxd")

    if not movie_urls:
        rss_movies = _extract_movies_from_rss(username, max_movies=max_movies)
        if rss_movies:
            logger.info("Using RSS fallback movie data for %s", username)
            merged_movies = _merge_rss_with_cached(rss_movies, movies)
            _save_movies(username, merged_movies, is_complete=False)
            report("completed", "Completed using RSS fallback", len(rss_movies), len(rss_movies))
            return merged_movies

        # Nothing could be fetched at all; leave the cache untouched.
        logger.info("No movies could be retrieved, keeping existing movie data")
        report("completed", "No new movies found", 0, 0)
        return movies

    # Ratings come for free with the listing, so keep cached movies' ratings current
    for movie in movies:
        latest_rating = ratings.get(movie.get("letterboxd_url"))
        if latest_rating and movie.get("rating") != latest_rating:
            movie["rating"] = latest_rating

    # Filter out movies we already have
    new_movie_urls = [url for url in movie_urls if url not in existing_urls]
    logger.info(f"Found {len(new_movie_urls)} new movies to process")

    # Limit the number of new movies to process per request
    truncated = bool(max_movies and max_movies > 0 and len(new_movie_urls) > max_movies)
    if truncated:
        new_movie_urls = new_movie_urls[:max_movies]
        logger.info(f"Limiting to {max_movies} movies for this request to prevent timeout")

    # If no new movies, just record that the cache is fresh again
    if not new_movie_urls:
        logger.info("No new movies found, keeping existing movie data")
        movies = _order_by_listing(movies, movie_urls)
        if movies:
            _save_movies(username, movies, is_complete=True)
        report("completed", "No new movies found", 0, 0)
        return movies

    total_to_process = len(new_movie_urls)
    processed_movies = 0
    report("processing_movies", "Processing movie pages", processed_movies, total_to_process)

    # Process movies in batches; each batch is scraped concurrently
    batch_size = max(1, batch_size)
    total_batches = (total_to_process + batch_size - 1) // batch_size
    for batch_number, i in enumerate(range(0, total_to_process, batch_size), start=1):
        batch = new_movie_urls[i:i+batch_size]
        logger.info(f"Processing batch {batch_number}/{total_batches} ({len(batch)} movies)")

        results = _executor.map(lambda url: process_movie_data(url, username, ratings.get(url)), batch)
        for movie_data in results:
            if movie_data:
                movies.append(movie_data)
            processed_movies += 1
            report("processing_movies", "Processing movie pages", processed_movies, total_to_process)

        if batch_number < total_batches:
            # Checkpoint so an interrupted run resumes from here on the next refresh
            if batch_number % CHECKPOINT_EVERY_BATCHES == 0:
                _save_movies(username, _order_by_listing(movies, movie_urls), is_complete=False)

            # Add a delay between batches to avoid rate limiting
            logger.info(f"Waiting {SEARCHING_DELAY} seconds before processing next batch")
            time.sleep(SEARCHING_DELAY)

    # Newest first, matching the Letterboxd listing
    movies = _order_by_listing(movies, movie_urls)
    if movies:
        _save_movies(username, movies, is_complete=not truncated)

    logger.info(f"Processed {len(movies)} total movies for {username} ({len(movies) - existing_count} new)")
    report("completed", "Movie refresh complete", total_to_process, total_to_process)
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

        # If cache is recent and complete, return it directly
        if is_complete and (now - last_updated < TTL):
            logger.info(f"Cache hit, returning {len(cached_movies)} movies")
            return [MovieResult(**movie) for movie in cached_movies]
            
        if not is_complete:
            logger.info("Cache is incomplete, refreshing regardless of TTL")
        else:
            logger.info("Cache is stale, performing smart update")

        # If cache exists but is stale/incomplete, we'll do a smart update
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

def backfill_movies(
    username: str,
    force: bool = False,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
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
            # Keep existing and perform a full incremental refresh.
            existing_movies = existing_item.get("movies", [])
            logger.info(
                "User %s already exists with %s movies. Running incremental refresh.",
                username,
                len(existing_movies),
            )
            movies = get_all_movies(
                username,
                existing_movies=existing_movies,
                max_movies=0,
                progress_callback=progress_callback,
            )
            movie_count = len(movies)
            return {
                'success': True,
                'message': f"Successfully refreshed {movie_count} movies for {username}",
                'count': movie_count
            }
        
        if existing_item and force:
            # User exists but we're forcing a refresh - delete the existing item
            logger.info(f"Forcing refresh for {username}, deleting existing record")
            _delete_cached_item(username)
        
        # Perform a full fetch of all movies
        logger.info(f"Fetching all movies for {username}")
        movies = get_all_movies(username, max_movies=0, progress_callback=progress_callback)
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
