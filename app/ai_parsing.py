from __future__ import annotations

import json
import re

from .utils import clean


def parse_model_response(text: str) -> tuple[str, str, str]:
    cleaned = text.strip()
    tagged = parse_tagged_model_response(cleaned)
    if tagged:
        return tagged
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    if "{" in cleaned and "}" in cleaned:
        cleaned = cleaned[cleaned.find("{"): cleaned.rfind("}") + 1]
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        data = {
            "subject": extract_json_string(cleaned, "subject"),
            "html_body": extract_json_string(cleaned, "html_body"),
            "quality_notes": extract_json_string(cleaned, "quality_notes"),
        }
        if not data["subject"] or not data["html_body"]:
            raise
    return (
        clean(data.get("subject", "")) or "A note for you",
        str(data.get("html_body", "")).strip(),
        clean(data.get("quality_notes", "")),
    )

def parse_tagged_model_response(text: str) -> tuple[str, str, str] | None:
    match = re.search(
        r"SUBJECT:\s*(?P<subject>.*?)\s*HTML_BODY:\s*(?P<body>.*?)\s*QUALITY_NOTES:\s*(?P<notes>.*)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    return (
        clean(match.group("subject")) or "A note for you",
        match.group("body").strip(),
        clean(match.group("notes")),
    )

def extract_json_string(text: str, key: str) -> str:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*"((?:\\.|[^"\\])*)"', text, re.DOTALL)
    if not match:
        return ""
    raw = match.group(1)
    try:
        return json.loads(f'"{raw}"')
    except json.JSONDecodeError:
        return raw.replace(r"\/", "/").replace(r"\"", '"').replace(r"\n", "\n")
