from __future__ import annotations

from typing import Any

from .ai_parsing import parse_model_response
from .ai_providers import (
    generate_with_amazon_q_cli,
    generate_with_copilot_cli,
    generate_with_gemini,
    generate_with_groq,
    generate_with_lmstudio,
    generate_with_ollama,
    generate_with_openai,
    generate_with_openai_compatible,
)
from .config import (
    get_brand_config,
    get_sender_config,
    get_setting,
    is_zero_cost_mode,
    should_fallback_to_template,
)
from .email_templates import generate_template_email
from .email_validation import (
    language_rules,
    polish_html,
    validate_context_alignment,
    validate_generated_email,
    validate_language_requirement,
)
from .utils import attachment_paths, sanitize_email_html, strip_tags


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
        elif provider == "amazon_q":
            text = generate_with_amazon_q_cli(prompt)
        elif provider == "copilot":
            text = generate_with_copilot_cli(prompt)
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
    prompt_mode = (record.get("prompt_mode") or ("append" if use_row_prompts else "ignore")).lower()
    if prompt_mode not in {"append", "enhance", "override", "ignore"}:
        prompt_mode = "append" if use_row_prompts else "ignore"
    row_prompt = record.get("content_prompt") if prompt_mode != "ignore" else ""
    mode_instruction = {
        "append": "Append the recipient-specific prompt as additional instructions after applying the campaign context.",
        "enhance": "Use the recipient-specific prompt to enrich personalization and wording while preserving the campaign context.",
        "override": "For this recipient only, the recipient-specific prompt may override the generic campaign context where they conflict.",
        "ignore": "Ignore the recipient-specific prompt for this recipient.",
    }[prompt_mode]
    brand = get_brand_config(job)
    sender = get_sender_config(job)
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

Sender profile:
- Sender name: {sender["name"] or "Not specified"}
- Sender email: {sender["email"] or "Not specified"}
- Sender title/role: {sender["title"] or "Not specified"}
- Sender organization: {sender["organization"] or brand["name"] or "Not specified"}
- Sender phone: {sender["phone"] or "Not specified"}
- Sender website: {sender["website"] or "Not specified"}
- Signature instruction/content: {strip_tags(sender["signature_html"]) or "Use a concise natural sign-off from the sender profile."}

Recipient:
- Salutation: {record.get("salutation") or "not specified"}
- Name: {full_name}
- Email: {record.get("emailid")}
- Language: {language}
- Tone: {record.get("email_tone") or "friendly"}
- Desired length: {record.get("content_length") or "medium"}
- Attachments/images referenced: {attachment_names}
- Recipient prompt mode: {prompt_mode}
- Recipient-specific prompt: {row_prompt or "None"}

Requirements:
- Draft like a human wrote it for this recipient.
- The authoritative campaign context is the main event/topic and must not be replaced.
- Recipient prompt handling: {mode_instruction}
- Apply recipient-specific prompts only according to the selected prompt mode.
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
- End with a natural sender signature using the Sender profile. If signature content is provided, use it as the authoritative sign-off/signature block.
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
