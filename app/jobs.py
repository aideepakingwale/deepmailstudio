from __future__ import annotations

import uuid
from typing import Any

from .config import ai_config, build_brand_config, build_sender_config, build_smtp_config
from .settings import UPLOADS_DIR
from .sheets import load_records
from .storage import append_log, save_job, utc_now
from .utils import safe_filename


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
        "sheet_verified": False,
        "sheet_verified_at": "",
        "brand": build_brand_config(form),
        "smtp": build_smtp_config(form, include_password=True),
        "sender": build_sender_config(form),
        "ai": ai_config(),
        "records": records,
    }
    for record in job["records"]:
        record["prompt_mode"] = "append" if job["use_row_prompts"] and record.get("content_prompt") else "ignore"
    save_job(job)
    append_log(job_id, "-", "created", f"Loaded {len(records)} recipient rows")
    return job
