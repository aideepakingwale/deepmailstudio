const shell = document.querySelector(".job-shell");
const jobId = shell?.dataset.jobId;

let job = null;
let selectedId = null;
let pollTimer = null;
let selectedForSend = new Set();
let mailClients = [];

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
    brandNameInput: document.getElementById("brandNameInput"),
    brandVoiceInput: document.getElementById("brandVoiceInput"),
    brandPrimaryInput: document.getElementById("brandPrimaryInput"),
    brandAccentInput: document.getElementById("brandAccentInput"),
    brandLogoInput: document.getElementById("brandLogoInput"),
    brandLayoutInput: document.getElementById("brandLayoutInput"),
    brandCtaTextInput: document.getElementById("brandCtaTextInput"),
    brandCtaUrlInput: document.getElementById("brandCtaUrlInput"),
    brandFooterInput: document.getElementById("brandFooterInput"),
    senderNameInput: document.getElementById("senderNameInput"),
    senderEmailInput: document.getElementById("senderEmailInput"),
    senderTitleInput: document.getElementById("senderTitleInput"),
    senderOrganizationInput: document.getElementById("senderOrganizationInput"),
    senderPhoneInput: document.getElementById("senderPhoneInput"),
    senderWebsiteInput: document.getElementById("senderWebsiteInput"),
    senderSignatureInput: document.getElementById("senderSignatureInput"),
    formatBlockSelect: document.getElementById("formatBlockSelect"),
    textColorInput: document.getElementById("textColorInput"),
    linkBtn: document.getElementById("linkBtn"),
    ctaBtn: document.getElementById("ctaBtn"),
    dividerBtn: document.getElementById("dividerBtn"),
    htmlSourceBtn: document.getElementById("htmlSourceBtn"),
    htmlSourcePanel: document.getElementById("htmlSourcePanel"),
    htmlSourceInput: document.getElementById("htmlSourceInput"),
    loadHtmlBtn: document.getElementById("loadHtmlBtn"),
    applyHtmlBtn: document.getElementById("applyHtmlBtn"),
    refreshEventsBtn: document.getElementById("refreshEventsBtn"),
    eventList: document.getElementById("eventList"),
    countPending: document.getElementById("countPending"),
    countReady: document.getElementById("countReady"),
    countSent: document.getElementById("countSent"),
    progressText: document.getElementById("progressText"),
    progressFill: document.getElementById("progressFill"),
    selectedSendCount: document.getElementById("selectedSendCount"),
    mailClientSelect: document.getElementById("mailClientSelect"),
    mailClientHelp: document.getElementById("mailClientHelp"),
    selectApprovedBtn: document.getElementById("selectApprovedBtn"),
    sendSelectedBtn: document.getElementById("sendSelectedBtn"),
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

async function loadMailClients() {
    if (!els.mailClientSelect) return;
    try {
        const response = await fetch("/api/mail-clients", { cache: "no-store" });
        const payload = await response.json();
        mailClients = payload.clients || [];
        renderMailClients(payload.default_client_id || "");
    } catch (error) {
        els.mailClientSelect.innerHTML = '<option value="">Mail client detection failed</option>';
        els.mailClientHelp.textContent = error.message;
    }
}

function renderMailClients(defaultClientId = "") {
    const sendable = mailClients.filter((client) => client.can_send);
    if (!sendable.length) {
        els.mailClientSelect.innerHTML = '<option value="">No automatic sender detected</option>';
        els.mailClientSelect.disabled = true;
        const visibleClients = mailClients
            .filter((client) => client.installed)
            .map(formatMailClientSummary)
            .join(" | ");
        els.mailClientHelp.textContent = visibleClients || "Configure SMTP or install classic Outlook with pywin32 support.";
        updateSelectedSendUi();
        return;
    }
    els.mailClientSelect.disabled = false;
    els.mailClientSelect.innerHTML = sendable
        .map((client) => `<option value="${escapeAttribute(client.id)}">${escapeHtml(client.name)}</option>`)
        .join("");
    els.mailClientSelect.value = defaultClientId && sendable.some((client) => client.id === defaultClientId)
        ? defaultClientId
        : sendable[0].id;
    updateMailClientHelp();
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
    const completed = ready + sent;
    const percent = job.records.length ? Math.round((completed / job.records.length) * 100) : 0;
    if (els.progressText) els.progressText.textContent = `${percent}%`;
    if (els.progressFill) els.progressFill.style.width = `${percent}%`;
    syncSelectedForSend();
    updateSelectedSendUi();
}

function renderRecipientList() {
    els.recipientList.innerHTML = "";
    job.records.forEach((record) => {
        const button = document.createElement("button");
        button.className = `recipient-item ${record.id === selectedId ? "active" : ""}`;
        const canSelectForSend = canBulkSend(record);
        button.innerHTML = `
            <span class="recipient-line">
                <input class="send-check" type="checkbox" data-record-id="${escapeAttribute(record.id)}" ${selectedForSend.has(record.id) ? "checked" : ""} ${canSelectForSend ? "" : "disabled"} title="Select approved email for bulk sending">
                <span>
                    <strong>${escapeHtml(displayName(record))}</strong>
                    <small>${escapeHtml(record.emailid || "No email")}</small>
                </span>
            </span>
            <span class="mini-status ${escapeHtml(record.status)}">${escapeHtml(record.status)}</span>
        `;
        button.addEventListener("click", () => {
            selectedId = record.id;
            render();
        });
        const checkbox = button.querySelector(".send-check");
        checkbox?.addEventListener("click", (event) => {
            event.stopPropagation();
            if (checkbox.checked) selectedForSend.add(record.id);
            else selectedForSend.delete(record.id);
            updateSelectedSendUi();
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
    els.generateBtn.textContent = activeGeneration ? "Working..." : (record.subject || record.body_html ? "Regen" : "Generate");
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

function canBulkSend(record) {
    return ["approved", "drafted"].includes(record.status);
}

function syncSelectedForSend() {
    const validIds = new Set((job?.records || []).filter(canBulkSend).map((record) => record.id));
    selectedForSend = new Set([...selectedForSend].filter((recordId) => validIds.has(recordId)));
}

function updateSelectedSendUi() {
    if (els.selectedSendCount) {
        els.selectedSendCount.textContent = `${selectedForSend.size} selected`;
    }
    if (els.sendSelectedBtn) {
        els.sendSelectedBtn.disabled = selectedForSend.size === 0;
        els.sendSelectedBtn.title = selectedForSend.size === 0
            ? "Select one or more approved emails first."
            : (els.mailClientSelect?.value ? "Send selected approved emails." : "No automatic sender is currently available.");
    }
}

function updateMailClientHelp() {
    const client = mailClients.find((item) => item.id === els.mailClientSelect?.value);
    if (!client || !els.mailClientHelp) return;
    els.mailClientHelp.textContent = formatMailClientSummary(client);
    updateSelectedSendUi();
}

function formatMailClientSummary(client) {
    const parts = [client.detail || ""];
    if (client.account_count !== undefined && client.account_count !== null) {
        const accounts = Array.isArray(client.accounts) && client.accounts.length
            ? ` (${client.accounts.join(", ")})`
            : "";
        parts.push(`Accounts: ${client.account_count}${accounts}`);
    }
    if (client.reason) {
        parts.push(client.reason);
    }
    return `${client.name}: ${parts.filter(Boolean).join(" · ")}`;
}

async function setBusy(button, label, action) {
    const original = button.textContent;
    button.disabled = true;
    button.classList.add("busy");
    button.textContent = label;
    try {
        await action();
    } catch (error) {
        showBox(els.errorBox, error.message);
    } finally {
        button.textContent = original;
        button.disabled = false;
        button.classList.remove("busy");
        updateSelectedSendUi();
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
els.mailClientSelect?.addEventListener("change", updateMailClientHelp);

els.selectApprovedBtn?.addEventListener("click", () => {
    selectedForSend = new Set(job.records.filter(canBulkSend).map((record) => record.id));
    renderRecipientList();
    updateSelectedSendUi();
});

els.sendSelectedBtn?.addEventListener("click", () => setBusy(els.sendSelectedBtn, "Sending...", async () => {
    const client = mailClients.find((item) => item.id === els.mailClientSelect.value);
    if (!client) {
        throw new Error("No automatic sender is available right now. Configure SMTP, or activate/sign in to classic Outlook and refresh DeepMail Studio.");
    }
    const ok = confirm(`Send ${selectedForSend.size} approved email(s) now using ${client.name}?`);
    if (!ok) return;
    const result = await api(`/api/jobs/${jobId}/send-selected`, {
        method: "POST",
        body: JSON.stringify({
            record_ids: [...selectedForSend],
            client_id: client.id,
        }),
    });
    selectedForSend.clear();
    await loadJob();
    const failed = result.failed?.length || 0;
    showBox(els.notesBox, `Bulk send completed via ${client.name}. Sent: ${result.sent.length}. Failed: ${failed}.`);
}));

els.saveContextBtn?.addEventListener("click", () => setBusy(els.saveContextBtn, "Saving...", async () => {
    await api(`/api/jobs/${jobId}/context`, {
        method: "POST",
        body: JSON.stringify({
            context_prompt: els.jobContextInput.value,
            use_row_prompts: els.useRowPromptsInput.checked,
            brand: collectBrand(),
            sender: collectSender(),
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
    const client = mailClients.find((item) => item.id === els.mailClientSelect?.value);
    if (!client) {
        throw new Error("No automatic sender is available right now. Configure SMTP, or activate/sign in to classic Outlook and refresh DeepMail Studio.");
    }
    const ok = confirm(`Approve and send this email now using ${client.name}?`);
    if (!ok) return;
    await persistReview(true);
    const record = currentRecord();
    await api(`/api/jobs/${jobId}/send-selected`, {
        method: "POST",
        body: JSON.stringify({
            record_ids: [record.id],
            client_id: client.id,
        }),
    });
    await loadJob();
}));

document.querySelectorAll("[data-command]").forEach((button) => {
    button.addEventListener("click", () => {
        document.execCommand(button.dataset.command, false, null);
        els.bodyEditor.focus();
    });
});

els.formatBlockSelect?.addEventListener("change", () => {
    document.execCommand("formatBlock", false, els.formatBlockSelect.value);
    els.bodyEditor.focus();
});

els.textColorInput?.addEventListener("input", () => {
    document.execCommand("foreColor", false, els.textColorInput.value);
    els.bodyEditor.focus();
});

els.linkBtn?.addEventListener("click", () => {
    const url = prompt("Link URL");
    if (!url) return;
    document.execCommand("createLink", false, url);
    els.bodyEditor.focus();
});

els.ctaBtn?.addEventListener("click", () => {
    const text = els.brandCtaTextInput?.value || prompt("CTA text") || "Learn More";
    const url = els.brandCtaUrlInput?.value || prompt("CTA URL") || "#";
    const color = els.brandPrimaryInput?.value || "#166a5f";
    document.execCommand(
        "insertHTML",
        false,
        `<p style="margin:24px 0;"><a href="${escapeAttribute(url)}" style="background:${escapeAttribute(color)};color:#ffffff;text-decoration:none;padding:12px 18px;border-radius:6px;display:inline-block;font-weight:700;">${escapeHtml(text)}</a></p>`
    );
    els.bodyEditor.focus();
});

els.dividerBtn?.addEventListener("click", () => {
    document.execCommand("insertHTML", false, '<hr style="border:0;border-top:1px solid #e5e7eb;margin:24px 0;">');
    els.bodyEditor.focus();
});

els.htmlSourceBtn?.addEventListener("click", () => {
    els.htmlSourcePanel.classList.toggle("hidden");
    if (!els.htmlSourcePanel.classList.contains("hidden")) {
        els.htmlSourceInput.value = els.bodyEditor.innerHTML;
    }
});

els.loadHtmlBtn?.addEventListener("click", () => {
    els.htmlSourceInput.value = els.bodyEditor.innerHTML;
});

els.applyHtmlBtn?.addEventListener("click", () => {
    els.bodyEditor.innerHTML = els.htmlSourceInput.value;
    showBox(els.notesBox, "HTML source applied to the editor. Approve to save it.");
});

function collectBrand() {
    return {
        name: els.brandNameInput?.value || "",
        voice: els.brandVoiceInput?.value || "",
        primary_color: els.brandPrimaryInput?.value || "#166a5f",
        accent_color: els.brandAccentInput?.value || "#b4462d",
        logo_url: els.brandLogoInput?.value || "",
        layout: els.brandLayoutInput?.value || "",
        cta_text: els.brandCtaTextInput?.value || "",
        cta_url: els.brandCtaUrlInput?.value || "",
        footer: els.brandFooterInput?.value || "",
    };
}

function collectSender() {
    return {
        name: els.senderNameInput?.value || "",
        email: els.senderEmailInput?.value || "",
        title: els.senderTitleInput?.value || "",
        organization: els.senderOrganizationInput?.value || "",
        phone: els.senderPhoneInput?.value || "",
        website: els.senderWebsiteInput?.value || "",
        signature_html: els.senderSignatureInput?.value || "",
    };
}

function escapeAttribute(value) {
    return String(value ?? "").replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}

if (jobId) {
    loadJob();
    loadMailClients();
}
