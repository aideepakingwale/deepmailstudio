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

Amazon Q Developer CLI, using the locally signed-in CLI:

```text
AI_PROVIDER=amazon_q
AMAZON_Q_COMMAND=q
AMAZON_Q_MODEL=
AMAZON_Q_TIMEOUT_SECONDS=600
```

GitHub Copilot CLI, using the locally signed-in Copilot CLI:

```text
AI_PROVIDER=copilot
COPILOT_COMMAND=copilot
COPILOT_MODEL=
COPILOT_TIMEOUT_SECONDS=600
```

### Install GitHub Copilot CLI

Official install guide: [Installing GitHub Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/install-copilot-cli).

Prerequisites:

- Active GitHub Copilot access.
- On Windows, PowerShell v6 or higher.

Install on Windows with WinGet:

```powershell
winget install GitHub.Copilot
```

Alternative install with npm on any platform, requiring Node.js 22 or later:

```powershell
npm install -g @github/copilot
```

Authenticate after installation:

```powershell
copilot
```

If prompted, run `/login` inside Copilot CLI and complete the GitHub sign-in flow. Then configure DeepMail Studio with `AI_PROVIDER=copilot`.

Verify non-interactive generation works:

```powershell
copilot -p "Reply with exactly: COPILOT_OK" -s --no-ask-user
```

If PowerShell cannot find `copilot` after a WinGet install, restart PowerShell or point DeepMail Studio directly to the WinGet link:

```text
COPILOT_COMMAND=%LOCALAPPDATA%\Microsoft\WinGet\Links\copilot.exe
```

### Install Amazon Q Developer CLI

AWS Developer Center: [Amazon Q Developer for CLI](https://aws.amazon.com/developer/learning/q-developer-cli/).  
Amazon Q command-line chat docs: [Using chat on the command line](https://docs.aws.amazon.com/en_us/amazonq/latest/qdeveloper-ug/command-line-chat.html).

Notes:

- DeepMail Studio expects the `q` command to be available in PATH.
- AWS documentation currently notes that Q CLI has moved toward Kiro CLI in some contexts. Use the Amazon Q Developer CLI/Kiro CLI package that provides the `q` command, or set `AMAZON_Q_COMMAND` to the installed command name/path.
- Sign in with AWS Builder ID or the authentication method supported by your installation.

Install/download from the AWS Developer Center:

1. Open [Amazon Q Developer for CLI](https://aws.amazon.com/developer/learning/q-developer-cli/).
2. Download the installer for your OS.
3. Install it and restart PowerShell/terminal so PATH is refreshed.
4. Confirm the command is available:

```powershell
q --help
```

Start and authenticate:

```powershell
q chat
```

After sign-in works locally, configure DeepMail Studio:

```text
AI_PROVIDER=amazon_q
AMAZON_Q_COMMAND=q
AMAZON_Q_MODEL=
AMAZON_Q_TIMEOUT_SECONDS=600
```

DeepMail Studio invokes Amazon Q non-interactively with `q chat --no-interactive`.

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

## Sender Profile

Sender name, email, title, organization, contact details, and signature can be configured when creating a campaign or edited later from **Campaign Settings**. The AI uses this profile while generating the closing/signature, and outgoing drafts/messages use the sender name/email where applicable.

Optional `.env` defaults:

```text
SENDER_NAME=Your Name
SENDER_EMAIL=you@example.com
SENDER_TITLE=Project Lead
SENDER_ORGANIZATION=Your Company
SENDER_PHONE=
SENDER_WEBSITE=
SENDER_SIGNATURE_HTML=
```

## Local Email Client Sending

DeepMail Studio detects local desktop mail clients from Windows app paths. Automatic local-client sending is supported for classic Microsoft Outlook through COM automation. SMTP remains available as a mailbox-based automatic sending option when configured.

The campaign workbench can send the currently reviewed approved email, select all approved rows, or send selected approved/drafted rows in one go. Other detected clients, such as New Outlook or Thunderbird, are shown for visibility but disabled for automatic sending unless they expose a safe local automation API.

## Agent Flow

1. Upload a sheet and provide the global email context.
2. The agent validates every row and attachment path.
3. By default, the agent auto-starts generation for all valid rows. You can turn this off before creating the job.
4. Review and edit the generated subject/body in the WYSIWYG editor.
5. Click **Approve**.
6. Choose a detected sending option, then click **Send** for one email or **Send Selected** for approved selected rows. You can also create `.eml` drafts.
7. The job state and event log are saved locally.

## Local Data

- Jobs: `data/jobs`
- Uploaded sheets: `data/uploads`
- Drafts: `drafts`
- Event log: `logs/agent_events.csv`
