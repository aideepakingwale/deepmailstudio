const shell = document.querySelector(".job-shell");
const jobId = shell?.dataset.jobId;

let job = null;
let selectedId = null;
let pollTimer = null;

const els = {
    recipientList: document.getElementById("recipientList"),
    emptyState: document.getElementById("emptyState"),
    editorState: document.getElementById("editorState"),
    recipientName: document.getElementById("recipientName"),
    recipientMeta: document.getElementById("recipientMeta"),
    statusBadge: document.getElementById("statusBadge"),
    errorBox: document.getElementById("errorBox"),
    notesBox: document.getElementById("notesBox"),
    subjectInput: document.getElementById("subjectInput"),
    bodyEditor: document.getElementById("bodyEditor"),
    generateBtn: document.getElementById("generateBtn"),
    approveBtn: document.getElementById("approveBtn"),
    draftBtn: document.getElementById("draftBtn"),
    sendBtn: document.getElementById("sendBtn"),
    refreshBtn: document.getElementById("refreshBtn"),
    saveContextBtn: document.getElementById("saveContextBtn"),
    jobContextInput: document.getElementById("jobContextInput"),
    useRowPromptsInput: document.getElementById("useRowPromptsInput"),
    refreshEventsBtn: document.getElementById("refreshEventsBtn"),
    eventList: document.getElementById("eventList"),
    countPending: document.getElementById("countPending"),
    countReady: document.getElementById("countReady"),
    countSent: document.getElementById("countSent"),
    detailEmail: document.getElementById("detailEmail"),
    detailCc: document.getElementById("detailCc"),
    detailBcc: document.getElementById("detailBcc"),
    detailLanguage: document.getElementById("detailLanguage"),
    detailTone: document.getElementById("detailTone"),
    detailLength: document.getElementById("detailLength"),
    detailPrompt: document.getElementById("detailPrompt"),
    detailAttachments: document.getElementById("detailAttachments"),
    detailGeneration: document.getElementById("detailGeneration"),
};

async function api(path, options = {}) {
    const response = await fetch(path, {
        cache: "no-store",
        headers: { "Content-Type": "application/json", "Cache-Control": "no-store", ...(options.headers || {}) },
        ...options,
    });
    const payload = await response.json();
    if (!response.ok || payload.ok === false) {
        throw new Error(payload.error || "Request failed");
    }
    return payload;
}

async function loadJob() {
    const response = await fetch(`/api/jobs/${jobId}`, { cache: "no-store" });
    job = await response.json();
    if (!selectedId && job.records.length) {
        selectedId = job.records[0].id;
    }
    render();
    loadEvents();
}

async function loadEvents() {
    if (!els.eventList) return;
    const response = await fetch(`/api/jobs/${jobId}/events?t=${Date.now()}`, { cache: "no-store" });
    const payload = await response.json();
    els.eventList.innerHTML = "";
    if (!payload.events.length) {
        els.eventList.innerHTML = "<p>No events recorded for this job yet.</p>";
        return;
    }
    payload.events.slice().reverse().forEach((event) => {
        const row = document.createElement("div");
        row.className = "event-row";
        row.innerHTML = `
            <strong>${escapeHtml(event.event || "")}</strong>
            <small>${escapeHtml(event.timestamp || "")} | record ${escapeHtml(event.record_id || "-")}</small>
            <span>${escapeHtml(event.details || "")}</span>
        `;
        els.eventList.appendChild(row);
    });
}

function render() {
    renderCounts();
    renderRecipientList();
    renderSelected();
}

function renderCounts() {
    const pending = job.records.filter((r) => ["pending", "invalid", "failed", "queued", "generating"].includes(r.status)).length;
    const ready = job.records.filter((r) => ["generated", "approved", "drafted"].includes(r.status)).length;
    const sent = job.records.filter((r) => r.status === "sent").length;
    els.countPending.textContent = pending;
    els.countReady.textContent = ready;
    els.countSent.textContent = sent;
}

function renderRecipientList() {
    els.recipientList.innerHTML = "";
    job.records.forEach((record) => {
        const button = document.createElement("button");
        button.className = `recipient-item ${record.id === selectedId ? "active" : ""}`;
        button.innerHTML = `
            <strong>${escapeHtml(displayName(record))}</strong>
            <small>${escapeHtml(record.emailid || "No email")}</small>
            <span class="mini-status">${escapeHtml(record.status)}</span>
        `;
        button.addEventListener("click", () => {
            selectedId = record.id;
            render();
        });
        els.recipientList.appendChild(button);
    });
}

function renderSelected() {
    const record = currentRecord();
    if (!record) {
        els.emptyState.classList.remove("hidden");
        els.editorState.classList.add("hidden");
        return;
    }
    els.emptyState.classList.add("hidden");
    els.editorState.classList.remove("hidden");

    els.recipientName.textContent = displayName(record);
    els.recipientMeta.textContent = `${record.emailid} | ${record.language} | ${record.email_tone} | ${record.content_length}`;
    els.statusBadge.textContent = record.status;
    els.statusBadge.className = `badge ${record.status}`;
    els.subjectInput.value = record.subject || "";
    els.bodyEditor.innerHTML = record.body_html || "<p></p>";

    showBox(els.errorBox, (record.errors || []).join(" "));
    showBox(els.notesBox, record.quality_notes || "");

    els.detailEmail.textContent = record.emailid || "-";
    els.detailCc.textContent = record.cc || "-";
    els.detailBcc.textContent = record.bcc || "-";
    els.detailLanguage.textContent = record.language || "English";
    els.detailTone.textContent = record.email_tone || "friendly";
    els.detailLength.textContent = record.content_length || "medium";
    els.detailPrompt.textContent = record.content_prompt || "-";
    els.detailAttachments.textContent = record.attachments || "-";
    els.detailGeneration.textContent = generationSummary(record);

    const activeGeneration = ["queued", "generating"].includes(record.status);
    els.generateBtn.textContent = activeGeneration ? "Working..." : (record.subject || record.body_html ? "Regenerate" : "Generate");
    els.generateBtn.disabled = record.status === "invalid" || activeGeneration;
    els.approveBtn.disabled = record.status === "invalid" || record.status === "sent";
    els.draftBtn.disabled = record.status === "invalid" || record.status === "sent";
    els.sendBtn.disabled = record.status === "invalid" || record.status === "sent";
    updatePolling();
}

function showBox(element, text) {
    element.textContent = text;
    element.classList.toggle("hidden", !text);
}

function currentRecord() {
    return job?.records.find((record) => record.id === selectedId);
}

function displayName(record) {
    return [record.salutation, record.first_name, record.surname].filter(Boolean).join(" ") || "Unnamed recipient";
}

function escapeHtml(value) {
    const div = document.createElement("div");
    div.textContent = value ?? "";
    return div.innerHTML;
}

function generationSummary(record) {
    const parts = [];
    if (record.generation_attempts) parts.push(`${record.generation_attempts} attempt(s)`);
    if (record.last_generation_request_id) parts.push(`request ${record.last_generation_request_id}`);
    if (record.status === "queued") parts.push("queued");
    if (record.status === "generating") parts.push("generating");
    if (record.last_generation_completed_at) parts.push(`completed ${record.last_generation_completed_at}`);
    else if (record.last_generation_started_at) parts.push(`started ${record.last_generation_started_at}`);
    return parts.join(" | ") || "-";
}

function updatePolling() {
    const hasActive = job?.records.some((record) => ["queued", "generating"].includes(record.status));
    if (hasActive && !pollTimer) {
        pollTimer = setInterval(loadJob, 4000);
    } else if (!hasActive && pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
    }
}

async function setBusy(button, label, action) {
    const original = button.textContent;
    button.disabled = true;
    button.textContent = label;
    try {
        await action();
    } catch (error) {
        showBox(els.errorBox, error.message);
    } finally {
        button.textContent = original;
        button.disabled = false;
    }
}

async function persistReview(approved = false) {
    const record = currentRecord();
    const payload = {
        subject: els.subjectInput.value,
        body_html: els.bodyEditor.innerHTML,
        approved,
    };
    await api(`/api/jobs/${jobId}/records/${record.id}/save`, {
        method: "POST",
        body: JSON.stringify(payload),
    });
    await loadJob();
}

els.refreshBtn?.addEventListener("click", loadJob);
els.refreshEventsBtn?.addEventListener("click", loadEvents);

els.saveContextBtn?.addEventListener("click", () => setBusy(els.saveContextBtn, "Saving...", async () => {
    await api(`/api/jobs/${jobId}/context`, {
        method: "POST",
        body: JSON.stringify({
            context_prompt: els.jobContextInput.value,
            use_row_prompts: els.useRowPromptsInput.checked,
        }),
    });
    await loadJob();
    showBox(els.notesBox, "Campaign context settings saved. Click Generate again to regenerate with the updated context.");
}));

els.generateBtn?.addEventListener("click", () => setBusy(els.generateBtn, "Generating...", async () => {
    const record = currentRecord();
    await api(`/api/jobs/${jobId}/records/${record.id}/generate?t=${Date.now()}`, { method: "POST" });
    await loadJob();
    showBox(els.notesBox, "Generation queued. The page will refresh while the local LLM works in the background.");
}));

els.approveBtn?.addEventListener("click", () => setBusy(els.approveBtn, "Saving...", async () => {
    await persistReview(true);
}));

els.draftBtn?.addEventListener("click", () => setBusy(els.draftBtn, "Drafting...", async () => {
    await persistReview(true);
    const record = currentRecord();
    const result = await api(`/api/jobs/${jobId}/records/${record.id}/draft`, { method: "POST" });
    await loadJob();
    if (result.draft_path) {
        showBox(els.notesBox, `Draft created: ${result.draft_path}`);
    }
}));

els.sendBtn?.addEventListener("click", () => setBusy(els.sendBtn, "Sending...", async () => {
    const ok = confirm("Send this email now using the configured SMTP settings?");
    if (!ok) return;
    await persistReview(true);
    const record = currentRecord();
    await api(`/api/jobs/${jobId}/records/${record.id}/send`, {
        method: "POST",
        body: JSON.stringify({}),
    });
    await loadJob();
}));

document.querySelectorAll("[data-command]").forEach((button) => {
    button.addEventListener("click", () => {
        document.execCommand(button.dataset.command, false, null);
        els.bodyEditor.focus();
    });
});

if (jobId) {
    loadJob();
}
