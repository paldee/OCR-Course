/**
 * KatRAG Web UI — vanilla JS frontend
 *
 * Features:
 * - ส่งคำถามผ่าน POST /ask
 * - แสดงคำตอบพร้อม citations
 * - คลิก citation → GET /pages/{citation_id} แสดงหน้าเอกสาร + bbox overlay
 * - แสดง curriculum version และสถานะการตรวจ
 */

"use strict";

const API_BASE = "";  // same origin — served by FastAPI

// ── DOM Elements ─────────────────────────────────────────────────────

const form = document.getElementById("ask-form");
const questionInput = document.getElementById("question-input");
const charCount = document.getElementById("char-count");
const askBtn = document.getElementById("ask-btn");
const loading = document.getElementById("loading");
const errorDisplay = document.getElementById("error-display");
const answerSection = document.getElementById("answer-section");
const answerText = document.getElementById("answer-text");
const versionBadge = document.getElementById("version-badge");
const timeBadge = document.getElementById("time-badge");
const removedCount = document.getElementById("removed-count");
const unsupportedCount = document.getElementById("unsupported-count");
const citationsList = document.getElementById("citations-list");
const pageViewer = document.getElementById("page-viewer");
const closeViewer = document.getElementById("close-viewer");
const viewerTitle = document.getElementById("viewer-title");
const viewerCanvas = document.getElementById("viewer-canvas");
const viewerInfo = document.getElementById("viewer-info");

// ── Character counter ────────────────────────────────────────────────

questionInput.addEventListener("input", () => {
    const len = questionInput.value.length;
    charCount.textContent = `${len} / 2000`;
});

// ── Form submission ──────────────────────────────────────────────────

form.addEventListener("submit", async (e) => {
    e.preventDefault();

    const question = questionInput.value.trim();
    if (!question) return;

    // ส่ง program เป็น field แยก (backend prepend เอง — robust)
    const programSelect = document.getElementById("program-select");
    const selectedProgram = programSelect ? programSelect.value : "";

    // Reset UI
    hideElement(errorDisplay);
    hideElement(answerSection);
    showElement(loading);
    askBtn.disabled = true;

    try {
        const response = await fetch(`${API_BASE}/ask`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ question, program: selectedProgram }),
        });

        if (!response.ok) {
            const errData = await response.json().catch(() => null);
            if (response.status === 422 && errData && errData.detail) {
                const msgs = errData.detail.map(
                    (d) => `${d.loc.join(".")}: ${d.msg}`
                );
                throw new Error(`Validation error:\n${msgs.join("\n")}`);
            }
            throw new Error(
                errData?.detail || `HTTP ${response.status}: ${response.statusText}`
            );
        }

        const data = await response.json();
        renderAnswer(data);
    } catch (err) {
        showError(err.message || "เกิดข้อผิดพลาด");
    } finally {
        hideElement(loading);
        askBtn.disabled = false;
    }
});

// ── Render answer ────────────────────────────────────────────────────

function renderAnswer(data) {
    // Answer text
    answerText.textContent = data.answer || "(ไม่มีคำตอบ)";

    // Version badge
    if (data.versions_resolved && data.versions_resolved.length > 0) {
        versionBadge.textContent = data.versions_resolved.join(", ");
        showElement(versionBadge);
    } else {
        versionBadge.textContent = "ทุกเวอร์ชัน";
        showElement(versionBadge);
    }

    // Time badge
    timeBadge.textContent = `${data.total_time_seconds.toFixed(2)}s`;

    // Validation status
    removedCount.textContent = `ลบ: ${data.citations_removed} รายการ`;
    unsupportedCount.textContent = `ไม่รองรับ: ${data.unsupported_claims} รายการ`;

    // Citations
    citationsList.innerHTML = "";
    if (data.citations && data.citations.length > 0) {
        data.citations.forEach((cite) => {
            const li = document.createElement("li");
            li.setAttribute("role", "button");
            li.setAttribute("tabindex", "0");
            li.setAttribute("aria-label", `ดูอ้างอิง ${cite.citation_id}`);
            li.innerHTML = `
                <span class="citation-id">[${cite.citation_id}]</span>
                <span>${cite.heading}</span>
                <span class="citation-meta">— ${cite.document_id} หน้า ${cite.page}</span>
            `;
            li.addEventListener("click", () => openPageViewer(cite.citation_id));
            li.addEventListener("keydown", (e) => {
                if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    openPageViewer(cite.citation_id);
                }
            });
            citationsList.appendChild(li);
        });
    } else {
        const li = document.createElement("li");
        li.textContent = "ไม่มี citation";
        li.style.color = "var(--text-muted)";
        citationsList.appendChild(li);
    }

    showElement(answerSection);
}

// ── Page viewer ──────────────────────────────────────────────────────

async function openPageViewer(citationId) {
    showElement(pageViewer);
    viewerTitle.textContent = `กำลังโหลด ${citationId}...`;
    viewerInfo.textContent = "";

    // Hide canvas, reset text viewer
    viewerCanvas.style.display = "none";
    const existingText = document.getElementById("viewer-text-content");
    if (existingText) existingText.style.display = "none";

    try {
        const response = await fetch(`${API_BASE}/pages/${encodeURIComponent(citationId)}`);

        if (!response.ok) {
            if (response.status === 404) {
                throw new Error(`ไม่พบ citation: ${citationId}`);
            }
            throw new Error(`HTTP ${response.status}`);
        }

        const data = await response.json();
        renderPageWithBbox(data);
    } catch (err) {
        viewerTitle.textContent = "เกิดข้อผิดพลาด";
        viewerInfo.textContent = err.message;
    }
}

function renderPageWithBbox(data) {
    viewerTitle.textContent = `${data.citation_id} — ${data.heading}`;
    viewerInfo.textContent = `เอกสาร: ${data.document_id} | หน้า: ${data.page}`;

    // แสดงเนื้อหาข้อความแทน canvas จำลอง — ใช้ประโยชน์ได้จริง
    viewerCanvas.style.display = "none";

    // สร้าง/อัป text content element
    let textEl = document.getElementById("viewer-text-content");
    if (!textEl) {
        textEl = document.createElement("div");
        textEl.id = "viewer-text-content";
        textEl.style.cssText = "white-space:pre-wrap;font-size:14px;line-height:1.6;padding:16px;max-height:400px;overflow-y:auto;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;margin-top:8px;";
        viewerCanvas.parentNode.insertBefore(textEl, viewerCanvas.nextSibling);
    }
    textEl.style.display = "block";
    textEl.textContent = data.chunk_text || `(ไม่มีเนื้อหาข้อความ — หน้า ${data.page})`;
}

// ── Close viewer ─────────────────────────────────────────────────────

closeViewer.addEventListener("click", () => hideElement(pageViewer));
document.querySelector(".page-viewer-backdrop")?.addEventListener("click", () =>
    hideElement(pageViewer)
);
document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !pageViewer.classList.contains("hidden")) {
        hideElement(pageViewer);
    }
});

// ── Utility ──────────────────────────────────────────────────────────

function showElement(el) {
    el.classList.remove("hidden");
}

function hideElement(el) {
    el.classList.add("hidden");
}

function showError(message) {
    errorDisplay.textContent = message;
    showElement(errorDisplay);
}
