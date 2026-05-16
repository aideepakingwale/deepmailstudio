from __future__ import annotations

import csv
import json
import threading
from datetime import datetime, timezone
from typing import Any

from .settings import JOBS_DIR, LOGS_DIR
from .utils import safe_filename

JOB_FILE_LOCK = threading.RLock()


def find_record(job: dict[str, Any], record_id: str) -> dict[str, Any]:
    for record in job["records"]:
        if record["id"] == record_id:
            return record
    raise KeyError(f"Record not found: {record_id}")

def load_job(job_id: str) -> dict[str, Any]:
    path = JOBS_DIR / f"{safe_filename(job_id)}.json"
    if not path.exists():
        raise FileNotFoundError(f"Job not found: {job_id}")
    with JOB_FILE_LOCK:
        return json.loads(path.read_text(encoding="utf-8"))

def save_job(job: dict[str, Any]) -> None:
    job["updated_at"] = utc_now()
    path = JOBS_DIR / f"{job['id']}.json"
    with JOB_FILE_LOCK:
        path.write_text(json.dumps(job, indent=2), encoding="utf-8")

def update_record_in_job(job_id: str, record_id: str, updater) -> dict[str, Any]:
    with JOB_FILE_LOCK:
        path = JOBS_DIR / f"{safe_filename(job_id)}.json"
        if not path.exists():
            raise FileNotFoundError(f"Job not found: {job_id}")
        job = json.loads(path.read_text(encoding="utf-8"))
        record = find_record(job, record_id)
        updater(record)
        job["updated_at"] = utc_now()
        path.write_text(json.dumps(job, indent=2), encoding="utf-8")
        return record

def mark_failed(job: dict[str, Any], record: dict[str, Any], error: str):
    record["status"] = "failed"
    record.setdefault("errors", []).append(error)
    record["updated_at"] = utc_now()
    append_log(job["id"], record["id"], "failed", error)
    save_job(job)

def append_log(job_id: str, record_id: str, event: str, details: str) -> None:
    path = LOGS_DIR / "agent_events.csv"
    write_log_row(path, job_id, record_id, event, details)

def write_log_row(path: Path, job_id: str, record_id: str, event: str, details: str) -> None:
    new_file = not path.exists()
    try:
        with path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            if new_file:
                writer.writerow(["timestamp", "job_id", "record_id", "event", "details"])
            writer.writerow([utc_now(), job_id, record_id, event, details])
    except PermissionError:
        fallback = LOGS_DIR / f"agent_events_fallback_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        with fallback.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["timestamp", "job_id", "record_id", "event", "details"])
            writer.writerow([utc_now(), job_id, record_id, event, f"{details} (main log locked: {path.name})"])

def read_job_events(job_id: str) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    for path in sorted(LOGS_DIR.glob("agent_events*.csv")):
        try:
            with path.open("r", newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    if row.get("job_id") == job_id:
                        row["source"] = path.name
                        events.append(row)
        except (PermissionError, OSError):
            continue
    return events[-50:]

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
