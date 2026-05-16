# DeepMail Studio

A locally running, zero-cost agentic email automation studio for Windows desktops. It reads a recipient Excel/CSV sheet, generates personalized brand-ready email content with a local/free AI provider, pauses for human WYSIWYG review, then creates `.eml` drafts or sends via an existing SMTP mailbox with attachments.

## Quick Start

```powershell
.\run.ps1
```

Open:

```text
http://127.0.0.1:7865
```

Download the sample sheet from the app or use `sample_recipients.csv`.

## Recipient Sheet Columns

- `Salutation`
- `First Name`
- `Surname`
- `emailid`
- `cc`
- `bcc`
- `Content Prompt`
- `Language`
- `email Tone`
- `Content lenghth` or `Content length`
- `Attachment`

Use comma-separated addresses for `cc` and `bcc`. Use `;` or `,` separated absolute file paths for `Attachment`.

## Zero-Cost AI Options

Edit `.env`.

Zero-cost protection is on by default. It allows local providers plus Groq/Gemini free-tier providers:

```text
ZERO_COST_MODE=true
AI_FALLBACK_TO_TEMPLATE=true
```

Template mode, no model required and no external calls:

```text
AI_PROVIDER=template
```

Ollama local model:

```text
AI_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1
```

LM Studio local model:

```text
AI_PROVIDER=lmstudio
LM_STUDIO_BASE_URL=http://localhost:1234/v1
LM_STUDIO_API_KEY=
LM_STUDIO_MODEL=local-model
```

OpenAI-compatible local server such as LM Studio:

```text
AI_PROVIDER=openai_compatible
OPENAI_COMPATIBLE_BASE_URL=http://localhost:1234/v1
OPENAI_COMPATIBLE_MODEL=local-model
OPENAI_COMPATIBLE_API_KEY=
```

Groq free plan:

```text
AI_PROVIDER=groq
GROQ_API_KEY=your_groq_key
GROQ_MODEL=llama-3.1-8b-instant
```

Gemini API free tier:

```text
AI_PROVIDER=gemini
GEMINI_API_KEY=your_gemini_key
GEMINI_MODEL=gemini-2.5-flash-lite
```

Paid OpenAI API usage is intentionally disabled while `ZERO_COST_MODE=true`.

## Zero-Cost Email Sending

SMTP can be entered in the UI for a job or set in `.env`:

```text
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=you@example.com
SMTP_PASSWORD=app_password_or_smtp_password
SMTP_FROM_EMAIL=you@example.com
SMTP_FROM_NAME=Your Name
SMTP_SECURITY=starttls
```

Use an existing mailbox/free SMTP provider so there is no extra operating cost. Free providers have daily/hourly sending limits, so start with small batches. If SMTP is not configured, use **Create Draft**. The app writes `.eml` files into the `drafts` folder, which can be opened in desktop email clients.

## Agent Flow

1. Upload a sheet and provide the global email context.
2. The agent validates every row and attachment path.
3. By default, the agent auto-starts generation for all valid rows. You can turn this off before creating the job.
4. Review and edit the generated subject/body in the WYSIWYG editor.
5. Click **Approve**.
6. Click **Send SMTP** or **Create Draft**.
7. The job state and event log are saved locally.

## Local Data

- Jobs: `data/jobs`
- Uploaded sheets: `data/uploads`
- Drafts: `drafts`
- Event log: `logs/agent_events.csv`
