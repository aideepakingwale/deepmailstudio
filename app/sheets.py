from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

import pandas as pd

from .settings import EMAIL_RE, HEADER_ALIASES
from .utils import attachment_paths, clean, parse_email_list


def load_records(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() in {".xlsx", ".xlsm", ".xls"}:
        frame = pd.read_excel(path, dtype=str).fillna("")
    else:
        frame = pd.read_csv(path, dtype=str).fillna("")

    frame = frame.rename(columns={column: normalize_header(column) for column in frame.columns})
    records: list[dict[str, Any]] = []
    for index, row in frame.iterrows():
        record = {
            "id": uuid.uuid4().hex[:10],
            "row_number": int(index) + 2,
            "salutation": clean(row.get("salutation", "")),
            "first_name": clean(row.get("first_name", "")),
            "surname": clean(row.get("surname", "")),
            "emailid": clean(row.get("emailid", "")),
            "cc": clean(row.get("cc", "")),
            "bcc": clean(row.get("bcc", "")),
            "content_prompt": clean(row.get("content_prompt", "")),
            "prompt_mode": "append",
            "language": clean(row.get("language", "")) or "English",
            "email_tone": clean(row.get("email_tone", "")) or "friendly",
            "content_length": clean(row.get("content_length", "")) or "medium",
            "attachments": clean(row.get("attachments", "")),
            "subject": "",
            "body_html": "",
            "quality_notes": "",
            "status": "pending",
            "errors": [],
            "row_verified": False,
            "updated_at": "",
        }
        record["errors"] = validate_record(record)
        if record["errors"]:
            record["status"] = "invalid"
        records.append(record)
    return records

def normalize_header(value: str) -> str:
    key = re.sub(r"\s+", " ", str(value).strip().lower())
    return HEADER_ALIASES.get(key, key.replace(" ", "_"))

def update_recipient_config(record: dict[str, Any], payload: dict[str, Any]) -> None:
    editable_fields = [
        "salutation",
        "first_name",
        "surname",
        "emailid",
        "cc",
        "bcc",
        "content_prompt",
        "language",
        "email_tone",
        "content_length",
        "attachments",
    ]
    for field in editable_fields:
        if field in payload:
            record[field] = clean(payload.get(field))
    prompt_mode = clean(payload.get("prompt_mode") or record.get("prompt_mode") or "append").lower()
    if prompt_mode not in {"append", "enhance", "override", "ignore"}:
        prompt_mode = "append"
    record["prompt_mode"] = prompt_mode
    if record.get("status") in {"generated", "approved", "drafted"}:
        record["status"] = "pending"
        record["subject"] = ""
        record["body_html"] = ""
        record["quality_notes"] = ""

def validate_record(record: dict[str, Any]) -> list[str]:
    errors = []
    if not record["first_name"]:
        errors.append("First Name is required.")
    if not record["emailid"] or not EMAIL_RE.match(record["emailid"]):
        errors.append("emailid is missing or invalid.")
    for field in ("cc", "bcc"):
        invalid = [address for address in parse_email_list(record.get(field, "")) if not EMAIL_RE.match(address)]
        if invalid:
            errors.append(f"{field} contains invalid email address(es): {', '.join(invalid)}")
    missing_paths = [str(path) for path in attachment_paths(record) if not path.exists()]
    if missing_paths:
        errors.append("Attachment path(s) not found: " + "; ".join(missing_paths))
    return errors
