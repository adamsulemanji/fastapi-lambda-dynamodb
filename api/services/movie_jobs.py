import json
import logging
import os
import time
import uuid
from decimal import Decimal
from typing import Any, Dict, Optional

import boto3
from boto3.dynamodb.conditions import Key

from services.movies import backfill_movies


logger = logging.getLogger(__name__)

jobs_table_name = os.environ.get("MOVIES_JOBS_TABLE_NAME", "movies_jobs")
jobs_username_index = os.environ.get("MOVIES_JOBS_USERNAME_INDEX", "username-created_at-index")
scrape_queue_url = os.environ.get("MOVIES_SCRAPE_QUEUE_URL")

dynamodb = boto3.resource("dynamodb")
sqs = boto3.client("sqs")
jobs_table = dynamodb.Table(jobs_table_name)

ACTIVE_STATUSES = {"PENDING", "IN_PROGRESS"}
TERMINAL_STATUSES = {"SUCCEEDED", "FAILED"}
JOB_TTL_SECONDS = 60 * 60 * 24 * 7


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


def _public_job(job_item: Dict[str, Any], existing_job: bool = False) -> Dict[str, Any]:
    item = _to_json_compatible(job_item)
    return {
        "job_id": item["job_id"],
        "username": item["username"],
        "status": item.get("status", "PENDING"),
        "force": bool(item.get("force", False)),
        "existing_job": existing_job,
        "created_at": item.get("created_at", 0),
        "updated_at": item.get("updated_at", 0),
        "started_at": item.get("started_at"),
        "completed_at": item.get("completed_at"),
        "processed_movies": item.get("processed_movies"),
        "total_movies": item.get("total_movies"),
        "result_count": item.get("result_count"),
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
            IndexName=jobs_username_index,
            KeyConditionExpression=Key("username").eq(username),
            ScanIndexForward=False,
            Limit=10,
        )
    except Exception as exc:
        logger.warning("Active job lookup failed for %s: %s", username, exc)
        return None

    for item in response.get("Items", []):
        status = item.get("status")
        if status in ACTIVE_STATUSES:
            return item
    return None


def create_movies_job(username: str, force: bool = False) -> Dict[str, Any]:
    if not scrape_queue_url:
        raise RuntimeError("MOVIES_SCRAPE_QUEUE_URL is not configured")

    active_job = _find_active_job_for_username(username)
    if active_job:
        return _public_job(active_job, existing_job=True)

    now = _now_epoch()
    job_id = str(uuid.uuid4())
    job_item = {
        "job_id": job_id,
        "username": username,
        "force": bool(force),
        "status": "PENDING",
        "created_at": now,
        "updated_at": now,
        "progress_phase": "queued",
        "progress_message": "Job queued",
        "processed_movies": 0,
        "total_movies": 0,
        "expires_at": now + JOB_TTL_SECONDS,
    }

    jobs_table.put_item(
        Item=job_item,
        ConditionExpression="attribute_not_exists(job_id)",
    )

    payload = {
        "job_id": job_id,
        "username": username,
        "force": bool(force),
    }

    try:
        sqs.send_message(
            QueueUrl=scrape_queue_url,
            MessageBody=json.dumps(payload),
        )
    except Exception as exc:
        logger.exception("Failed to enqueue movies job %s: %s", job_id, exc)
        completed_at = _now_epoch()
        _update_job(
            job_id,
            {
                "status": "FAILED",
                "completed_at": completed_at,
                "error_message": f"Failed to enqueue job: {exc}",
                "progress_phase": "failed",
                "progress_message": "Failed to enqueue job",
            },
        )
        job_item["status"] = "FAILED"
        job_item["completed_at"] = completed_at
        job_item["error_message"] = f"Failed to enqueue job: {exc}"
        job_item["progress_phase"] = "failed"
        job_item["progress_message"] = "Failed to enqueue job"

    return _public_job(job_item)


def get_movies_job(job_id: str) -> Optional[Dict[str, Any]]:
    response = jobs_table.get_item(Key={"job_id": job_id})
    item = response.get("Item")
    if not item:
        return None
    return _public_job(item)


def _run_movies_job(job_id: str, username: str, force: bool) -> None:
    existing = jobs_table.get_item(Key={"job_id": job_id}).get("Item")
    if not existing:
        logger.warning("Job %s no longer exists", job_id)
        return

    existing_status = existing.get("status")
    if existing_status in TERMINAL_STATUSES:
        logger.info("Job %s is already terminal (%s)", job_id, existing_status)
        return

    attempts = int(_to_json_compatible(existing.get("attempts", 0))) + 1
    _update_job(
        job_id,
        {
            "status": "IN_PROGRESS",
            "attempts": attempts,
            "started_at": existing.get("started_at", _now_epoch()),
            "progress_phase": "starting",
            "progress_message": "Starting scrape",
        },
    )

    last_progress_write = 0.0

    def progress_callback(progress: Dict[str, Any]) -> None:
        nonlocal last_progress_write
        now = time.time()

        processed = progress.get("processed_movies")
        total = progress.get("total_movies")
        is_terminal = progress.get("phase") == "completed"
        is_boundary = processed in (0, total)
        if not is_terminal and not is_boundary and (now - last_progress_write) < 2:
            return

        _update_job(
            job_id,
            {
                "status": "IN_PROGRESS",
                "progress_phase": progress.get("phase"),
                "progress_message": progress.get("message"),
                "processed_movies": processed,
                "total_movies": total,
            },
        )
        last_progress_write = now

    try:
        result = backfill_movies(
            username=username,
            force=force,
            progress_callback=progress_callback,
        )
        if result.get("success"):
            _update_job(
                job_id,
                {
                    "status": "SUCCEEDED",
                    "completed_at": _now_epoch(),
                    "result_count": int(result.get("count", 0)),
                    "progress_phase": "completed",
                    "progress_message": result.get("message", "Movies scrape complete"),
                },
            )
            return

        _update_job(
            job_id,
            {
                "status": "FAILED",
                "completed_at": _now_epoch(),
                "error_message": result.get("message", "Unknown backfill failure"),
                "progress_phase": "failed",
                "progress_message": "Movies scrape failed",
            },
        )
    except Exception as exc:
        logger.exception("Unhandled job error for %s: %s", job_id, exc)
        _update_job(
            job_id,
            {
                "status": "FAILED",
                "completed_at": _now_epoch(),
                "error_message": str(exc),
                "progress_phase": "failed",
                "progress_message": "Movies scrape failed",
            },
        )


def process_sqs_record(record: Dict[str, Any]) -> None:
    body = record.get("body", "{}")
    payload = json.loads(body)
    job_id = payload["job_id"]
    username = payload["username"]
    force = bool(payload.get("force", False))
    _run_movies_job(job_id=job_id, username=username, force=force)
