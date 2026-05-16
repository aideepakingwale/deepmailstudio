from __future__ import annotations

import re
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
JOBS_DIR = DATA_DIR / "jobs"
UPLOADS_DIR = DATA_DIR / "uploads"
DRAFTS_DIR = BASE_DIR / "drafts"
LOGS_DIR = BASE_DIR / "logs"

for folder in (DATA_DIR, JOBS_DIR, UPLOADS_DIR, DRAFTS_DIR, LOGS_DIR):
    folder.mkdir(parents=True, exist_ok=True)

load_dotenv(BASE_DIR / ".env")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
OFFICIAL_TONES = {"official", "business", "formal", "strict", "angry", "upset"}

HEADER_ALIASES = {
    "salutation": "salutation",
    "first name": "first_name",
    "firstname": "first_name",
    "first_name": "first_name",
    "surname": "surname",
    "last name": "surname",
    "emailid": "emailid",
    "email id": "emailid",
    "email": "emailid",
    "to": "emailid",
    "cc": "cc",
    "bcc": "bcc",
    "content prompt to override generic content generation prompt any specific personalised content appended to the existing content to override the": "content_prompt",
    "content prompt": "content_prompt",
    "override prompt": "content_prompt",
    "personalised prompt": "content_prompt",
    "personalized prompt": "content_prompt",
    "language": "language",
    "email tone": "email_tone",
    "tone": "email_tone",
    "content lenghth": "content_length",
    "content length": "content_length",
    "length": "content_length",
    "attachment": "attachments",
    "attachments": "attachments",
    "images": "attachments",
}
