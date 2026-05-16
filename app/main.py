from __future__ import annotations

import csv
import ctypes
import html
import json
import os
import re
import shutil
import smtplib
import ssl
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path
from typing import Any
from queue import Queue

import pandas as pd
import requests
from dotenv import dotenv_values, load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, send_file, url_for

try:
    import winreg
except ImportError:
    winreg = None

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
JOB_FILE_LOCK = threading.RLock()

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

    @app.get("/api/mail-clients")
    def get_mail_clients():
        return jsonify({"clients": detect_mail_clients(), "default_client_id": default_mail_client_id()})

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
        job["brand"] = build_brand_config(payload.get("brand", {}))
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
        clear_send_errors(record)
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
            clear_send_errors(record)
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

    @app.post("/api/jobs/<job_id>/send-selected")
    def send_selected_records(job_id: str):
        job = load_job(job_id)
        payload = request.get_json(force=True)
        record_ids = [str(record_id) for record_id in payload.get("record_ids", []) if record_id]
        client_id = str(payload.get("client_id", "")).strip()
        if not record_ids:
            return jsonify({"ok": False, "error": "Select at least one approved recipient."}), 400
        try:
            result = send_selected_with_client(job, record_ids, client_id)
            return jsonify({"ok": True, **result, "job": load_job(job_id)})
        except Exception as exc:
            append_log(job_id, "-", "bulk_send_failed", str(exc))
            return jsonify({"ok": False, "error": str(exc), "job": load_job(job_id)}), 500

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
        "auto_start_generation": form.get("auto_start_generation", "on") == "on",
        "brand": build_brand_config(form),
        "smtp": build_smtp_config(form, include_password=True),
        "ai": ai_config(),
        "records": records,
    }
    save_job(job)
    append_log(job_id, "-", "created", f"Loaded {len(records)} recipient rows")
    if job["auto_start_generation"]:
        queued = enqueue_all_pending_records(job)
        append_log(job_id, "-", "auto_start_generation", f"Queued {queued} valid recipient rows")
    return job


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
    body_html = sanitize_email_html(body_html)
    notes = validate_generated_email(subject, body_html, record)
    language_issue = validate_language_requirement(subject, body_html, record)
    if language_issue:
        if should_fallback_to_template():
            fallback_subject, fallback_body, fallback_notes = generate_template_email(job, record)
            return (
                fallback_subject,
                fallback_body,
                f"{fallback_notes} Local AI output rejected for language mismatch: {language_issue}",
            )
        raise ValueError(language_issue)
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
    brand = get_brand_config(job)
    language = record.get("language") or "English"
    language_instruction = language_rules(language)
    return f"""
You are an agentic email copywriter and reviewer. Create one personalized email.

Authoritative campaign context:
{job.get("context_prompt") or "No global context was provided."}

Brand and rich HTML requirements:
- Brand name: {brand["name"] or "Not specified"}
- Brand voice: {brand["voice"] or "Warm, clear, trustworthy"}
- Primary color: {brand["primary_color"]}
- Accent color: {brand["accent_color"]}
- Logo URL/path: {brand["logo_url"] or "None"}
- CTA text: {brand["cta_text"] or "None"}
- CTA URL: {brand["cta_url"] or "None"}
- Footer: {brand["footer"] or "None"}
- Layout style: {brand["layout"]}

Recipient:
- Salutation: {record.get("salutation") or "not specified"}
- Name: {full_name}
- Email: {record.get("emailid")}
- Language: {language}
- Tone: {record.get("email_tone") or "friendly"}
- Desired length: {record.get("content_length") or "medium"}
- Attachments/images referenced: {attachment_names}
- {row_prompt_label}: {row_prompt or "None"}

Requirements:
- Draft like a human wrote it for this recipient.
- The authoritative campaign context is the main event/topic and must not be replaced.
- Apply recipient-specific prompts only when they support the campaign context.
- If a recipient-specific prompt conflicts with the campaign context, ignore the conflicting part unless it starts with "OVERRIDE:".
- Produce rich, email-client-friendly HTML using inline styles.
- Use a polished branded layout with header, body sections, key details, CTA button when CTA text is available, and footer.
- Keep the HTML self-contained. Do not use external CSS, JavaScript, forms, or unsupported interactive elements.
- Use table-free simple HTML unless a table is needed for layout compatibility.
- Use the requested language and tone.
- Language is a hard requirement: write the subject, greeting, body, CTA, and closing in {language}.
- {language_instruction}
- Keep proper nouns, brand names, email addresses, URLs, and unavoidable technical terms as-is, but translate normal sentence text.
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
    brand = get_brand_config(job)
    subject_base = first_sentence(context) or "A note for you"
    localized = localize_template_text(record, context, subject_base)
    subject = localized["subject"]
    greeting = localized["greeting"].format(name=html.escape(name or record.get("first_name", "there")))
    logo_html = ""
    if brand["logo_url"]:
        logo_html = (
            f'<img src="{html.escape(brand["logo_url"])}" alt="{html.escape(brand["name"] or "Brand")}" '
            'style="max-width:160px;height:auto;display:block;margin-bottom:16px;">'
        )
    cta_html = ""
    if brand["cta_text"]:
        href = brand["cta_url"] or "#"
        cta_html = (
            f'<p style="margin:24px 0 4px;"><a href="{html.escape(href)}" '
            f'style="background:{html.escape(brand["primary_color"])};color:#ffffff;text-decoration:none;'
            'padding:12px 18px;border-radius:6px;display:inline-block;font-weight:700;">'
            f'{html.escape(brand["cta_text"])}</a></p>'
        )
    body_parts = [
        f'<div style="font-family:Segoe UI,Arial,sans-serif;line-height:1.6;color:#1f2937;max-width:680px;margin:0 auto;border:1px solid #e5e7eb;border-radius:8px;overflow:hidden;background:#ffffff;">',
        f'<div style="background:{html.escape(brand["primary_color"])};padding:22px;color:#ffffff;">'
        f'{logo_html}<h1 style="margin:0;font-size:24px;line-height:1.25;">{html.escape(subject)}</h1>'
        f'<p style="margin:8px 0 0;color:#eef2ff;">{html.escape(brand["name"] or "Personal Invitation")}</p></div>',
        '<div style="padding:24px;">',
        f'<p style="margin-top:0;">{greeting}</p>',
        f'<p>{html.escape(localized["context"])}</p>',
    ]
    if custom:
        body_parts.append(f"<p>{html.escape(custom)}</p>")
    if attachment_paths(record):
        body_parts.append("<p><strong>Attached:</strong> Please find the relevant file(s) included with this email.</p>")
    body_parts.append(
        f'<div style="border-left:4px solid {html.escape(brand["accent_color"])};background:#f9fafb;'
        f'padding:14px 16px;margin:18px 0;"><strong>{html.escape(localized["key_label"])}</strong> {html.escape(localized["key_note"])}</div>'
    )
    body_parts.append(cta_html)
    closing = "Warm regards" if tone.lower() not in OFFICIAL_TONES else "Regards"
    if tone.lower() in {"friendly", "funny", "casual"}:
        body_parts.append(f"<p>{html.escape(localized['friendly_line'])}</p>")
    body_parts.append(f"<p>{html.escape(localized['closing'] if tone.lower() in OFFICIAL_TONES else closing)},<br>{html.escape(brand['name'] or get_setting('SMTP_FROM_NAME', 'DeepMail Studio'))}</p>")
    body_parts.append("</div>")
    if brand["footer"]:
        body_parts.append(
            f'<div style="background:#f3f4f6;padding:14px 24px;color:#6b7280;font-size:12px;">{html.escape(brand["footer"])}</div>'
        )
    body_parts.append("</div>")
    notes = "Generated with deterministic zero-cost template provider. Connect Ollama, Groq free tier, Gemini free tier, or a local LM Studio server for richer zero-cost personalization."
    return subject, "\n".join(body_parts), validate_generated_email(subject, "\n".join(body_parts), record) + " " + notes


def localize_template_text(record: dict[str, Any], context: str, subject_base: str) -> dict[str, str]:
    language = (record.get("language") or "English").strip().lower()
    if language in {"hindi", "हिंदी"}:
        localized_context = localize_context_summary(context, "hindi")
        return {
            "subject": "तत्काल सूचना: महत्वपूर्ण बैठक",
            "greeting": "नमस्ते {name},",
            "context": localized_context,
            "key_label": "मुख्य सूचना:",
            "key_note": "कृपया विवरण ध्यान से पढ़ें और आवश्यक होने पर तुरंत उत्तर दें।",
            "friendly_line": "आपके सहयोग के लिए धन्यवाद।",
            "closing": "सादर",
        }
    if language in {"marathi", "मराठी"}:
        localized_context = localize_context_summary(context, "marathi")
        return {
            "subject": "तातडीची सूचना: महत्त्वाची बैठक",
            "greeting": "नमस्कार {name},",
            "context": localized_context,
            "key_label": "मुख्य सूचना:",
            "key_note": "कृपया तपशील काळजीपूर्वक वाचा आणि आवश्यक असल्यास त्वरित प्रतिसाद द्या.",
            "friendly_line": "आपल्या सहकार्याबद्दल धन्यवाद.",
            "closing": "सादर",
        }
    return {
        "subject": f"{subject_base[:72]}".strip(),
        "greeting": "Hello {name},",
        "context": context,
        "key_label": "Key note:",
        "key_note": "Please review the details and respond if needed.",
        "friendly_line": "Looking forward to hearing from you.",
        "closing": "Regards",
    }


def localize_context_summary(context: str, language: str) -> str:
    normalized = context.lower()
    is_emergency = any(term in normalized for term in ["urgent", "emergency", "critical", "power failure"])
    if language == "hindi":
        if is_emergency:
            return "ऑफिस में गंभीर बिजली समस्या के कारण एक तत्काल और महत्वपूर्ण बैठक बुलानी है। कृपया इस विषय को प्राथमिकता दें और आवश्यक चर्चा के लिए उपलब्ध रहें।"
        return f"यह महत्वपूर्ण सूचना है: {context}"
    if language == "marathi":
        if is_emergency:
            return "ऑफिसमध्ये गंभीर वीजपुरवठा समस्या निर्माण झाल्यामुळे तातडीची आणि महत्त्वाची बैठक बोलवायची आहे. कृपया या विषयाला प्राधान्य द्या आणि आवश्यक चर्चेसाठी उपलब्ध राहा."
        return f"ही महत्त्वाची सूचना आहे: {context}"
    return context


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


def sanitize_email_html(body_html: str) -> str:
    cleaned = re.sub(r"<script\b[^>]*>.*?</script>", "", body_html or "", flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<style\b[^>]*>.*?</style>", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"</?(?:html|head|body)\b[^>]*>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^\s*```(?:html)?|```\s*$", "", cleaned.strip(), flags=re.IGNORECASE)
    return cleaned.strip()


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


def detect_mail_clients() -> list[dict[str, Any]]:
    smtp_config = build_smtp_config({}, include_password=False)
    clients = [
        {
            "id": "smtp",
            "name": "Configured SMTP mailbox",
            "kind": "smtp",
            "installed": bool(smtp_config.get("host")),
            "can_send": bool(smtp_config.get("host")),
            "detail": smtp_config.get("host") or "Configure SMTP on the campaign/home screen to enable this option.",
            "auto_send": True,
        }
    ]

    outlook_path = find_windows_app_path("OUTLOOK.EXE")
    pywin32_ready = has_pywin32()
    activation_hint = outlook_activation_hint()
    outlook_reason = ""
    if not pywin32_ready:
        outlook_reason = "Install pywin32 in the local environment to enable Outlook automation."
    elif activation_hint:
        outlook_reason = activation_hint
    clients.append(
        {
            "id": "outlook_classic",
            "name": "Microsoft Outlook desktop",
            "kind": "desktop",
            "installed": bool(outlook_path),
            "can_send": bool(outlook_path and pywin32_ready and not activation_hint),
            "detail": outlook_path or "Classic Outlook was not found in Windows app paths.",
            "auto_send": True,
            "reason": outlook_reason,
        }
    )

    for client_id, name, exe_name in [
        ("thunderbird", "Mozilla Thunderbird", "thunderbird.exe"),
        ("new_outlook", "New Outlook for Windows", "olk.exe"),
        ("windows_mail", "Windows Mail", "HxOutlook.exe"),
    ]:
        found_path = find_windows_app_path(exe_name) or shutil.which(exe_name)
        clients.append(
            {
                "id": client_id,
                "name": name,
                "kind": "desktop",
                "installed": bool(found_path),
                "can_send": False,
                "detail": found_path or "Not detected.",
                "auto_send": False,
                "reason": "Detected for visibility only. This app does not expose a safe local automatic-send API to DeepMail Studio.",
            }
        )

    return clients


def default_mail_client_id() -> str:
    for client in detect_mail_clients():
        if client["id"] == "outlook_classic" and client["can_send"]:
            return client["id"]
    for client in detect_mail_clients():
        if client["id"] == "smtp" and client["can_send"]:
            return client["id"]
    return ""


def has_pywin32() -> bool:
    try:
        import win32com.client  # noqa: F401
        return True
    except Exception:
        return False


def find_windows_app_path(exe_name: str) -> str:
    if not winreg:
        return ""

    registry_paths = [
        rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}",
        rf"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}",
    ]
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for key_path in registry_paths:
            try:
                with winreg.OpenKey(root, key_path) as key:
                    value, _ = winreg.QueryValueEx(key, "")
                    if value:
                        return str(value)
            except OSError:
                continue

    candidates = [
        Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps" / exe_name,
        Path(os.getenv("ProgramFiles", "")) / "Microsoft Office" / "root" / "Office16" / exe_name,
        Path(os.getenv("ProgramFiles(x86)", "")) / "Microsoft Office" / "root" / "Office16" / exe_name,
        Path(os.getenv("ProgramFiles", "")) / "Mozilla Thunderbird" / exe_name,
        Path(os.getenv("ProgramFiles(x86)", "")) / "Mozilla Thunderbird" / exe_name,
    ]
    for candidate in candidates:
        if str(candidate) and candidate.exists():
            return str(candidate)
    return ""


def send_selected_with_client(job: dict[str, Any], record_ids: list[str], client_id: str) -> dict[str, Any]:
    clients = {client["id"]: client for client in detect_mail_clients()}
    client = clients.get(client_id)
    if not client:
        raise ValueError("Choose a detected sending option.")
    if not client.get("can_send"):
        reason = client.get("reason") or "The selected client cannot be controlled for automatic sending."
        raise ValueError(f"{client['name']} is not available for automatic sending. {reason}")

    selected_records = [find_record(job, record_id) for record_id in record_ids]
    blocked = [
        display_recipient(record)
        for record in selected_records
        if record.get("status") not in {"approved", "drafted"}
    ]
    if blocked:
        raise ValueError("Only approved or drafted emails can be bulk sent. Review these rows first: " + ", ".join(blocked))

    sent: list[str] = []
    failed: list[dict[str, str]] = []
    for record in selected_records:
        try:
            clear_send_errors(record)
            if client_id == "smtp":
                send_email(job, record, build_smtp_config(job.get("smtp", {})))
            elif client_id == "outlook_classic":
                send_via_outlook(job, record)
            else:
                raise ValueError(f"{client['name']} automatic sending is not implemented.")
            record["status"] = "sent"
            record["sent_at"] = utc_now()
            record["sent_via"] = client["name"]
            record["updated_at"] = utc_now()
            append_log(job["id"], record["id"], "sent", f"Sent to {record['emailid']} via {client['name']}")
            sent.append(record["id"])
        except Exception as exc:
            clear_send_errors(record)
            record.setdefault("errors", []).append(f"Send failed via {client['name']}: {exc}")
            record["updated_at"] = utc_now()
            failed.append({"record_id": record["id"], "recipient": display_recipient(record), "error": str(exc)})
            append_log(job["id"], record["id"], "send_failed", f"{client['name']}: {exc}")

    save_job(job)
    if failed and not sent:
        raise ValueError("; ".join(f"{item['recipient']}: {item['error']}" for item in failed))
    return {"sent": sent, "failed": failed, "client": client}


def send_via_outlook(job: dict[str, Any], record: dict[str, Any]) -> None:
    try:
        import pythoncom
        import win32com.client
    except Exception as exc:
        raise ValueError("pywin32 is required for Outlook desktop automation.") from exc

    build_email_message(job, record)
    pythoncom.CoInitialize()
    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        session = outlook.Session
        if getattr(session.Accounts, "Count", 0) < 1:
            raise ValueError("Outlook has no sending account configured.")
        mail = outlook.CreateItem(0)
        mail.To = record["emailid"]
        mail.CC = ", ".join(parse_email_list(record.get("cc", "")))
        mail.BCC = ", ".join(parse_email_list(record.get("bcc", "")))
        mail.Subject = record["subject"]
        mail.HTMLBody = record["body_html"]
        from_account = build_smtp_config(job.get("smtp", {}), include_password=False).get("from_email")
        if from_account:
            for account in session.Accounts:
                if str(account.SmtpAddress).lower() == from_account.lower():
                    mail.SendUsingAccount = account
                    break
        for attachment in attachment_paths(record):
            if not attachment.exists():
                raise ValueError(f"Attachment not found: {attachment}")
            mail.Attachments.Add(str(attachment))
        if not mail.Recipients.ResolveAll():
            raise ValueError("Outlook could not resolve one or more recipients.")
        try:
            mail.Send()
        except Exception as exc:
            raise RuntimeError(format_outlook_send_error(exc)) from exc
    finally:
        pythoncom.CoUninitialize()


def format_outlook_send_error(exc: Exception) -> str:
    message = str(exc)
    if "-2147467260" in message or "Operation aborted" in message:
        advice = "Outlook aborted the send operation."
        activation_hint = outlook_activation_hint()
        if activation_hint:
            advice += f" {activation_hint}"
        advice += " Open Outlook, confirm the mailbox can manually send a normal email, then retry. SMTP sending is the best fallback when Outlook blocks automation."
        return advice
    return message


def outlook_activation_hint() -> str:
    for title in visible_window_titles():
        if "outlook" in title.lower() and "activation failed" in title.lower():
            return "The Outlook window title shows 'Product Activation Failed', so Outlook is likely blocking send until Office is activated or signed in."
    return ""


def visible_window_titles() -> list[str]:
    titles: list[str] = []
    if os.name != "nt":
        return titles

    EnumWindows = ctypes.windll.user32.EnumWindows
    IsWindowVisible = ctypes.windll.user32.IsWindowVisible
    GetWindowTextLengthW = ctypes.windll.user32.GetWindowTextLengthW
    GetWindowTextW = ctypes.windll.user32.GetWindowTextW

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd, _):
        if IsWindowVisible(hwnd):
            length = GetWindowTextLengthW(hwnd)
            if length:
                buffer = ctypes.create_unicode_buffer(length + 1)
                GetWindowTextW(hwnd, buffer, length + 1)
                if buffer.value:
                    titles.append(buffer.value)
        return True

    EnumWindows(callback, None)
    return titles


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
    from_name = smtp_config.get("from_name") or "DeepMail Studio"

    message = EmailMessage()
    message["Subject"] = record["subject"]
    message["From"] = formataddr((from_name, from_email))
    message["To"] = record["emailid"]
    if parse_email_list(record.get("cc", "")):
        message["Cc"] = ", ".join(parse_email_list(record.get("cc", "")))
    if parse_email_list(record.get("bcc", "")):
        message["Bcc"] = ", ".join(parse_email_list(record.get("bcc", "")))
    message["Message-ID"] = make_msgid(domain="deepmailstudio.local")
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
        "from_name": clean(values.get("smtp_from_name") or values.get("from_name") or os.getenv("SMTP_FROM_NAME", "DeepMail Studio")),
        "security": clean(values.get("smtp_security") or values.get("security") or os.getenv("SMTP_SECURITY", "starttls")),
    }
    if include_password:
        config["password"] = password
    return config


def build_brand_config(values: dict[str, Any]) -> dict[str, str]:
    return {
        "name": clean(values.get("brand_name") or values.get("name") or ""),
        "voice": clean(values.get("brand_voice") or values.get("voice") or ""),
        "primary_color": clean(values.get("brand_primary_color") or values.get("primary_color") or "#166a5f"),
        "accent_color": clean(values.get("brand_accent_color") or values.get("accent_color") or "#b4462d"),
        "logo_url": clean(values.get("brand_logo_url") or values.get("logo_url") or ""),
        "cta_text": clean(values.get("brand_cta_text") or values.get("cta_text") or ""),
        "cta_url": clean(values.get("brand_cta_url") or values.get("cta_url") or ""),
        "footer": clean(values.get("brand_footer") or values.get("footer") or ""),
        "layout": clean(values.get("brand_layout") or values.get("layout") or "modern branded invitation"),
    }


def get_brand_config(job: dict[str, Any]) -> dict[str, str]:
    return build_brand_config(job.get("brand", {}))


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


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=7865, debug=True)
