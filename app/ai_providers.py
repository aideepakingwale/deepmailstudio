from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import requests

from .config import get_lmstudio_model, get_setting, is_zero_cost_mode
from .settings import BASE_DIR


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

def generate_with_amazon_q_cli(prompt: str) -> str:
    command = resolve_local_command(get_setting("AMAZON_Q_COMMAND", "q"), ["q.exe", "q.cmd"])
    if not command:
        raise ValueError("Amazon Q Developer CLI was not found. Install/sign in to Amazon Q CLI or choose another AI_PROVIDER.")
    timeout = int(get_setting("AMAZON_Q_TIMEOUT_SECONDS", get_setting("AI_REQUEST_TIMEOUT_SECONDS", "600")) or "600")
    model = get_setting("AMAZON_Q_MODEL", "").strip()
    cmd = [command, "chat", "--no-interactive"]
    if model:
        cmd.extend(["--model", model])
    cmd.append(prompt)
    return run_local_ai_command(cmd, timeout, "Amazon Q Developer CLI")

def generate_with_copilot_cli(prompt: str) -> str:
    command = resolve_local_command(get_setting("COPILOT_COMMAND", "copilot"), ["copilot.exe", "copilot.cmd"])
    if not command:
        raise ValueError("GitHub Copilot CLI was not found. Install/sign in to Copilot CLI or choose another AI_PROVIDER.")
    timeout = int(get_setting("COPILOT_TIMEOUT_SECONDS", get_setting("AI_REQUEST_TIMEOUT_SECONDS", "600")) or "600")
    model = get_setting("COPILOT_MODEL", "").strip()
    cmd = [command, "-p", prompt, "-s", "--no-ask-user"]
    if model:
        cmd.extend(["--model", model])
    return run_local_ai_command(cmd, timeout, "GitHub Copilot CLI")

def resolve_local_command(command: str, extra_names: list[str]) -> str:
    if not command:
        return ""
    expanded = os.path.expandvars(command)
    if Path(expanded).exists():
        return expanded
    found = shutil.which(command)
    if found:
        return found
    search_dirs = [
        Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links",
        Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps",
        Path(os.getenv("APPDATA", "")) / "npm",
    ]
    names = [command, *extra_names]
    for folder in search_dirs:
        if not str(folder) or not folder.exists():
            continue
        for name in names:
            candidate = folder / name
            if candidate.exists():
                return str(candidate)
    return ""

def run_local_ai_command(cmd: list[str], timeout: int, label: str) -> str:
    completed = subprocess.run(
        cmd,
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    output = (completed.stdout or "").strip()
    error = (completed.stderr or "").strip()
    if completed.returncode != 0:
        raise ValueError(f"{label} failed with exit code {completed.returncode}: {error or output}")
    if not output:
        raise ValueError(f"{label} returned empty output. {error}")
    return output

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
