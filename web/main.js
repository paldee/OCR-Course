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

    // หลักสูตรเป็นค่าบังคับ — ข้อมูลรายวิชา/แผนเรียนต่างกันทุกหลักสูตร
    // ถ้าไม่เลือก คำถามจะกำกวมและระบบตอบผิดหลักสูตรได้
    const programSelect = document.getElementById("program-select");
    const selectedProgram = programSelect ? programSelect.value : "";
    if (!selectedProgram) {
        showError("กรุณาเลือกหลักสูตรก่อนถาม");
        if (programSelect) programSelect.focus();
        return;
    }

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
    // Answer text — คำตอบจาก LLM มักมี markdown (**ตัวหนา**, รายการ -, หัวข้อ)
    // render แบบปลอดภัย: escape HTML ก่อนเสมอ แล้วค่อยแปลง markdown ชุดจำกัด
    answerText.innerHTML = renderMarkdown(data.answer || "(ไม่มีคำตอบ)");

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

// ── Minimal markdown renderer ────────────────────────────────────────
// รองรับเฉพาะ subset ที่ LLM ใช้จริง: หัวข้อ #, ตัวหนา **, ตัวเอียง *,
// inline code `, รายการ - / •, และเว้นบรรทัด
// escape HTML ก่อนเสมอ เพราะข้อความคำตอบเป็น untrusted (ป้องกัน XSS)

function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

function renderInline(text) {
    // text ถูก escape มาแล้ว — ปลอดภัยที่จะใส่ tag
    return text
        .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
        .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
        .replace(/`([^`]+)`/g, "<code>$1</code>");
}

function renderMarkdown(raw) {
    const lines = escapeHtml(raw).split("\n");
    const html = [];
    let inList = false;

    const closeList = () => {
        if (inList) {
            html.push("</ul>");
            inList = false;
        }
    };

    for (const line of lines) {
        const trimmed = line.trim();

        if (trimmed === "") {
            closeList();
            continue;
        }

        // หัวข้อ: #, ##, ###
        const heading = trimmed.match(/^(#{1,3})\s+(.*)$/);
        if (heading) {
            closeList();
            const level = heading[1].length + 2; // #→h3, ##→h4, ###→h5
            html.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
            continue;
        }

        // รายการ: -, *, • ตามด้วยช่องว่าง
        const bullet = trimmed.match(/^[-*•]\s+(.*)$/);
        if (bullet) {
            if (!inList) {
                html.push("<ul class=\"md-list\">");
                inList = true;
            }
            html.push(`<li>${renderInline(bullet[1])}</li>`);
            continue;
        }

        // ย่อหน้าปกติ
        closeList();
        html.push(`<p>${renderInline(trimmed)}</p>`);
    }
    closeList();
    return html.join("");
}

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
