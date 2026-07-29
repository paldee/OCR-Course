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

    // Reset UI
    hideElement(errorDisplay);
    hideElement(answerSection);
    showElement(loading);
    askBtn.disabled = true;

    try {
        const response = await fetch(`${API_BASE}/ask`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ question }),
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

    // Clear canvas
    const ctx = viewerCanvas.getContext("2d");
    viewerCanvas.width = 400;
    viewerCanvas.height = 300;
    ctx.fillStyle = "#f1f5f9";
    ctx.fillRect(0, 0, 400, 300);
    ctx.fillStyle = "#64748b";
    ctx.font = "14px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("กำลังโหลด...", 200, 150);

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
        ctx.clearRect(0, 0, viewerCanvas.width, viewerCanvas.height);
        ctx.fillStyle = "#fef2f2";
        ctx.fillRect(0, 0, viewerCanvas.width, viewerCanvas.height);
        ctx.fillStyle = "#dc2626";
        ctx.font = "14px sans-serif";
        ctx.textAlign = "center";
        ctx.fillText(err.message, 200, 150);
    }
}

function renderPageWithBbox(data) {
    viewerTitle.textContent = `${data.citation_id} — ${data.heading}`;
    viewerInfo.textContent = `เอกสาร: ${data.document_id} | หน้า: ${data.page} | ขนาด: ${data.page_width.toFixed(0)}×${data.page_height.toFixed(0)} pt`;

    // Scale page to fit viewer (max 600px wide)
    const maxWidth = 600;
    const pageW = data.page_width || 612;  // default letter size
    const pageH = data.page_height || 792;
    const scale = Math.min(maxWidth / pageW, 1.0);
    const canvasW = Math.round(pageW * scale);
    const canvasH = Math.round(pageH * scale);

    viewerCanvas.width = canvasW;
    viewerCanvas.height = canvasH;

    const ctx = viewerCanvas.getContext("2d");

    // Draw page background
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, canvasW, canvasH);

    // Draw page border
    ctx.strokeStyle = "#e2e8f0";
    ctx.lineWidth = 1;
    ctx.strokeRect(0, 0, canvasW, canvasH);

    // Draw some placeholder content lines
    ctx.fillStyle = "#e2e8f0";
    for (let y = 40; y < canvasH - 40; y += 18) {
        const lineWidth = 50 + Math.random() * (canvasW - 120);
        ctx.fillRect(30 * scale, y, lineWidth * scale, 8);
    }

    // Draw bbox overlay if present
    if (data.bbox) {
        const bbox = data.bbox;
        const x = bbox.x0 * scale;
        const y = bbox.y0 * scale;
        const w = (bbox.x1 - bbox.x0) * scale;
        const h = (bbox.y1 - bbox.y0) * scale;

        // Semi-transparent highlight
        ctx.fillStyle = "rgba(37, 99, 235, 0.12)";
        ctx.fillRect(x, y, w, h);

        // Border
        ctx.strokeStyle = "#2563eb";
        ctx.lineWidth = 2;
        ctx.setLineDash([4, 2]);
        ctx.strokeRect(x, y, w, h);
        ctx.setLineDash([]);

        // Label
        ctx.fillStyle = "#2563eb";
        ctx.font = `bold ${11 * scale}px sans-serif`;
        ctx.textAlign = "left";
        ctx.fillText(data.citation_id, x + 4, y - 4);
    }
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
