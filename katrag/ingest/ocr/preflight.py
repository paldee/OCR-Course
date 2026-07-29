"""Preflight — ตรวจ artifact/dependency ของ OCR cascade ก่อนเริ่มระบบ (design §4.35, R20.6-R20.8).

ข้อบังคับที่ชั้นนี้รักษา

1. **ต้องจบภายใน 10 วินาที** (`fail_fast_seconds` จาก `config/engines.toml`) — ไม่มีการรอ
   I/O ที่ไม่แน่นอน (เช่น เชื่อมต่อเครือข่าย) ในเส้นทางนี้เลย
2. **ห้ามดาวน์โหลดไฟล์ใดทั้งสิ้น** — ตรวจเฉพาะไฟล์ที่มีอยู่แล้วบนดิสก์ และ import
   ไลบรารีแบบ lazy เพื่อไม่ให้ transformers/huggingface_hub พยายามเรียก network
   (ตั้ง `HF_HUB_OFFLINE=1` ก่อน import เสมอ)
3. **ไม่มี GPU/CUDA ไม่ถือเป็นข้อผิดพลาด** — Typhoon-OCR-1.5-2B (stage 2) เป็น GPU-gated
   ตาม R5.1.1/R20.7 preflight ต้องรายงานสถานะนี้แยกจาก artifact ที่ขาดจริง เพื่อให้
   evaluation report อ่านได้ว่าทำไม stage 2 ไม่ทำงาน (ไม่ใช่ artifact ขาด)
4. **weight sha256 ต้องตรงกับที่บันทึกไว้ใน `config/engines.toml`** — ค่าว่าง ("") หมายถึง
   ยังไม่บันทึกไว้ (ยังไม่เคยตรวจยืนยัน) จึงรายงานเป็น "ยังไม่ยืนยัน" ไม่ใช่ "ไม่ตรง"
   เพื่อแยกกรณี "ยังไม่ตั้งค่า" จาก "ตั้งค่าไว้แต่ตอนนี้ไม่ตรง" (weight ถูกแก้ไข)

`glob` pattern ใน `weight_files[].path` (เช่น `snapshots/*/model.safetensors` ของ
Hugging Face cache ที่ hash ของ snapshot เปลี่ยนได้) ถูก resolve ด้วย `Path.glob()`
ก่อนคำนวณ sha256 — ถ้าไม่มีไฟล์ที่ตรง pattern เลยถือเป็น artifact ที่ขาด

**ข้อสังเกตสำคัญที่วัดได้จริง (กระทบเพดาน 10 วินาที):** sha256 ของไฟล์ weight ขนาด 4 GB
(Typhoon-OCR-1.5-2B) ใช้เวลา **5 วินาทีเดียว** บน SSD — ถ้าต้องคำนวณ sha256 ของทุกไฟล์ weight
ใหม่ทุกครั้งที่ preflight รัน (รวม bge-m3 ~2.3 GB และ Qwen3 4B GGUF ~2.6 GB ในเฟสถัดไป) จะเกิน
10 วินาทีแน่นอน จึงใช้ **cache ตาม (path, size, mtime_ns)** ที่ `artifacts/preflight_hash_cache.json`
— คำนวณ sha256 ใหม่เฉพาะไฟล์ที่ขนาดหรือเวลาแก้ไขเปลี่ยนไปจากครั้งก่อน ไฟล์ที่ตรวจแล้วและไม่ถูกแก้ไข
จะข้ามการคำนวณซ้ำ (อ่าน mtime/size ของไฟล์เป็นการดำเนินการที่เร็วกว่าการอ่านทั้งไฟล์มาก)
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from katrag.common.hashing import sha256_file
from katrag.errors import PreflightError

#: env var ที่ต้องตั้งก่อน import huggingface_hub/transformers เพื่อกันการดาวน์โหลด (R20.8)
_HF_OFFLINE_ENV = {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}

#: ชื่อไฟล์ cache ของ sha256 ที่คำนวณไปแล้ว (เทียบ path+size+mtime เพื่อข้ามการคำนวณซ้ำ)
DEFAULT_HASH_CACHE_FILENAME = "preflight_hash_cache.json"


class HashCache:
    """cache sha256 ตาม (path, size, mtime_ns) — ไม่คำนวณซ้ำถ้าไฟล์ไม่เปลี่ยน.

    จำเป็นเพราะไฟล์ weight ของโมเดล OCR/LLM มีขนาดหลาย GB การ sha256 ทุกไฟล์ใหม่ทุกครั้ง
    ที่ preflight รันจะเกินเพดาน 10 วินาทีตาม R20.8 (วัดจริง: 4 GB ≈ 5 วินาที)
    """

    def __init__(self, cache_path: Path) -> None:
        self._cache_path = cache_path
        self._entries: dict[str, dict[str, Any]] = {}
        if cache_path.is_file():
            try:
                self._entries = json.loads(cache_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._entries = {}

    def sha256_of(self, path: Path) -> str:
        stat = path.stat()
        key = str(path.resolve())
        cached = self._entries.get(key)
        if (
            cached is not None
            and cached.get("size") == stat.st_size
            and cached.get("mtime_ns") == stat.st_mtime_ns
        ):
            return str(cached["sha256"])
        digest = sha256_file(path)
        self._entries[key] = {
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": digest,
        }
        return digest

    def save(self) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(
            json.dumps(self._entries, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )


@dataclass(frozen=True, slots=True)
class WeightCheck:
    """ผลตรวจไฟล์ weight หนึ่งไฟล์ (หรือหนึ่ง glob pattern)."""

    declared_path: str
    resolved_path: str | None      # None เมื่อ resolve/glob ไม่พบไฟล์เลย
    exists: bool
    declared_sha256: str           # "" หมายถึงยังไม่บันทึกไว้
    actual_sha256: str | None      # None เมื่อไฟล์ไม่มีอยู่จึงคำนวณไม่ได้
    status: str                    # "ok" | "missing" | "sha256_mismatch" | "sha256_unset"


@dataclass(frozen=True, slots=True)
class EngineCheck:
    """ผลตรวจ engine หนึ่งตัวจาก `config/engines.toml`."""

    name: str
    role: str
    gpu_required: bool
    weight_checks: tuple[WeightCheck, ...]
    skipped_reason: str | None     # "no_cuda" เมื่อ gpu_required=True และไม่มี CUDA

    @property
    def ok(self) -> bool:
        """engine พร้อมใช้งานจริง — GPU-gated ที่ถูกข้ามอย่างมีเหตุผลไม่ถือว่า ok=False."""
        if self.skipped_reason is not None:
            return True
        return all(w.status == "ok" for w in self.weight_checks)

    @property
    def missing_artifacts(self) -> tuple[str, ...]:
        if self.skipped_reason is not None:
            return ()
        return tuple(
            w.declared_path for w in self.weight_checks if w.status in ("missing", "sha256_mismatch")
        )


@dataclass(frozen=True, slots=True)
class PreflightReport:
    """ผลตรวจ preflight ทั้งหมด (design §4.35)."""

    ok: bool
    elapsed_seconds: float
    cuda_available: bool
    tesseract_binary_found: bool
    tesseract_langs_found: tuple[str, ...]
    engine_checks: tuple[EngineCheck, ...]
    missing_artifacts: tuple[str, ...]

    def summary_lines(self) -> tuple[str, ...]:
        """สรุปแบบมนุษย์อ่านได้ — ใช้แสดงตอน CLI preflight ล้มเหลว."""
        lines: list[str] = []
        if not self.tesseract_binary_found:
            lines.append("ไม่พบ Tesseract binary")
        missing_langs = {"tha", "eng"} - set(self.tesseract_langs_found)
        if missing_langs:
            lines.append(f"Tesseract ขาด traineddata: {', '.join(sorted(missing_langs))}")
        for engine in self.engine_checks:
            if engine.skipped_reason is not None:
                lines.append(f"{engine.name}: ข้าม ({engine.skipped_reason}) — ไม่ถือเป็นข้อผิดพลาด")
                continue
            for artifact in engine.missing_artifacts:
                lines.append(f"{engine.name}: artifact ขาดหรือไม่ตรง — {artifact}")
        return tuple(lines)


def cuda_available() -> bool:
    """ตรวจ CUDA แบบไม่ import torch เต็มโมดูลถ้าไม่จำเป็น (เร็ว, ไม่ error เมื่อไม่มี torch)."""
    try:
        import torch  # นำเข้าเฉพาะตอนตรวจ — โมดูลนี้ไม่ควร import torch ที่ระดับบนสุด
    except ImportError:
        return False
    try:
        return bool(torch.cuda.is_available())
    except Exception:  # pragma: no cover - driver/DLL ผิดพลาดต้องไม่ทำให้ preflight crash
        return False


def tesseract_status(tesseract_cmd: str | None = None) -> tuple[bool, tuple[str, ...]]:
    """ตรวจว่ามี Tesseract binary และ traineddata ภาษาไหนบ้าง โดยไม่เรียก subprocess ที่ค้างได้.

    คืน (พบ binary หรือไม่, รายชื่อภาษาที่พบ) — ใช้ `pytesseract` เฉพาะเพื่อหา path ของ
    binary ถ้าตั้งค่าไว้ ไม่ได้เรียก `tesseract --list-langs` เพื่อเลี่ยงการพึ่ง subprocess
    ในเส้นทางที่ต้องจบภายใน 10 วินาทีเสมอ — ตรวจจากไฟล์ `tessdata/*.traineddata` ตรง ๆ แทน
    """
    candidates = [tesseract_cmd] if tesseract_cmd else []
    candidates.extend(
        [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            "/usr/bin/tesseract",
            "/usr/local/bin/tesseract",
        ]
    )
    binary_path: Path | None = None
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            binary_path = Path(candidate)
            break
    if binary_path is None:
        return False, ()

    tessdata_dir = binary_path.parent / "tessdata"
    if not tessdata_dir.is_dir():
        return True, ()
    langs = tuple(
        sorted(p.stem for p in tessdata_dir.glob("*.traineddata") if p.stem in ("tha", "eng"))
    )
    return True, langs


def _resolve_weight_paths(project_root: Path, declared: str) -> list[Path]:
    """แปลง path ที่ประกาศไว้ (รองรับ glob) เป็นรายการไฟล์ที่มีอยู่จริง.

    path ที่เป็น absolute (เช่น cache ของ Hugging Face ที่อยู่นอก `project/`) ใช้ตรง ๆ
    ส่วน path สัมพัทธ์เทียบจาก `project_root`
    """
    candidate = Path(declared)
    base = candidate if candidate.is_absolute() else project_root / candidate
    if "*" not in declared:
        return [base] if base.is_file() else []
    # glob ต้องแยก parent ที่ไม่มี wildcard ออกจากส่วนที่มี wildcard
    parts = base.parts
    fixed_parts: list[str] = []
    pattern_parts: list[str] = []
    seen_wildcard = False
    for part in parts:
        if "*" in part:
            seen_wildcard = True
        (pattern_parts if seen_wildcard else fixed_parts).append(part)
    fixed_root = Path(*fixed_parts) if fixed_parts else Path(base.anchor)
    if not fixed_root.exists():
        return []
    pattern = "/".join(pattern_parts) if pattern_parts else "*"
    return sorted(p for p in fixed_root.glob(pattern) if p.is_file())


def _check_weight_file(
    project_root: Path, declared_path: str, declared_sha256: str, hash_cache: HashCache
) -> WeightCheck:
    matches = _resolve_weight_paths(project_root, declared_path)
    if not matches:
        return WeightCheck(
            declared_path=declared_path,
            resolved_path=None,
            exists=False,
            declared_sha256=declared_sha256,
            actual_sha256=None,
            status="missing",
        )
    resolved = matches[0]
    actual = hash_cache.sha256_of(resolved)
    if not declared_sha256:
        status = "sha256_unset"
    elif actual == declared_sha256:
        status = "ok"
    else:
        status = "sha256_mismatch"
    return WeightCheck(
        declared_path=declared_path,
        resolved_path=str(resolved),
        exists=True,
        declared_sha256=declared_sha256,
        actual_sha256=actual,
        status=status,
    )


def run_preflight(
    engines_config: Mapping[str, Any],
    project_root: Path,
    *,
    tesseract_cmd: str | None = None,
    hash_cache_path: Path | None = None,
) -> PreflightReport:
    """รันตรวจ preflight ทั้งหมด — ต้องจบภายใน `[preflight].fail_fast_seconds` (R20.8).

    Args:
        engines_config: เนื้อหาที่ parse แล้วจาก `config/engines.toml` (`KatragConfig.engines`)
        project_root: รากของโปรเจกต์ สำหรับ resolve path สัมพัทธ์ของ weight
        tesseract_cmd: path ของ tesseract binary ถ้าต้องการระบุเอง (ทดสอบ)
        hash_cache_path: path ของ cache ไฟล์ sha256 (ค่าเริ่มต้น
            `project_root/artifacts/preflight_hash_cache.json`) — จำเป็นเพื่อให้ preflight
            จบภายใน 10 วินาทีเมื่อมีไฟล์ weight ขนาดหลาย GB (ดู docstring ของโมดูล)

    Raises:
        PreflightError: เมื่อ `fail_fast_seconds` ถูกเกินระหว่างตรวจ (สัญญาณว่าการตรวจ
            พึ่งพา I/O ที่ไม่แน่นอน ซึ่งไม่ควรเกิดขึ้นเพราะห้ามดาวน์โหลด)
    """
    for key, value in _HF_OFFLINE_ENV.items():
        os.environ.setdefault(key, value)

    start = time.perf_counter()
    fail_fast_seconds = float(engines_config.get("preflight", {}).get("fail_fast_seconds", 10.0))

    has_cuda = cuda_available()
    tess_found, tess_langs = tesseract_status(tesseract_cmd)

    cache_path = hash_cache_path or (project_root / "artifacts" / DEFAULT_HASH_CACHE_FILENAME)
    hash_cache = HashCache(cache_path)

    engine_checks: list[EngineCheck] = []
    for entry in engines_config.get("engine", []):
        name = str(entry.get("name", ""))
        role = str(entry.get("role", ""))
        gpu_required = bool(entry.get("gpu_required", False))
        skipped_reason = "no_cuda" if (gpu_required and not has_cuda) else None

        weight_checks: list[WeightCheck] = []
        if skipped_reason is None:
            for weight_entry in entry.get("weight_files", []):
                weight_checks.append(
                    _check_weight_file(
                        project_root,
                        str(weight_entry.get("path", "")),
                        str(weight_entry.get("weight_sha256", "")),
                        hash_cache,
                    )
                )

        if name == "tesseract5":
            # tessdata อยู่ข้าง binary ของระบบ ไม่ใช่ path ที่ config ประกาศแบบ engine อื่น
            # (ดูหมายเหตุใน engines.toml) จึงสร้าง WeightCheck สังเคราะห์จากผลของ
            # tesseract_status() เพื่อให้ EngineCheck.ok สะท้อนความพร้อมจริง
            missing_langs = {"tha", "eng"} - set(tess_langs)
            weight_checks = [
                WeightCheck(
                    declared_path=f"tessdata/{lang}.traineddata (system install)",
                    resolved_path=None,
                    exists=False,
                    declared_sha256="",
                    actual_sha256=None,
                    status="missing",
                )
                for lang in sorted(missing_langs)
            ]
            if not tess_found:
                weight_checks.append(
                    WeightCheck(
                        declared_path="tesseract.exe (system install)",
                        resolved_path=None,
                        exists=False,
                        declared_sha256="",
                        actual_sha256=None,
                        status="missing",
                    )
                )

        engine_checks.append(
            EngineCheck(
                name=name,
                role=role,
                gpu_required=gpu_required,
                weight_checks=tuple(weight_checks),
                skipped_reason=skipped_reason,
            )
        )

        elapsed = time.perf_counter() - start
        if elapsed > fail_fast_seconds:
            raise PreflightError(
                "preflight เกินเวลาที่กำหนด — การตรวจต้องไม่พึ่ง I/O ที่ไม่แน่นอน",
                elapsed_seconds=elapsed,
                fail_fast_seconds=fail_fast_seconds,
            )

    hash_cache.save()

    missing_artifacts = tuple(
        artifact for engine in engine_checks for artifact in engine.missing_artifacts
    )
    tesseract_ok = tess_found and {"tha", "eng"}.issubset(set(tess_langs))
    overall_ok = tesseract_ok and all(engine.ok for engine in engine_checks)

    elapsed_total = time.perf_counter() - start
    return PreflightReport(
        ok=overall_ok,
        elapsed_seconds=elapsed_total,
        cuda_available=has_cuda,
        tesseract_binary_found=tess_found,
        tesseract_langs_found=tess_langs,
        engine_checks=tuple(engine_checks),
        missing_artifacts=missing_artifacts,
    )
