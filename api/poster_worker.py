import colorsys
import io
import json
import logging
import math
import os
from typing import Any, Dict, List

import boto3
import requests
from PIL import Image, ImageOps

from services.movie_poster_jobs import (
    increment_movie_poster_job_progress,
    mark_movie_poster_job_failed,
    mark_movie_poster_job_succeeded,
    update_movie_poster_job,
)
from services.movies import backfill_movies, get_cached_movies


logger = logging.getLogger(__name__)

s3 = boto3.client("s3")

poster_temp_bucket_name = os.environ.get("MOVIES_POSTER_TEMP_BUCKET_NAME")
poster_output_bucket_name = os.environ.get("MOVIES_POSTER_OUTPUT_BUCKET_NAME")

REQUEST_TIMEOUT = 20
TILE_WIDTH = 200
TILE_HEIGHT = 300


try:
    RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:
    RESAMPLE = Image.LANCZOS


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _best_grid(available_tiles: int) -> Dict[str, int]:
    """
    Pick a poster-shaped grid (close to square in tile counts, so roughly 2:3
    overall) that uses as many tiles as possible. Some tiles may be left out
    so the grid is always completely filled, e.g. 214 tiles -> 15x14 (210 used).
    """
    if available_tiles <= 0:
        return {"columns": 1, "rows": 1}

    best_columns, best_rows = 1, 1
    for rows in range(1, math.isqrt(available_tiles) + 1):
        # Allow the grid to be at most about a third wider than it is tall.
        max_columns = rows + max(1, rows // 3)
        columns = min(available_tiles // rows, max_columns)
        if columns < rows:
            continue
        # ">=" prefers the squarer grid (more rows) when two grids use the same number of tiles.
        if columns * rows >= best_columns * best_rows:
            best_columns, best_rows = columns, rows

    return {"columns": best_columns, "rows": best_rows}


def build_manifest_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    if not poster_temp_bucket_name:
        raise RuntimeError("MOVIES_POSTER_TEMP_BUCKET_NAME is not configured")

    job_id = event["job_id"]
    username = event["username"]
    movie_count = _safe_int(event.get("movie_count"), 0)
    bootstrap_movies = bool(event.get("bootstrap_movies", False))

    if bootstrap_movies or movie_count == 0:
        update_movie_poster_job(
            job_id,
            {
                "status": "IN_PROGRESS",
                "progress_phase": "syncing_movies",
                "progress_message": "Syncing movies before poster generation",
            },
        )
        sync_result = backfill_movies(username=username, force=False)
        if not sync_result.get("success"):
            raise ValueError(sync_result.get("message", "Failed to sync movies before poster generation"))

    movies = get_cached_movies(username)

    dedupe = set()
    manifest_movies: List[Dict[str, Any]] = []
    for source_index, movie in enumerate(movies):
        poster_url = movie.get("poster_url")
        if not poster_url:
            continue

        dedupe_key = movie.get("letterboxd_url") or poster_url
        if dedupe_key in dedupe:
            continue
        dedupe.add(dedupe_key)

        manifest_movies.append(
            {
                "source_index": source_index,
                "title": movie.get("title"),
                "poster_url": poster_url,
                "letterboxd_url": movie.get("letterboxd_url"),
            }
        )

    if movie_count > 0:
        manifest_movies = manifest_movies[:movie_count]

    total_items = len(manifest_movies)
    if total_items == 0:
        raise ValueError(f"No poster URLs available for user {username}")

    manifest_key = f"jobs/{job_id}/manifest.json"
    s3.put_object(
        Bucket=poster_temp_bucket_name,
        Key=manifest_key,
        Body=json.dumps(
            {
                "job_id": job_id,
                "username": username,
                "movie_count": movie_count,
                "movies": manifest_movies,
            }
        ).encode("utf-8"),
        ContentType="application/json",
    )

    update_movie_poster_job(
        job_id,
        {
            "status": "IN_PROGRESS",
            "progress_phase": "downloading",
            "progress_message": f"Preparing {total_items} posters",
            "processed_items": 0,
            "total_items": total_items,
        },
    )

    return {
        "job_id": job_id,
        "username": username,
        "movie_count": movie_count,
        "bootstrap_movies": bootstrap_movies,
        "manifest_key": manifest_key,
        "total_items": total_items,
        "movies": manifest_movies,
    }


def process_poster_item_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    if not poster_temp_bucket_name:
        raise RuntimeError("MOVIES_POSTER_TEMP_BUCKET_NAME is not configured")

    job_id = event["job_id"]
    total_items = _safe_int(event.get("total_items"), 0)
    movie = event.get("movie", {})

    source_index = _safe_int(movie.get("source_index"), 0)
    poster_url = movie.get("poster_url")

    result: Dict[str, Any] = {
        "source_index": source_index,
        "title": movie.get("title"),
        "letterboxd_url": movie.get("letterboxd_url"),
        "skipped": True,
    }

    try:
        if not poster_url:
            raise ValueError("Missing poster URL")

        response = requests.get(poster_url, timeout=REQUEST_TIMEOUT)
        if response.status_code != 200:
            raise ValueError(f"Poster fetch failed with status {response.status_code}")

        with Image.open(io.BytesIO(response.content)) as raw_image:
            image = raw_image.convert("RGB")
            tile = ImageOps.fit(image, (TILE_WIDTH, TILE_HEIGHT), method=RESAMPLE)

        dominant_color = tile.resize((1, 1), resample=RESAMPLE).getpixel((0, 0))
        r, g, b = dominant_color
        hue, sat, val = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)

        tile_key = f"jobs/{job_id}/tiles/{source_index}.jpg"
        buffer = io.BytesIO()
        tile.save(buffer, format="JPEG", quality=88, optimize=True)
        buffer.seek(0)
        s3.put_object(
            Bucket=poster_temp_bucket_name,
            Key=tile_key,
            Body=buffer.getvalue(),
            ContentType="image/jpeg",
        )

        result.update(
            {
                "tile_key": tile_key,
                "h": hue,
                "s": sat,
                "v": val,
                "skipped": False,
            }
        )
    except Exception as exc:
        logger.warning("Skipping poster item for job %s (index=%s): %s", job_id, source_index, exc)
        result["error"] = str(exc)
    finally:
        increment_movie_poster_job_progress(
            job_id=job_id,
            processed_delta=1,
            total_items=total_items if total_items > 0 else None,
            progress_phase="downloading",
            progress_message="Downloading and analyzing posters",
        )

    return result


def render_poster_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    if not poster_temp_bucket_name:
        raise RuntimeError("MOVIES_POSTER_TEMP_BUCKET_NAME is not configured")
    if not poster_output_bucket_name:
        raise RuntimeError("MOVIES_POSTER_OUTPUT_BUCKET_NAME is not configured")

    job_id = event["job_id"]
    processed_items = event.get("processed_items", [])
    if not isinstance(processed_items, list):
        processed_items = []

    valid_items = [
        item
        for item in processed_items
        if isinstance(item, dict) and not item.get("skipped") and item.get("tile_key")
    ]
    if not valid_items:
        raise ValueError("No valid posters available to render")

    # Choose a fully filled, poster-shaped grid; drop the oldest movies that don't fit.
    layout = _best_grid(len(valid_items))
    columns = layout["columns"]
    rows = layout["rows"]
    movie_tiles = columns * rows
    dropped_tiles = len(valid_items) - movie_tiles
    blank_tiles = 0

    # source_index follows the cached movie order (newest first), so keep the lowest indexes.
    kept_items = sorted(valid_items, key=lambda item: _safe_int(item.get("source_index"), 0))[:movie_tiles]

    sorted_items = sorted(
        kept_items,
        key=lambda item: (
            float(item.get("h", 0.0)),
            float(item.get("s", 0.0)),
            float(item.get("v", 0.0)),
            _safe_int(item.get("source_index"), 0),
        ),
    )

    canvas = Image.new("RGB", (columns * TILE_WIDTH, rows * TILE_HEIGHT), color=(14, 14, 18))

    for position, item in enumerate(sorted_items):
        tile_key = item["tile_key"]
        tile_obj = s3.get_object(Bucket=poster_temp_bucket_name, Key=tile_key)
        tile_bytes = tile_obj["Body"].read()
        with Image.open(io.BytesIO(tile_bytes)) as tile_img:
            tile = tile_img.convert("RGB")

        x = (position % columns) * TILE_WIDTH
        y = (position // columns) * TILE_HEIGHT
        canvas.paste(tile, (x, y))

    output_key = f"posters/{job_id}.png"
    output_buffer = io.BytesIO()
    canvas.save(output_buffer, format="PNG", optimize=True)
    output_buffer.seek(0)

    s3.put_object(
        Bucket=poster_output_bucket_name,
        Key=output_key,
        Body=output_buffer.getvalue(),
        ContentType="image/png",
        CacheControl="public,max-age=31536000,immutable",
    )

    mark_movie_poster_job_succeeded(
        job_id=job_id,
        output_key=output_key,
        result_count=movie_tiles,
        blank_tiles=blank_tiles,
        grid_columns=columns,
        grid_rows=rows,
        progress_message=f"Poster generated with {movie_tiles} posters ({columns}x{rows}, dropped={dropped_tiles})",
    )

    return {
        "job_id": job_id,
        "output_key": output_key,
        "result_count": movie_tiles,
        "blank_tiles": blank_tiles,
        "dropped_tiles": dropped_tiles,
        "grid_columns": columns,
        "grid_rows": rows,
    }


def fail_poster_job_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    job_id = event.get("job_id")
    if not job_id:
        raise ValueError("Missing job_id for failure handler")

    error = event.get("error", "WorkflowError")
    cause = event.get("cause", "Poster workflow failed")
    error_message = f"{error}: {cause}"
    mark_movie_poster_job_failed(job_id=job_id, error_message=error_message[:1000])

    return {
        "job_id": job_id,
        "status": "FAILED",
    }
