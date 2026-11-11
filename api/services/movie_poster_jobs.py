import json
import logging
import os
import time
import uuid
from decimal import Decimal
from typing import Any, Dict, Optional

import boto3
from boto3.dynamodb.conditions import Key

from services.movies import get_cached_movies_state

logger = logging.getLogger(__name__)

poster_jobs_table_name = os.environ.get("MOVIES_POSTER_JOBS_TABLE_NAME", "movies-poster-jobs")
poster_jobs_username_index = os.environ.get("MOVIES_POSTER_JOBS_USERNAME_INDEX", "username-created_at-index")
poster_state_machine_arn = os.environ.get("MOVIES_POSTER_STATE_MACHINE_ARN")
poster_output_bucket_name = os.environ.get("MOVIES_POSTER_OUTPUT_BUCKET_NAME")
poster_url_expiration_seconds = int(os.environ.get("MOVIES_POSTER_URL_EXPIRATION_SECONDS", "604800"))

dynamodb = boto3.resource("dynamodb")
sfn_client = boto3.client("stepfunctions")
s3 = boto3.client("s3")
jobs_table = dynamodb.Table(poster_jobs_table_name)

ACTIVE_STATUSES = {"QUEUED", "IN_PROGRESS"}
TERMINAL_STATUSES = {"SUCCEEDED", "FAILED"}
JOB_TTL_SECONDS = 60 * 60 * 24 * 14


ACTIVE_JOB_MAX_AGE_SECONDS = 35 * 60


def _now_epoch() -> int:
    return int(time.time())


def _to_json_compatible(value: Any) -> Any:
    if isinstance(value, list):
        return [_to_json_compatible(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_json_compatible(item) for key, item in value.items()}
    if isinstance(value, Decimal):
        if value % 1 == 0:
            return int(value)
        return float(value)
    return value


def _build_output_url(output_key: Optional[str]) -> Optional[str]:
    if not output_key or not poster_output_bucket_name:
        return None
    try:
        return s3.generate_presigned_url(
            ClientMethod="get_object",
            Params={
                "Bucket": poster_output_bucket_name,
                "Key": output_key,
            },
            ExpiresIn=poster_url_expiration_seconds,
        )
    except Exception as exc:
        logger.warning("Failed to generate presigned URL for %s: %s", output_key, exc)
        return None


def _public_job(job_item: Dict[str, Any], existing_job: bool = False) -> Dict[str, Any]:
    item = _to_json_compatible(job_item)
    output_key = item.get("output_key")
    return {
        "job_id": item["job_id"],
        "username": item["username"],
        "status": item.get("status", "QUEUED"),
        "movie_count": int(item.get("movie_count", 0)),
        "existing_job": existing_job,
        "created_at": item.get("created_at", 0),
        "updated_at": item.get("updated_at", 0),
        "started_at": item.get("started_at"),
        "completed_at": item.get("completed_at"),
        "processed_items": item.get("processed_items"),
        "total_items": item.get("total_items"),
        "blank_tiles": item.get("blank_tiles"),
        "grid_columns": item.get("grid_columns"),
        "grid_rows": item.get("grid_rows"),
        "result_count": item.get("result_count"),
        "output_key": output_key,
        "output_url": _build_output_url(output_key),
        "progress_phase": item.get("progress_phase"),
        "progress_message": item.get("progress_message"),
        "error_message": item.get("error_message"),
    }


def _update_job(job_id: str, attributes: Dict[str, Any]) -> None:
    updates = dict(attributes)
    if "updated_at" not in updates:
        updates["updated_at"] = _now_epoch()

    expression_names: Dict[str, str] = {}
    expression_values: Dict[str, Any] = {}
    update_parts = []

    index = 0
    for key, value in updates.items():
        if value is None:
            continue
        name_key = f"#k{index}"
        value_key = f":v{index}"
        expression_names[name_key] = key
        expression_values[value_key] = value
        update_parts.append(f"{name_key} = {value_key}")
        index += 1

    if not update_parts:
        return

    jobs_table.update_item(
        Key={"job_id": job_id},
        UpdateExpression="SET " + ", ".join(update_parts),
        ExpressionAttributeNames=expression_names,
        ExpressionAttributeValues=expression_values,
    )


def _find_active_job_for_username(username: str) -> Optional[Dict[str, Any]]:
    try:
        response = jobs_table.query(
            IndexName=poster_jobs_username_index,
            KeyConditionExpression=Key("username").eq(username),
            ScanIndexForward=False,
            Limit=10,
        )
    except Exception as exc:
        logger.warning("Poster active-job lookup failed for %s: %s", username, exc)
        return None

    # The workflow times out after 30 minutes, so an "active" job older than
    # that died without reporting back and must not block new jobs.
    stale_before = _now_epoch() - ACTIVE_JOB_MAX_AGE_SECONDS
    for item in response.get("Items", []):
        if item.get("status") in ACTIVE_STATUSES and int(item.get("created_at", 0)) >= stale_before:
            return item
    return None


def create_movie_poster_job(username: str, movie_count: int = 0) -> Dict[str, Any]:
    if not poster_state_machine_arn:
        raise RuntimeError("MOVIES_POSTER_STATE_MACHINE_ARN is not configured")

    active_job = _find_active_job_for_username(username)
    if active_job:
        return _public_job(active_job, existing_job=True)

    now = _now_epoch()
    job_id = str(uuid.uuid4())
    execution_name = f"poster-{job_id}"
    cache_state = get_cached_movies_state(username)
    bootstrap_movies = not bool(cache_state.get("exists", False))
    job_item = {
        "job_id": job_id,
        "username": username,
        "movie_count": int(movie_count),
        "bootstrap_movies": bootstrap_movies,
        "status": "QUEUED",
        "created_at": now,
        "updated_at": now,
        "progress_phase": "queued",
        "progress_message": "Poster job queued",
        "processed_items": 0,
        "total_items": 0,
        "expires_at": now + JOB_TTL_SECONDS,
    }

    jobs_table.put_item(
        Item=job_item,
        ConditionExpression="attribute_not_exists(job_id)",
    )

    state_input = {
        "job_id": job_id,
        "username": username,
        "movie_count": int(movie_count),
        "bootstrap_movies": bootstrap_movies,
    }

    try:
        execution = sfn_client.start_execution(
            stateMachineArn=poster_state_machine_arn,
            name=execution_name,
            input=json.dumps(state_input),
        )
        started_at = _now_epoch()
        execution_arn = execution.get("executionArn")
        _update_job(
            job_id,
            {
                "status": "IN_PROGRESS",
                "started_at": started_at,
                "execution_arn": execution_arn,
                "progress_phase": "starting",
                "progress_message": "Poster workflow started",
            },
        )
        job_item["status"] = "IN_PROGRESS"
        job_item["started_at"] = started_at
        job_item["execution_arn"] = execution_arn
        job_item["progress_phase"] = "starting"
        job_item["progress_message"] = "Poster workflow started"
    except Exception as exc:
        logger.exception("Failed to start poster workflow for job %s: %s", job_id, exc)
        completed_at = _now_epoch()
        _update_job(
            job_id,
            {
                "status": "FAILED",
                "completed_at": completed_at,
                "error_message": f"Failed to start workflow: {exc}",
                "progress_phase": "failed",
                "progress_message": "Poster workflow failed to start",
            },
        )
        job_item["status"] = "FAILED"
        job_item["completed_at"] = completed_at
        job_item["error_message"] = f"Failed to start workflow: {exc}"
        job_item["progress_phase"] = "failed"
        job_item["progress_message"] = "Poster workflow failed to start"

    return _public_job(job_item)


def get_movie_poster_job(job_id: str) -> Optional[Dict[str, Any]]:
    response = jobs_table.get_item(Key={"job_id": job_id})
    item = response.get("Item")
    if not item:
        return None
    return _public_job(item)


def update_movie_poster_job(job_id: str, attributes: Dict[str, Any]) -> None:
    _update_job(job_id, attributes)


def increment_movie_poster_job_progress(
    job_id: str,
    processed_delta: int = 1,
    total_items: Optional[int] = None,
    progress_phase: Optional[str] = None,
    progress_message: Optional[str] = None,
) -> None:
    expression_values: Dict[str, Any] = {
        ":updated_at": _now_epoch(),
        ":delta": int(processed_delta),
    }
    expression_names: Dict[str, str] = {"#updated_at": "updated_at"}
    set_parts = ["#updated_at = :updated_at"]
    add_parts = ["processed_items :delta"]

    if total_items is not None:
        expression_values[":total_items"] = int(total_items)
        expression_names["#total_items"] = "total_items"
        set_parts.append("#total_items = :total_items")
    if progress_phase is not None:
        expression_values[":progress_phase"] = progress_phase
        expression_names["#progress_phase"] = "progress_phase"
        set_parts.append("#progress_phase = :progress_phase")
    if progress_message is not None:
        expression_values[":progress_message"] = progress_message
        expression_names["#progress_message"] = "progress_message"
        set_parts.append("#progress_message = :progress_message")

    update_expression = "SET " + ", ".join(set_parts) + " ADD " + ", ".join(add_parts)
    jobs_table.update_item(
        Key={"job_id": job_id},
        UpdateExpression=update_expression,
        ExpressionAttributeNames=expression_names,
        ExpressionAttributeValues=expression_values,
    )


def mark_movie_poster_job_succeeded(
    job_id: str,
    output_key: str,
    result_count: int,
    blank_tiles: int,
    grid_columns: int,
    grid_rows: int,
    progress_message: str,
) -> None:
    _update_job(
        job_id,
        {
            "status": "SUCCEEDED",
            "completed_at": _now_epoch(),
            "output_key": output_key,
            "result_count": int(result_count),
            "blank_tiles": int(blank_tiles),
            "grid_columns": int(grid_columns),
            "grid_rows": int(grid_rows),
            "progress_phase": "completed",
            "progress_message": progress_message,
        },
    )


def mark_movie_poster_job_failed(job_id: str, error_message: str) -> None:
    _update_job(
        job_id,
        {
            "status": "FAILED",
            "completed_at": _now_epoch(),
            "error_message": error_message,
            "progress_phase": "failed",
            "progress_message": "Poster generation failed",
        },
    )
