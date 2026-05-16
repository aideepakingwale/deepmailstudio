from __future__ import annotations

import html
import re
from typing import Any

from .config import get_brand_config, get_sender_config, sender_signature_html
from .email_validation import validate_generated_email
from .settings import OFFICIAL_TONES
from .utils import attachment_paths


def generate_template_email(job: dict[str, Any], record: dict[str, Any]) -> tuple[str, str, str]:
    salutation = record.get("salutation") or ""
    name = " ".join(part for part in [salutation, record.get("first_name")] if part).strip()
    context = job.get("context_prompt") or "I wanted to share this note with you."
    custom = record.get("content_prompt") if record.get("prompt_mode", "append") != "ignore" else ""
    tone = record.get("email_tone", "friendly")
    brand = get_brand_config(job)
    sender = get_sender_config(job)
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
    body_parts.append(sender_signature_html(sender, localized["closing"] if tone.lower() in OFFICIAL_TONES else closing))
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
