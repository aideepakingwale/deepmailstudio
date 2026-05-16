from __future__ import annotations

import ctypes
import os
import shutil
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path
from typing import Any

try:
    import winreg
except ImportError:
    winreg = None

from .config import build_smtp_config, get_sender_config
from .settings import DRAFTS_DIR
from .storage import append_log, find_record, save_job, utc_now
from .utils import (
    attachment_paths,
    clear_send_errors,
    display_recipient,
    guess_mime,
    parse_email_list,
    safe_filename,
    strip_tags,
)


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
    sender = get_sender_config(job)
    from_email = smtp_config.get("from_email") or sender.get("email") or smtp_config.get("username") or "local-agent@example.local"
    from_name = smtp_config.get("from_name") or sender.get("name") or "DeepMail Studio"

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
