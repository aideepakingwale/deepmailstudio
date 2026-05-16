const verifyShell = document.querySelector(".verify-shell");
const verifyJobId = verifyShell?.dataset.jobId;

let verifyJob = null;
let verifySelectedId = null;
const verifiedRows = new Set();

const v = {
    list: document.getElementById("verifyRecipientList"),
    summary: document.getElementById("verifySummary"),
    progressText: document.getElementById("verifyProgressText"),
    progressFill: document.getElementById("verifyProgressFill"),
    name: document.getElementById("verifyRecipientName"),
    meta: document.getElementById("verifyRecipientMeta"),
    badge: document.getElementById("verifyStatusBadge"),
    error: document.getElementById("verifyErrorBox"),
    notes: document.getElementById("verifyNotesBox"),
    salutation: document.getElementById("verifySalutation"),
    firstName: document.getElementById("verifyFirstName"),
    surname: document.getElementById("verifySurname"),
    email: document.getElementById("verifyEmail"),
    cc: document.getElementById("verifyCc"),
    bcc: document.getElementById("verifyBcc"),
    language: document.getElementById("verifyLanguage"),
    tone: document.getElementById("verifyTone"),
    length: document.getElementById("verifyLength"),
    attachments: document.getElementById("verifyAttachments"),
    chooseAttachmentsBtn: document.getElementById("chooseAttachmentsBtn"),
    attachmentPicker: document.getElementById("verifyAttachmentPicker"),
    promptMode: document.getElementById("verifyPromptMode"),
    promptModeHelp: document.getElementById("promptModeHelp"),
    promptEditor: document.getElementById("verifyPromptEditor"),
    saveBtn: document.getElementById("saveRecipientConfigBtn"),
    markBtn: document.getElementById("markVerifiedBtn"),
    continueBtn: document.getElementById("continueGenerationBtn"),
    clearPromptFormattingBtn: document.getElementById("clearPromptFormattingBtn"),
};

const promptModeText = {
    append: "Append keeps the campaign context authoritative and adds recipient-specific instructions.",
    enhance: "Enhance uses this prompt to personalize language, emphasis, and details without changing the campaign topic.",
    override: "Override allows this recipient prompt to replace conflicting generic campaign details for this row.",
    ignore: "Ignore excludes this row prompt from generation.",
};

async function verifyApi(path, options = {}) {
    const response = await fetch(path, {
        cache: "no-store",
        headers: { "Content-Type": "application/json", ...(options.headers || {}) },
        ...options,
    });
    const payload = await response.json();
    if (!response.ok || payload.ok === false) {
        throw new Error(payload.error || "Request failed");
    }
    return payload;
}

async function loadVerifyJob() {
    const response = await fetch(`/api/jobs/${verifyJobId}`, { cache: "no-store" });
    verifyJob = await response.json();
    verifiedRows.clear();
    verifyJob.records.forEach((record) => {
        if (record.row_verified) verifiedRows.add(record.id);
    });
    if (!verifySelectedId && verifyJob.records.length) {
        verifySelectedId = verifyJob.records[0].id;
    }
    renderVerify();
}

function renderVerify() {
    renderVerifyCounts();
    renderVerifyList();
    renderVerifySelected();
}

function renderVerifyCounts() {
    const total = verifyJob.records.length;
    const invalid = verifyJob.records.filter((record) => record.status === "invalid").length;
    const verified = verifiedRows.size;
    const percent = total ? Math.round((verified / total) * 100) : 0;
    v.summary.textContent = `${verified} verified, ${invalid} invalid`;
    v.progressText.textContent = `${percent}%`;
    v.progressFill.style.width = `${percent}%`;
    v.continueBtn.disabled = invalid > 0;
}

function renderVerifyList() {
    v.list.innerHTML = "";
    verifyJob.records.forEach((record) => {
        const button = document.createElement("button");
        button.className = `recipient-item ${record.id === verifySelectedId ? "active" : ""}`;
        const isVerified = verifiedRows.has(record.id);
        button.innerHTML = `
            <span class="recipient-line">
                <span class="verify-dot ${isVerified ? "done" : record.status === "invalid" ? "invalid" : ""}"></span>
                <span>
                    <strong>${escapeHtml(verifyDisplayName(record))}</strong>
                    <small>${escapeHtml(record.emailid || "No email")}</small>
                </span>
            </span>
            <span class="mini-status ${escapeHtml(record.status)}">${isVerified ? "verified" : escapeHtml(record.status)}</span>
        `;
        button.addEventListener("click", () => {
            verifySelectedId = record.id;
            renderVerify();
        });
        v.list.appendChild(button);
    });
}

function renderVerifySelected() {
    const record = currentVerifyRecord();
    if (!record) return;
    v.name.textContent = verifyDisplayName(record);
    v.meta.textContent = `${record.emailid || "No email"} | ${record.language || "English"} | ${record.email_tone || "friendly"} | ${record.content_length || "medium"}`;
    v.badge.textContent = verifiedRows.has(record.id) ? "verified" : record.status;
    v.badge.className = `badge ${record.status}`;
    v.salutation.value = record.salutation || "";
    v.firstName.value = record.first_name || "";
    v.surname.value = record.surname || "";
    v.email.value = record.emailid || "";
    v.cc.value = record.cc || "";
    v.bcc.value = record.bcc || "";
    v.language.value = record.language || "English";
    v.tone.value = record.email_tone || "friendly";
    v.length.value = record.content_length || "medium";
    v.attachments.value = record.attachments || "";
    v.promptMode.value = record.prompt_mode || (record.content_prompt ? "append" : "ignore");
    v.promptModeHelp.textContent = promptModeText[v.promptMode.value] || promptModeText.append;
    v.promptEditor.innerText = record.content_prompt || "";
    showVerifyBox(v.error, (record.errors || []).join(" "));
    showVerifyBox(v.notes, record.status === "invalid" ? "Fix the highlighted row before continuing." : "Review the sheet values, adjust prompt handling, then save or mark verified.");
}

function currentVerifyRecord() {
    return verifyJob?.records.find((record) => record.id === verifySelectedId);
}

function collectVerifyPayload() {
    return {
        salutation: v.salutation.value,
        first_name: v.firstName.value,
        surname: v.surname.value,
        emailid: v.email.value,
        cc: v.cc.value,
        bcc: v.bcc.value,
        language: v.language.value,
        email_tone: v.tone.value,
        content_length: v.length.value,
        attachments: v.attachments.value,
        prompt_mode: v.promptMode.value,
        content_prompt: v.promptEditor.innerText.trim(),
    };
}

async function saveVerifyRow(markVerified = false) {
    const record = currentVerifyRecord();
    const payload = collectVerifyPayload();
    payload.row_verified = markVerified || verifiedRows.has(record.id);
    const result = await verifyApi(`/api/jobs/${verifyJobId}/records/${record.id}/config`, {
        method: "POST",
        body: JSON.stringify(payload),
    });
    verifyJob = result.job;
    verifiedRows.clear();
    verifyJob.records.forEach((item) => {
        if (item.row_verified) verifiedRows.add(item.id);
    });
    const updated = verifyJob.records.find((item) => item.id === record.id);
    if (markVerified && updated && updated.status !== "invalid") {
        verifiedRows.add(record.id);
    }
    renderVerify();
}

async function withBusy(button, label, action) {
    const original = button.textContent;
    button.disabled = true;
    button.textContent = label;
    try {
        await action();
    } catch (error) {
        showVerifyBox(v.error, error.message);
    } finally {
        button.disabled = false;
        button.textContent = original;
    }
}

function showVerifyBox(element, text) {
    element.textContent = text || "";
    element.classList.toggle("hidden", !text);
}

function verifyDisplayName(record) {
    return [record.salutation, record.first_name, record.surname].filter(Boolean).join(" ") || "Unnamed recipient";
}

function escapeHtml(value) {
    const div = document.createElement("div");
    div.textContent = value ?? "";
    return div.innerHTML;
}

v.promptMode?.addEventListener("change", () => {
    v.promptModeHelp.textContent = promptModeText[v.promptMode.value] || promptModeText.append;
});

document.querySelectorAll("[data-verify-command]").forEach((button) => {
    button.addEventListener("click", () => {
        document.execCommand(button.dataset.verifyCommand, false, null);
        v.promptEditor.focus();
    });
});

v.clearPromptFormattingBtn?.addEventListener("click", () => {
    v.promptEditor.innerText = v.promptEditor.innerText;
    v.promptEditor.focus();
});

v.chooseAttachmentsBtn?.addEventListener("click", () => {
    v.attachmentPicker?.click();
});

v.attachmentPicker?.addEventListener("change", () => {
    const selected = [...(v.attachmentPicker.files || [])]
        .map((file) => file.path || file.name)
        .filter(Boolean);
    if (!selected.length) return;
    const existing = v.attachments.value
        .split(";")
        .map((item) => item.trim())
        .filter(Boolean);
    const merged = [...existing];
    selected.forEach((path) => {
        if (!merged.includes(path)) merged.push(path);
    });
    v.attachments.value = merged.join("; ");
    showVerifyBox(v.notes, `${selected.length} attachment path(s) added. Save the row to validate them.`);
    v.attachmentPicker.value = "";
});

v.saveBtn?.addEventListener("click", () => withBusy(v.saveBtn, "Saving...", async () => {
    await saveVerifyRow(false);
    showVerifyBox(v.notes, "Recipient configuration saved.");
}));

v.markBtn?.addEventListener("click", () => withBusy(v.markBtn, "Verifying...", async () => {
    await saveVerifyRow(true);
}));

v.continueBtn?.addEventListener("click", () => withBusy(v.continueBtn, "Queuing...", async () => {
    await saveVerifyRow(true);
    const invalid = verifyJob.records.filter((record) => record.status === "invalid").length;
    if (invalid) {
        throw new Error(`Fix ${invalid} invalid row(s) before continuing.`);
    }
    const result = await verifyApi(`/api/jobs/${verifyJobId}/verify`, {
        method: "POST",
        body: JSON.stringify({}),
    });
    window.location.href = result.redirect_url;
}));

if (verifyJobId) {
    loadVerifyJob();
}
