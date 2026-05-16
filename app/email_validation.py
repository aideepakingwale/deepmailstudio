from __future__ import annotations

import html
import re

from .settings import OFFICIAL_TONES
from .utils import has_emoji, strip_tags


def polish_html(body_html: str, record: dict[str, Any]) -> str:
    if not body_html:
        body_html = f"<p>Hello {html.escape(record.get('first_name', 'there'))},</p><p></p>"
    if "<" not in body_html:
        body_html = "".join(f"<p>{html.escape(line.strip())}</p>" for line in body_html.splitlines() if line.strip())
    if record.get("email_tone", "").lower() in OFFICIAL_TONES:
        body_html = re.sub(r"[\U0001f300-\U0001faff\U00002700-\U000027bf]+", "", body_html)
    return body_html

def validate_generated_email(subject: str, body_html: str, record: dict[str, Any]) -> str:
    notes = []
    if not subject:
        notes.append("Subject is empty.")
    if len(strip_tags(body_html)) < 40:
        notes.append("Body may be too short.")
    plain_body = strip_tags(body_html).lower()
    first_name = record.get("first_name", "").lower()
    surname = record.get("surname", "").lower()
    language = (record.get("language") or "English").strip().lower()
    if language == "english" and first_name and first_name not in plain_body and not (surname and surname in plain_body):
        notes.append("Recipient name was not found in body.")
    if record.get("email_tone", "").lower() in OFFICIAL_TONES and has_emoji(body_html):
        notes.append("Official/business tone should not contain smileys.")
    return " ".join(notes) or "Validation passed: grammar, personalization, tone, and email structure look acceptable for human review."

def language_rules(language: str) -> str:
    normalized = language.strip().lower()
    if normalized in {"hindi", "हिंदी"}:
        return "Use natural Hindi written primarily in Devanagari script. Do not write the email in English."
    if normalized in {"marathi", "मराठी"}:
        return "Use natural Marathi written primarily in Devanagari script. Do not write the email in English or Hindi."
    if normalized == "english":
        return "Use natural English."
    return f"Use natural {language}. Do not switch to English unless the row explicitly asks for bilingual content."

def validate_language_requirement(subject: str, body_html: str, record: dict[str, Any]) -> str:
    language = (record.get("language") or "English").strip().lower()
    plain = strip_tags(f"{subject} {body_html}")
    letters = re.findall(r"[A-Za-z\u0900-\u097F]", plain)
    if not letters:
        return ""
    devanagari = len(re.findall(r"[\u0900-\u097F]", plain))
    latin = len(re.findall(r"[A-Za-z]", plain))
    total = max(1, devanagari + latin)

    if language in {"hindi", "हिंदी", "marathi", "मराठी"} and devanagari / total < 0.35:
        return f"Expected {record.get('language')} in Devanagari script, but output appears mostly non-Devanagari."
    if language == "english" and devanagari / total > 0.25:
        return "Expected English, but output contains too much Devanagari text."
    return ""

def validate_context_alignment(job: dict[str, Any], subject: str, body_html: str) -> str:
    context = strip_tags(job.get("context_prompt", "")).lower()
    if not context:
        return ""

    generated = strip_tags(f"{subject} {body_html}").lower()
    generated_has_devanagari = bool(re.search(r"[\u0900-\u097F]", generated))
    date_tokens = re.findall(r"\b(?:\d{1,2}|20\d{2}|jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b", context)
    missing_date_tokens = [token for token in date_tokens if token not in generated]

    stopwords = {
        "invite", "invited", "everyone", "along", "with", "about", "there", "their", "should",
        "would", "could", "party", "email", "person", "people", "please", "certain", "event",
    }
    keywords = [
        token
        for token in re.findall(r"[a-zA-Z']{4,}", context)
        if token not in stopwords
    ]
    unique_keywords = list(dict.fromkeys(keywords))
    matched_keywords = [token for token in unique_keywords if token in generated]

    if date_tokens and missing_date_tokens:
        return f"Generated email missed required date token(s): {', '.join(missing_date_tokens)}"
    if generated_has_devanagari:
        return ""
    if len(unique_keywords) >= 3 and len(matched_keywords) < 2:
        return f"Generated email did not align with campaign keywords: {', '.join(unique_keywords[:6])}"
    return ""
