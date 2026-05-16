from __future__ import annotations

from flask import Flask, jsonify, redirect, render_template, request, send_file, url_for

from .config import ai_config, build_brand_config, build_sender_config, build_smtp_config, get_sender_config, smtp_defaults
from .generation_queue import enqueue_all_pending_records, enqueue_generation, start_generation_worker
from .jobs import create_job
from .mailers import default_mail_client_id, detect_mail_clients, send_email, send_selected_with_client, write_eml_draft
from .settings import BASE_DIR, DRAFTS_DIR, JOBS_DIR
from .sheets import update_recipient_config, validate_record
from .storage import append_log, find_record, load_job, mark_failed, read_job_events, save_job, utc_now
from .utils import clear_send_errors


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
        return redirect(url_for("verify_recipients_view", job_id=job["id"]))

    @app.get("/jobs/<job_id>")
    def job_view(job_id: str):
        job = load_job(job_id)
        return render_template("job.html", job=job, defaults=smtp_defaults(), ai=ai_config(), sender_defaults=get_sender_config(job))

    @app.get("/jobs/<job_id>/verify")
    def verify_recipients_view(job_id: str):
        job = load_job(job_id)
        return render_template("verify.html", job=job, ai=ai_config())

    @app.get("/api/jobs/<job_id>")
    def get_job(job_id: str):
        return jsonify(load_job(job_id))

    @app.get("/api/jobs/<job_id>/events")
    def get_job_events(job_id: str):
        return jsonify({"events": read_job_events(job_id)})

    @app.post("/api/jobs/<job_id>/records/<record_id>/config")
    def update_record_config(job_id: str, record_id: str):
        job = load_job(job_id)
        record = find_record(job, record_id)
        payload = request.get_json(force=True)
        update_recipient_config(record, payload)
        record["errors"] = validate_record(record)
        if record["errors"]:
            record["status"] = "invalid"
            record["row_verified"] = False
        elif record["status"] == "invalid":
            record["status"] = "pending"
        if not record["errors"] and payload.get("row_verified"):
            record["row_verified"] = True
        record["updated_at"] = utc_now()
        append_log(job_id, record_id, "recipient_config_updated", "Recipient row reviewed/edited before generation")
        save_job(job)
        return jsonify({"ok": True, "record": record, "job": job})

    @app.post("/api/jobs/<job_id>/verify")
    def verify_recipients(job_id: str):
        job = load_job(job_id)
        payload = request.get_json(silent=True) or {}
        auto_start = bool(payload.get("auto_start", job.get("auto_start_generation", True)))
        invalid_count = 0
        for record in job.get("records", []):
            record["errors"] = validate_record(record)
            if record["errors"]:
                record["status"] = "invalid"
                record["row_verified"] = False
                invalid_count += 1
            elif record.get("status") == "invalid":
                record["status"] = "pending"
            else:
                record["row_verified"] = True
        if invalid_count:
            save_job(job)
            return jsonify({"ok": False, "error": f"Fix {invalid_count} invalid row(s) before continuing.", "invalid_count": invalid_count}), 400
        job["sheet_verified"] = True
        job["sheet_verified_at"] = utc_now()
        append_log(job_id, "-", "sheet_verified", f"Human verified recipient sheet; invalid rows={invalid_count}")
        save_job(job)
        queued = 0
        if auto_start:
            queued = enqueue_all_pending_records(load_job(job_id))
            append_log(job_id, "-", "verified_auto_start_generation", f"Queued {queued} valid recipient rows after sheet verification")
        return jsonify({"ok": True, "queued": queued, "invalid_count": invalid_count, "redirect_url": url_for("job_view", job_id=job_id)})

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
        job["sender"] = build_sender_config(payload.get("sender", {}), job.get("smtp", {}))
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


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=7865, debug=True)
