import logging
from typing import Any, Dict, List

from services.movie_jobs import process_sqs_record


logger = logging.getLogger(__name__)


def handler(event: Dict[str, Any], context: Any) -> Dict[str, List[Dict[str, str]]]:
    records = event.get("Records", [])
    failures: List[Dict[str, str]] = []

    for record in records:
        try:
            process_sqs_record(record)
        except Exception as exc:
            logger.exception("Failed to process SQS record: %s", exc)
            message_id = record.get("messageId")
            if message_id:
                failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": failures}
