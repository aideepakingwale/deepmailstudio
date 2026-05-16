from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd


def attachment_paths(record: dict[str, Any]) -> list[Path]:
    raw = record.get("attachments", "")
    if not raw:
        return []
    parts = [part.strip().strip('"') for part in re.split(r"[;,]", raw) if part.strip()]
    return [Path(part).expanduser() for part in parts]

def parse_email_list(raw: str) -> list[str]:
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]

def strip_tags(value: str) -> str:
    return re.sub(r"<[^>]*>", " ", value or "").replace("&nbsp;", " ").strip()

def has_emoji(value: str) -> bool:
    return bool(re.search(r"[\U0001f300-\U0001faff\U00002700-\U000027bf]+", value or ""))

def guess_mime(path: Path) -> tuple[str, str]:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image", "jpeg"
    if suffix == ".png":
        return "image", "png"
    if suffix == ".gif":
        return "image", "gif"
    if suffix == ".pdf":
        return "application", "pdf"
    if suffix == ".csv":
        return "text", "csv"
    if suffix == ".txt":
        return "text", "plain"
    return "application", "octet-stream"

def clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value).strip()

def safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.@-]+", "_", value).strip("_") or "file"

def sanitize_email_html(body_html: str) -> str:
    cleaned = re.sub(r"<script\b[^>]*>.*?</script>", "", body_html or "", flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<style\b[^>]*>.*?</style>", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"</?(?:html|head|body)\b[^>]*>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^\s*```(?:html)?|```\s*$", "", cleaned.strip(), flags=re.IGNORECASE)
    return cleaned.strip()

def clear_send_errors(record: dict[str, Any]) -> None:
    errors = record.get("errors") or []
    record["errors"] = [
        error
        for error in errors
        if not str(error).lower().startswith("send failed")
    ]

def display_recipient(record: dict[str, Any]) -> str:
    name = " ".join([record.get("first_name", ""), record.get("surname", "")]).strip()
    return name or record.get("emailid", "") or record.get("id", "")
