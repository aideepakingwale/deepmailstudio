from __future__ import annotations

import html
import os
from typing import Any

import requests
from dotenv import dotenv_values

from .settings import BASE_DIR
from .utils import clean, sanitize_email_html


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

def build_sender_config(values: dict[str, Any], smtp_values: dict[str, Any] | None = None) -> dict[str, str]:
    smtp_values = smtp_values or values
    smtp_config = build_smtp_config(smtp_values, include_password=False)
    return {
        "name": clean(values.get("sender_name") or values.get("name") or smtp_config.get("from_name") or os.getenv("SENDER_NAME", "DeepMail Studio")),
        "email": clean(values.get("sender_email") or values.get("email") or smtp_config.get("from_email") or os.getenv("SENDER_EMAIL", "")),
        "title": clean(values.get("sender_title") or values.get("title") or os.getenv("SENDER_TITLE", "")),
        "organization": clean(values.get("sender_organization") or values.get("organization") or os.getenv("SENDER_ORGANIZATION", "")),
        "phone": clean(values.get("sender_phone") or values.get("phone") or os.getenv("SENDER_PHONE", "")),
        "website": clean(values.get("sender_website") or values.get("website") or os.getenv("SENDER_WEBSITE", "")),
        "signature_html": sanitize_email_html(clean(values.get("sender_signature_html") or values.get("signature_html") or os.getenv("SENDER_SIGNATURE_HTML", ""))),
    }

def get_sender_config(job: dict[str, Any]) -> dict[str, str]:
    return build_sender_config(job.get("sender", {}), job.get("smtp", {}))

def sender_signature_html(sender: dict[str, str], closing: str = "Regards") -> str:
    if sender.get("signature_html"):
        return sender["signature_html"]
    lines = [html.escape(sender.get("name") or get_setting("SMTP_FROM_NAME", "DeepMail Studio"))]
    if sender.get("title"):
        lines.append(html.escape(sender["title"]))
    if sender.get("organization"):
        lines.append(html.escape(sender["organization"]))
    contact = []
    if sender.get("email"):
        contact.append(html.escape(sender["email"]))
    if sender.get("phone"):
        contact.append(html.escape(sender["phone"]))
    if sender.get("website"):
        contact.append(html.escape(sender["website"]))
    if contact:
        lines.append(" | ".join(contact))
    return f"<p>{html.escape(closing)},<br>{'<br>'.join(lines)}</p>"

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
        "amazon_q": get_setting("AMAZON_Q_MODEL", "Amazon Q Developer CLI"),
        "copilot": get_setting("COPILOT_MODEL", "GitHub Copilot CLI default"),
        "openai": get_setting("OPENAI_MODEL", ""),
    }
    endpoint_by_provider = {
        "template": "local template engine",
        "ollama": get_setting("OLLAMA_BASE_URL", "http://localhost:11434"),
        "lmstudio": lmstudio_endpoint,
        "openai_compatible": get_setting("OPENAI_COMPATIBLE_BASE_URL", "http://localhost:1234/v1"),
        "groq": "https://api.groq.com/openai/v1",
        "gemini": "https://generativelanguage.googleapis.com/v1beta",
        "amazon_q": get_setting("AMAZON_Q_COMMAND", "q") + " chat --no-interactive",
        "copilot": get_setting("COPILOT_COMMAND", "copilot") + " -p ... -s",
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
        "amazon_q_model": get_setting("AMAZON_Q_MODEL", "Amazon Q Developer CLI"),
        "copilot_model": get_setting("COPILOT_MODEL", "GitHub Copilot CLI default"),
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
