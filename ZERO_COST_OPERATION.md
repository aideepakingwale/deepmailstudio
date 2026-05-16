# Zero-Cost Operation Guide

This project is configured to avoid paid operating costs.

## AI Generation

Use one of these providers:

```text
AI_PROVIDER=template
```

No model, no API, no cost. This keeps the agent workflow fully functional.

```text
AI_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1
```

Runs a local model through Ollama. The model uses your laptop CPU/GPU only.

```text
AI_PROVIDER=lmstudio
LM_STUDIO_BASE_URL=http://localhost:1234/v1
LM_STUDIO_API_KEY=
LM_STUDIO_MODEL=local-model
```

Runs a local model through LM Studio's OpenAI-compatible local server. No API key is required for normal local LM Studio use.

```text
AI_PROVIDER=openai_compatible
OPENAI_COMPATIBLE_BASE_URL=http://localhost:1234/v1
OPENAI_COMPATIBLE_MODEL=local-model
OPENAI_COMPATIBLE_API_KEY=
```

Runs against a local OpenAI-compatible server such as LM Studio, vLLM, or LocalAI.

```text
AI_PROVIDER=groq
GROQ_API_KEY=your_groq_key
GROQ_MODEL=llama-3.1-8b-instant
```

Uses the Groq free plan API. Respect Groq free-plan rate limits.

```text
AI_PROVIDER=gemini
GEMINI_API_KEY=your_gemini_key
GEMINI_MODEL=gemini-2.5-flash-lite
```

Uses the Gemini API free tier. Respect Gemini free-tier rate limits.

## Cost Guardrails

```text
ZERO_COST_MODE=true
AI_FALLBACK_TO_TEMPLATE=true
```

`ZERO_COST_MODE=true` blocks paid OpenAI API generation while allowing template, Ollama, LM Studio, local OpenAI-compatible servers, Groq free tier, and Gemini free tier.

`AI_FALLBACK_TO_TEMPLATE=true` keeps generation working if Ollama or LM Studio is not running.

## Email Sending

Use an existing mailbox/free SMTP provider. This avoids extra platform cost, but free providers often limit sending volume. For large campaigns, send in small batches and respect anti-spam rules.

If you do not want to configure SMTP, click **Create Draft**. The app creates local `.eml` files at zero cost.
