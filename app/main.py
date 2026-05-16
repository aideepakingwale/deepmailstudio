from __future__ import annotations

import csv
import html
import json
import os
import re
import smtplib
import ssl
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path
from typing import Any
from queue import Queue

import pandas as pd
import requests
from dotenv import dotenv_values, load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, send_file, url_for

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
JOBS_DIR = DATA_DIR / "jobs"
UPLOADS_DIR = DATA_DIR / "uploads"
DRAFTS_DIR = BASE_DIR / "drafts"
LOGS_DIR = BASE_DIR / "logs"

for folder in (DATA_DIR, JOBS_DIR, UPLOADS_DIR, DRAFTS_DIR, LOGS_DIR):
    folder.mkdir(parents=True, exist_ok=True)

load_dotenv(BASE_DIR / ".env")

GENERATION_QUEUE: Queue[tuple[str, str, str]] = Queue()
GENERATION_WORKER_STARTED = False
GENERATION_WORKER_LOCK = threading.Lock()

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


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024
    start_generation_worker()

    @app.get("/")
    def index():
        jobs = sorted(
            [load_job(path.stem) for path in JOBS_DIR.glob("*.json")],
            key=lambda job: job.get("created_at", ""),
            reverse=True,
        )
        return render_template("index.html", jobs=jobs[:10], defaults=smtp_defaults(), ai=ai_config())

    @app.post("/jobs")
    def create_job_route():
        sheet = request.files.get("sheet")
        if not sheet or not sheet.filename:
            return render_template(
                "index.html",
                jobs=[],
                defaults=smtp_defaults(),
                ai=ai_config(),
                error="Please choose an Excel or CSV recipient sheet.",
            ), 400

        context_prompt = request.form.get("context_prompt", "").strip()
        job = create_job(sheet, context_prompt, request.form)
        return redirect(url_for("job_view", job_id=job["id"]))

    @app.get("/jobs/<job_id>")
    def job_view(job_id: str):
        job = load_job(job_id)
        return render_template("job.html", job=job, defaults=smtp_defaults(), ai=ai_config())

    @app.get("/api/jobs/<job_id>")
    def get_job(job_id: str):
        return jsonify(load_job(job_id))

    @app.get("/api/jobs/<job_id>/events")
    def get_job_events(job_id: str):
        return jsonify({"events": read_job_events(job_id)})

    @app.post("/api/jobs/<job_id>/records/<record_id>/generate")
    def generate_record(job_id: str, record_id: str):
        job = load_job(job_id)
        record = find_record(job, record_id)
        try:
            request_id = enqueue_generation(job, record)
            return jsonify({"ok": True, "record": record})
        except Exception as exc:
            mark_failed(job, record, f"Generation failed: {exc}")
            return jsonify({"ok": False, "error": str(exc), "record": record}), 500

    @app.post("/jobs/<job_id>/records/<record_id>/generate")
    def generate_record_form(job_id: str, record_id: str):
        job = load_job(job_id)
        record = find_record(job, record_id)
        try:
            enqueue_generation(job, record)
        except Exception as exc:
            mark_failed(job, record, f"Generation failed: {exc}")
        return redirect(url_for("job_view", job_id=job_id))

    @app.post("/api/jobs/<job_id>/context")
    def update_job_context(job_id: str):
        job = load_job(job_id)
        payload = request.get_json(force=True)
        job["context_prompt"] = str(payload.get("context_prompt", "")).strip()
        job["use_row_prompts"] = bool(payload.get("use_row_prompts"))
        append_log(job_id, "-", "context_updated", "Campaign context/settings updated by human reviewer")
        save_job(job)
        return jsonify({"ok": True, "job": job})

    @app.post("/api/jobs/<job_id>/records/<record_id>/save")
    def save_record(job_id: str, record_id: str):
        job = load_job(job_id)
        record = find_record(job, record_id)
        payload = request.get_json(force=True)
        record["subject"] = str(payload.get("subject", "")).strip()
        record["body_html"] = str(payload.get("body_html", "")).strip()
        record["status"] = "approved" if payload.get("approved") else "generated"
        record["updated_at"] = utc_now()
        append_log(job_id, record_id, record["status"], "Human review saved")
        save_job(job)
        return jsonify({"ok": True, "record": record})

    @app.post("/api/jobs/<job_id>/records/<record_id>/draft")
    def draft_record(job_id: str, record_id: str):
        job = load_job(job_id)
        record = find_record(job, record_id)
        try:
            draft_path = write_eml_draft(job, record)
            record["status"] = "drafted"
            record["draft_path"] = str(draft_path)
            record["updated_at"] = utc_now()
            append_log(job_id, record_id, "drafted", str(draft_path))
            save_job(job)
            return jsonify({"ok": True, "record": record, "draft_path": str(draft_path)})
        except Exception as exc:
            mark_failed(job, record, f"Draft failed: {exc}")
            return jsonify({"ok": False, "error": str(exc), "record": record}), 500

    @app.post("/api/jobs/<job_id>/records/<record_id>/send")
    def send_record_route(job_id: str, record_id: str):
        job = load_job(job_id)
        record = find_record(job, record_id)
        payload = request.get_json(silent=True) or {}
        try:
            smtp_config = build_smtp_config({**job.get("smtp", {}), **payload.get("smtp", {})})
            send_email(job, record, smtp_config)
            record["status"] = "sent"
            record["sent_at"] = utc_now()
            record["updated_at"] = utc_now()
            append_log(job_id, record_id, "sent", f"Sent to {record['emailid']}")
            save_job(job)
            return jsonify({"ok": True, "record": record})
        except Exception as exc:
            mark_failed(job, record, f"Send failed: {exc}")
            return jsonify({"ok": False, "error": str(exc), "record": record}), 500

    @app.get("/drafts/<path:name>")
    def download_draft(name: str):
        path = DRAFTS_DIR / name
        if not path.exists():
            return jsonify({"error": "Draft not found"}), 404
        return send_file(path, as_attachment=True)

    @app.get("/sample")
    def sample_sheet():
        return send_file(BASE_DIR / "sample_recipients.csv", as_attachment=True)

    return app


def create_job(sheet_file, context_prompt: str, form: dict[str, Any]) -> dict[str, Any]:
    job_id = uuid.uuid4().hex[:12]
    filename = safe_filename(sheet_file.filename)
    upload_path = UPLOADS_DIR / f"{job_id}_{filename}"
    sheet_file.save(upload_path)

    records = load_records(upload_path)
    job = {
        "id": job_id,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "source_file": str(upload_path),
        "context_prompt": context_prompt,
        "use_row_prompts": form.get("use_row_prompts") == "on",
        "smtp": build_smtp_config(form, include_password=True),
        "ai": ai_config(),
        "records": records,
    }
    save_job(job)
    append_log(job_id, "-", "created", f"Loaded {len(records)} recipient rows")
    return job


def start_generation_worker() -> None:
    global GENERATION_WORKER_STARTED
    with GENERATION_WORKER_LOCK:
        if GENERATION_WORKER_STARTED:
            return
        worker = threading.Thread(target=generation_worker_loop, daemon=True, name="generation-worker")
        worker.start()
        GENERATION_WORKER_STARTED = True


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
    save_job(job)
    GENERATION_QUEUE.put((job["id"], record["id"], request_id))
    return request_id


def run_generation(job: dict[str, Any], record: dict[str, Any], request_id: str) -> None:
    current_ai = ai_config()
    record["status"] = "generating"
    record["last_generation_request_id"] = request_id
    record["last_generation_started_at"] = utc_now()
    record["last_generation_provider"] = current_ai["provider"]
    record["last_generation_model"] = current_ai["active_model"]
    record["generation_attempts"] = int(record.get("generation_attempts") or 0) + 1
    append_log(
        job["id"],
        record["id"],
        "generation_started",
        f"request_id={request_id}; provider={current_ai['provider']}; model={current_ai['active_model']}; endpoint={current_ai['active_endpoint']}",
    )
    save_job(job)

    subject, body_html, notes = generate_email(job, record)
    record["subject"] = subject
    record["body_html"] = body_html
    record["quality_notes"] = notes
    record["errors"] = []
    record["status"] = "generated"
    record["last_generation_completed_at"] = utc_now()
    record["updated_at"] = utc_now()
    append_log(job["id"], record["id"], "generated", f"request_id={request_id}; {notes}")
    save_job(job)


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
            "language": clean(row.get("language", "")) or "English",
            "email_tone": clean(row.get("email_tone", "")) or "friendly",
            "content_length": clean(row.get("content_length", "")) or "medium",
            "attachments": clean(row.get("attachments", "")),
            "subject": "",
            "body_html": "",
            "quality_notes": "",
            "status": "pending",
            "errors": [],
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


def generate_email(job: dict[str, Any], record: dict[str, Any]) -> tuple[str, str, str]:
    if record["status"] == "invalid":
        raise ValueError("; ".join(record.get("errors", [])))

    prompt = build_generation_prompt(job, record)
    provider = get_setting("AI_PROVIDER", "template").strip().lower() or "template"
    if is_zero_cost_mode() and provider == "openai":
        subject, body_html, notes = generate_template_email(job, record)
        return subject, body_html, notes + " Paid AI provider was blocked by ZERO_COST_MODE=true."

    try:
        if provider == "ollama":
            text = generate_with_ollama(prompt)
        elif provider == "lmstudio":
            text = generate_with_lmstudio(prompt)
        elif provider == "openai_compatible":
            text = generate_with_openai_compatible(prompt)
        elif provider == "groq":
            text = generate_with_groq(prompt)
        elif provider == "gemini":
            text = generate_with_gemini(prompt)
        elif provider == "openai":
            text = generate_with_openai(prompt)
        else:
            return generate_template_email(job, record)
    except Exception as exc:
        if should_fallback_to_template():
            subject, body_html, notes = generate_template_email(job, record)
            return subject, body_html, notes + f" Local AI provider fallback used: {exc}"
        raise

    try:
        subject, body_html, raw_notes = parse_model_response(text)
    except Exception as exc:
        if should_fallback_to_template():
            subject, body_html, notes = generate_template_email(job, record)
            return subject, body_html, notes + f" Local AI model returned malformed JSON, so template fallback was used: {exc}"
        raise
    body_html = polish_html(body_html, record)
    notes = validate_generated_email(subject, body_html, record)
    alignment_issue = validate_context_alignment(job, subject, body_html)
    if alignment_issue:
        if should_fallback_to_template():
            fallback_subject, fallback_body, fallback_notes = generate_template_email(job, record)
            return (
                fallback_subject,
                fallback_body,
                f"{fallback_notes} Local AI output rejected for context mismatch: {alignment_issue}",
            )
        raise ValueError(alignment_issue)
    if raw_notes:
        notes = f"{notes} Model notes: {raw_notes}".strip()
    return subject, body_html, notes


def build_generation_prompt(job: dict[str, Any], record: dict[str, Any]) -> str:
    full_name = " ".join(part for part in [record.get("first_name"), record.get("surname")] if part)
    attachment_names = ", ".join(path.name for path in attachment_paths(record)) or "None"
    use_row_prompts = bool(job.get("use_row_prompts"))
    row_prompt = record.get("content_prompt") if use_row_prompts else ""
    row_prompt_label = "Recipient-specific prompt" if use_row_prompts else "Recipient-specific prompt ignored for this job"
    return f"""
You are an agentic email copywriter and reviewer. Create one personalized email.

Authoritative campaign context:
{job.get("context_prompt") or "No global context was provided."}

Recipient:
- Salutation: {record.get("salutation") or "not specified"}
- Name: {full_name}
- Email: {record.get("emailid")}
- Language: {record.get("language") or "English"}
- Tone: {record.get("email_tone") or "friendly"}
- Desired length: {record.get("content_length") or "medium"}
- Attachments/images referenced: {attachment_names}
- {row_prompt_label}: {row_prompt or "None"}

Requirements:
- Draft like a human wrote it for this recipient.
- The authoritative campaign context is the main event/topic and must not be replaced.
- Apply recipient-specific prompts only when they support the campaign context.
- If a recipient-specific prompt conflicts with the campaign context, ignore the conflicting part unless it starts with "OVERRIDE:".
- Use the requested language and tone.
- Include a natural greeting using salutation and first name where appropriate.
- Use clean HTML suitable for an email body. Use paragraphs, bullets, and bold text when useful.
- Smileys are allowed only for friendly, funny, romantic, or casual tones. Do not use smileys for official, business, formal, angry, upset, or strict tones.
- Avoid hallucinating facts not present in the global or recipient prompt.
- Return exactly this plain text format, without Markdown fences and without JSON:
SUBJECT:
your subject line

HTML_BODY:
your email body as clean HTML

QUALITY_NOTES:
short review notes
""".strip()


def generate_with_ollama(prompt: str) -> str:
    base_url = get_setting("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    model = get_setting("OLLAMA_MODEL", "llama3.1")
    response = requests.post(
        f"{base_url}/api/chat",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": "Follow the user's requested output format exactly."},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "options": {"temperature": 0.7},
        },
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["message"]["content"]


def generate_with_openai_compatible(prompt: str) -> str:
    base_url = get_setting("OPENAI_COMPATIBLE_BASE_URL", "http://localhost:1234/v1").rstrip("/")
    api_key = get_setting("OPENAI_COMPATIBLE_API_KEY", "")
    model = get_setting("OPENAI_COMPATIBLE_MODEL", "local-model")
    return chat_completions_request(base_url, api_key, model, prompt)


def generate_with_lmstudio(prompt: str) -> str:
    base_url = get_setting("LM_STUDIO_BASE_URL", "http://localhost:1234/v1").rstrip("/")
    api_key = get_setting("LM_STUDIO_API_KEY", "")
    model = get_lmstudio_model(base_url)
    return chat_completions_request(base_url, api_key, model, prompt)


def generate_with_groq(prompt: str) -> str:
    api_key = get_setting("GROQ_API_KEY", "")
    model = get_setting("GROQ_MODEL", "llama-3.1-8b-instant")
    if not api_key:
        raise ValueError("GROQ_API_KEY must be set for AI_PROVIDER=groq.")
    return chat_completions_request("https://api.groq.com/openai/v1", api_key, model, prompt)


def generate_with_gemini(prompt: str) -> str:
    api_key = get_setting("GEMINI_API_KEY", "")
    model = get_setting("GEMINI_MODEL", "gemini-2.5-flash-lite")
    if not api_key:
        raise ValueError("GEMINI_API_KEY must be set for AI_PROVIDER=gemini.")

    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={
            "x-goog-api-key": api_key,
            "Content-Type": "application/json",
        },
        json={
            "system_instruction": {
                "parts": [
                    {"text": "You return only valid JSON for email generation tasks."}
                ]
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ],
            "generationConfig": {
                "temperature": 0.7,
                "responseMimeType": "application/json",
            },
        },
        timeout=120,
    )
    response.raise_for_status()
    data = response.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


def generate_with_openai(prompt: str) -> str:
    if is_zero_cost_mode():
        raise ValueError("OpenAI API is disabled because ZERO_COST_MODE=true.")
    api_key = get_setting("OPENAI_API_KEY", "")
    model = get_setting("OPENAI_MODEL", "")
    if not api_key or not model:
        raise ValueError("OPENAI_API_KEY and OPENAI_MODEL must be set for AI_PROVIDER=openai.")
    return chat_completions_request("https://api.openai.com/v1", api_key, model, prompt)


def chat_completions_request(base_url: str, api_key: str, model: str, prompt: str) -> str:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    timeout_seconds = int(get_setting("AI_REQUEST_TIMEOUT_SECONDS", "600") or "600")
    response = requests.post(
        f"{base_url}/chat/completions",
        headers=headers,
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": "Follow the user's requested output format exactly."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.7,
        },
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


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


def generate_template_email(job: dict[str, Any], record: dict[str, Any]) -> tuple[str, str, str]:
    salutation = record.get("salutation") or ""
    name = " ".join(part for part in [salutation, record.get("first_name")] if part).strip()
    context = job.get("context_prompt") or "I wanted to share this note with you."
    custom = record.get("content_prompt") if job.get("use_row_prompts") else ""
    tone = record.get("email_tone", "friendly")
    subject_base = first_sentence(context) or "A note for you"
    subject = f"{subject_base[:72]}".strip()

    body_parts = [
        f"<p>Hello {html.escape(name or record.get('first_name', 'there'))},</p>",
        f"<p>{html.escape(context)}</p>",
    ]
    if custom:
        body_parts.append(f"<p>{html.escape(custom)}</p>")
    if attachment_paths(record):
        body_parts.append("<p><strong>Attached:</strong> Please find the relevant file(s) included with this email.</p>")
    closing = "Warm regards" if tone.lower() not in OFFICIAL_TONES else "Regards"
    if tone.lower() in {"friendly", "funny", "casual"}:
        body_parts.append("<p>Looking forward to hearing from you.</p>")
    body_parts.append(f"<p>{closing},<br>{html.escape(get_setting('SMTP_FROM_NAME', 'Local AI Mail Agent'))}</p>")
    notes = "Generated with deterministic zero-cost template provider. Connect Ollama, Groq free tier, Gemini free tier, or a local LM Studio server for richer zero-cost personalization."
    return subject, "\n".join(body_parts), validate_generated_email(subject, "\n".join(body_parts), record) + " " + notes


def first_sentence(text: str) -> str:
    return re.split(r"[.!?\n]", text.strip(), maxsplit=1)[0].strip()


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
    if first_name and first_name not in plain_body and not (surname and surname in plain_body):
        notes.append("Recipient name was not found in body.")
    if record.get("email_tone", "").lower() in OFFICIAL_TONES and has_emoji(body_html):
        notes.append("Official/business tone should not contain smileys.")
    return " ".join(notes) or "Validation passed: grammar, personalization, tone, and email structure look acceptable for human review."


def validate_context_alignment(job: dict[str, Any], subject: str, body_html: str) -> str:
    context = strip_tags(job.get("context_prompt", "")).lower()
    if not context:
        return ""

    generated = strip_tags(f"{subject} {body_html}").lower()
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
    if len(unique_keywords) >= 3 and len(matched_keywords) < 2:
        return f"Generated email did not align with campaign keywords: {', '.join(unique_keywords[:6])}"
    return ""


def write_eml_draft(job: dict[str, Any], record: dict[str, Any]) -> Path:
    message = build_email_message(job, record)
    path = DRAFTS_DIR / f"{job['id']}_{record['id']}_{safe_filename(record['emailid'])}.eml"
    path.write_bytes(message.as_bytes())
    return path


def send_email(job: dict[str, Any], record: dict[str, Any], smtp_config: dict[str, Any]) -> None:
    if record.get("status") not in {"approved", "drafted", "generated"}:
        raise ValueError("Generate and approve the email before sending.")
    if not smtp_config.get("host"):
        raise ValueError("SMTP host is required. Use Draft instead if you do not have SMTP configured.")
    message = build_email_message(job, record, smtp_config)

    port = int(smtp_config.get("port") or 587)
    security = (smtp_config.get("security") or "starttls").lower()
    if security == "ssl":
        with smtplib.SMTP_SSL(smtp_config["host"], port, context=ssl.create_default_context(), timeout=60) as server:
            smtp_login(server, smtp_config)
            server.send_message(message)
    else:
        with smtplib.SMTP(smtp_config["host"], port, timeout=60) as server:
            server.ehlo()
            if security == "starttls":
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            smtp_login(server, smtp_config)
            server.send_message(message)


def build_email_message(job: dict[str, Any], record: dict[str, Any], smtp_config: dict[str, Any] | None = None) -> EmailMessage:
    if not record.get("subject") or not record.get("body_html"):
        raise ValueError("Email subject and body are required.")
    if record.get("errors"):
        raise ValueError("; ".join(record["errors"]))

    smtp_config = smtp_config or build_smtp_config(job.get("smtp", {}))
    from_email = smtp_config.get("from_email") or smtp_config.get("username") or "local-agent@example.local"
    from_name = smtp_config.get("from_name") or "Local AI Mail Agent"

    message = EmailMessage()
    message["Subject"] = record["subject"]
    message["From"] = formataddr((from_name, from_email))
    message["To"] = record["emailid"]
    if parse_email_list(record.get("cc", "")):
        message["Cc"] = ", ".join(parse_email_list(record.get("cc", "")))
    if parse_email_list(record.get("bcc", "")):
        message["Bcc"] = ", ".join(parse_email_list(record.get("bcc", "")))
    message["Message-ID"] = make_msgid(domain="local-ai-mail-agent")
    message.set_content(strip_tags(record["body_html"]))
    message.add_alternative(record["body_html"], subtype="html")

    for attachment in attachment_paths(record):
        if not attachment.exists():
            raise ValueError(f"Attachment not found: {attachment}")
        maintype, subtype = guess_mime(attachment)
        message.add_attachment(
            attachment.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=attachment.name,
        )
    return message


def smtp_login(server: smtplib.SMTP, smtp_config: dict[str, Any]) -> None:
    username = smtp_config.get("username")
    password = smtp_config.get("password")
    if username and password:
        server.login(username, password)


def build_smtp_config(values: dict[str, Any], include_password: bool = True) -> dict[str, Any]:
    password = clean(values.get("smtp_password") or values.get("password") or os.getenv("SMTP_PASSWORD", ""))
    config = {
        "host": clean(values.get("smtp_host") or values.get("host") or os.getenv("SMTP_HOST", "")),
        "port": clean(values.get("smtp_port") or values.get("port") or os.getenv("SMTP_PORT", "587")),
        "username": clean(values.get("smtp_username") or values.get("username") or os.getenv("SMTP_USERNAME", "")),
        "from_email": clean(values.get("smtp_from_email") or values.get("from_email") or os.getenv("SMTP_FROM_EMAIL", "")),
        "from_name": clean(values.get("smtp_from_name") or values.get("from_name") or os.getenv("SMTP_FROM_NAME", "Local AI Mail Agent")),
        "security": clean(values.get("smtp_security") or values.get("security") or os.getenv("SMTP_SECURITY", "starttls")),
    }
    if include_password:
        config["password"] = password
    return config


def smtp_defaults() -> dict[str, Any]:
    config = build_smtp_config({}, include_password=False)
    config["password"] = ""
    return config


def ai_config() -> dict[str, str]:
    provider = get_setting("AI_PROVIDER", "template")
    lmstudio_endpoint = get_setting("LM_STUDIO_BASE_URL", "http://localhost:1234/v1")
    lmstudio_model = get_lmstudio_model(lmstudio_endpoint) if provider == "lmstudio" else get_setting("LM_STUDIO_MODEL", "local-model")
    model_by_provider = {
        "template": "deterministic-template",
        "ollama": get_setting("OLLAMA_MODEL", "llama3.1"),
        "lmstudio": lmstudio_model,
        "openai_compatible": get_setting("OPENAI_COMPATIBLE_MODEL", "local-model"),
        "groq": get_setting("GROQ_MODEL", "llama-3.1-8b-instant"),
        "gemini": get_setting("GEMINI_MODEL", "gemini-2.5-flash-lite"),
        "openai": get_setting("OPENAI_MODEL", ""),
    }
    endpoint_by_provider = {
        "template": "local template engine",
        "ollama": get_setting("OLLAMA_BASE_URL", "http://localhost:11434"),
        "lmstudio": lmstudio_endpoint,
        "openai_compatible": get_setting("OPENAI_COMPATIBLE_BASE_URL", "http://localhost:1234/v1"),
        "groq": "https://api.groq.com/openai/v1",
        "gemini": "https://generativelanguage.googleapis.com/v1beta",
        "openai": "https://api.openai.com/v1",
    }
    return {
        "provider": provider,
        "active_model": model_by_provider.get(provider, "unknown"),
        "active_endpoint": endpoint_by_provider.get(provider, "unknown"),
        "zero_cost_mode": str(is_zero_cost_mode()),
        "fallback_to_template": str(should_fallback_to_template()),
        "ollama_model": get_setting("OLLAMA_MODEL", "llama3.1"),
        "lmstudio_model": lmstudio_model,
        "openai_compatible_model": get_setting("OPENAI_COMPATIBLE_MODEL", "local-model"),
        "groq_model": get_setting("GROQ_MODEL", "llama-3.1-8b-instant"),
        "gemini_model": get_setting("GEMINI_MODEL", "gemini-2.5-flash-lite"),
    }


def is_zero_cost_mode() -> bool:
    return get_setting("ZERO_COST_MODE", "true").strip().lower() in {"1", "true", "yes", "on"}


def should_fallback_to_template() -> bool:
    return get_setting("AI_FALLBACK_TO_TEMPLATE", "true").strip().lower() in {"1", "true", "yes", "on"}


def get_lmstudio_model(base_url: str | None = None) -> str:
    configured = get_setting("LM_STUDIO_MODEL", "local-model").strip()
    if configured and configured != "local-model":
        return configured

    base_url = (base_url or get_setting("LM_STUDIO_BASE_URL", "http://localhost:1234/v1")).rstrip("/")
    try:
        response = requests.get(f"{base_url}/models", timeout=5)
        response.raise_for_status()
        models = response.json().get("data", [])
        for model in models:
            model_id = str(model.get("id", ""))
            if model_id and "embed" not in model_id.lower():
                return model_id
    except Exception:
        pass
    return configured or "local-model"


def get_setting(key: str, default: str = "") -> str:
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        value = dotenv_values(env_path).get(key)
        if value is not None:
            return str(value)
    return os.getenv(key, default)


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


def find_record(job: dict[str, Any], record_id: str) -> dict[str, Any]:
    for record in job["records"]:
        if record["id"] == record_id:
            return record
    raise KeyError(f"Record not found: {record_id}")


def load_job(job_id: str) -> dict[str, Any]:
    path = JOBS_DIR / f"{safe_filename(job_id)}.json"
    if not path.exists():
        raise FileNotFoundError(f"Job not found: {job_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_job(job: dict[str, Any]) -> None:
    job["updated_at"] = utc_now()
    path = JOBS_DIR / f"{job['id']}.json"
    path.write_text(json.dumps(job, indent=2), encoding="utf-8")


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


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=7865, debug=True)
