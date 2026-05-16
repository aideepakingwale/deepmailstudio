from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone
from queue import Queue
from typing import Any

from .ai import generate_email
from .config import ai_config, get_setting
from .settings import JOBS_DIR
from .storage import (
    append_log,
    find_record,
    load_job,
    mark_failed,
    parse_iso_datetime,
    update_record_in_job,
    utc_now,
)

GENERATION_QUEUE: Queue[tuple[str, str, str]] = Queue()
GENERATION_WORKER_STARTED = False
GENERATION_WORKER_LOCK = threading.Lock()


def start_generation_worker() -> None:
    global GENERATION_WORKER_STARTED
    with GENERATION_WORKER_LOCK:
        if GENERATION_WORKER_STARTED:
            return
        worker_count = max(1, int(get_setting("GENERATION_WORKERS", "3") or "3"))
        for index in range(worker_count):
            worker = threading.Thread(target=generation_worker_loop, daemon=True, name=f"generation-worker-{index + 1}")
            worker.start()
        GENERATION_WORKER_STARTED = True
        recover_active_generations()

def generation_worker_loop() -> None:
    while True:
        job_id, record_id, request_id = GENERATION_QUEUE.get()
        try:
            job = load_job(job_id)
            record = find_record(job, record_id)
            if record.get("last_generation_request_id") != request_id:
                append_log(job_id, record_id, "generation_skipped", f"request_id={request_id}; newer request exists")
                continue
            run_generation(job, record, request_id)
        except Exception as exc:
            try:
                job = load_job(job_id)
                record = find_record(job, record_id)
                mark_failed(job, record, f"Generation failed: {exc}")
            except Exception:
                append_log(job_id, record_id, "generation_worker_failed", str(exc))
        finally:
            GENERATION_QUEUE.task_done()

def recover_active_generations() -> None:
    max_age_minutes = int(get_setting("GENERATION_RECOVERY_MAX_AGE_MINUTES", "120") or "120")
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
    for path in JOBS_DIR.glob("*.json"):
        try:
            job = load_job(path.stem)
        except Exception:
            continue
        for record in job.get("records", []):
            if record.get("status") in {"queued", "generating"} and record.get("last_generation_request_id"):
                started_at = parse_iso_datetime(record.get("last_generation_started_at") or record.get("updated_at"))
                if started_at and started_at < cutoff:
                    append_log(
                        job["id"],
                        record["id"],
                        "generation_reset_stale",
                        f"request_id={record['last_generation_request_id']}; older than {max_age_minutes} minutes",
                    )
                    record["status"] = "pending"
                    record["errors"] = []
                    record["updated_at"] = utc_now()
                    update_record_in_job(job["id"], record["id"], lambda latest_record, source=record: latest_record.update(source))
                    continue
                append_log(
                    job["id"],
                    record["id"],
                    "generation_recovered",
                    f"request_id={record['last_generation_request_id']}; requeued after server start",
                )
                record["status"] = "queued"
                record["updated_at"] = utc_now()
                update_record_in_job(job["id"], record["id"], lambda latest_record, source=record: latest_record.update(source))
                GENERATION_QUEUE.put((job["id"], record["id"], record["last_generation_request_id"]))

def enqueue_generation(job: dict[str, Any], record: dict[str, Any]) -> str:
    if record.get("status") == "invalid":
        raise ValueError("; ".join(record.get("errors", [])))

    request_id = uuid.uuid4().hex[:10]
    current_ai = ai_config()
    record["status"] = "queued"
    record["last_generation_request_id"] = request_id
    record["last_generation_started_at"] = ""
    record["last_generation_completed_at"] = ""
    record["last_generation_provider"] = current_ai["provider"]
    record["last_generation_model"] = current_ai["active_model"]
    record["generation_attempts"] = int(record.get("generation_attempts") or 0) + 1
    record["updated_at"] = utc_now()
    append_log(
        job["id"],
        record["id"],
        "generation_queued",
        f"request_id={request_id}; provider={current_ai['provider']}; model={current_ai['active_model']}; endpoint={current_ai['active_endpoint']}",
    )
    update_record_in_job(job["id"], record["id"], lambda latest_record: latest_record.update(record))
    GENERATION_QUEUE.put((job["id"], record["id"], request_id))
    return request_id

def enqueue_all_pending_records(job: dict[str, Any]) -> int:
    queued = 0
    latest_job = load_job(job["id"])
    for record in latest_job.get("records", []):
        if record.get("status") in {"pending", "failed"} and not record.get("errors"):
            enqueue_generation(latest_job, record)
            latest_job = load_job(job["id"])
            queued += 1
    return queued

def run_generation(job: dict[str, Any], record: dict[str, Any], request_id: str) -> None:
    current_ai = ai_config()
    started_at = utc_now()
    def mark_generating(latest_record: dict[str, Any]) -> None:
        latest_record["status"] = "generating"
        latest_record["last_generation_request_id"] = request_id
        latest_record["last_generation_started_at"] = started_at
        latest_record["last_generation_provider"] = current_ai["provider"]
        latest_record["last_generation_model"] = current_ai["active_model"]
        latest_record["updated_at"] = utc_now()
    mark_generating(record)
    append_log(
        job["id"],
        record["id"],
        "generation_started",
        f"request_id={request_id}; provider={current_ai['provider']}; model={current_ai['active_model']}; endpoint={current_ai['active_endpoint']}",
    )
    update_record_in_job(job["id"], record["id"], mark_generating)

    subject, body_html, notes = generate_email(job, record)
    completed_at = utc_now()
    def mark_generated(latest_record: dict[str, Any]) -> None:
        latest_record["subject"] = subject
        latest_record["body_html"] = body_html
        latest_record["quality_notes"] = notes
        latest_record["errors"] = []
        latest_record["status"] = "generated"
        latest_record["last_generation_completed_at"] = completed_at
        latest_record["updated_at"] = utc_now()
    mark_generated(record)
    append_log(job["id"], record["id"], "generated", f"request_id={request_id}; {notes}")
    update_record_in_job(job["id"], record["id"], mark_generated)
