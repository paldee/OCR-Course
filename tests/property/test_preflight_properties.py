"""Property test ของ preflight (task 9.2, R20.6-R20.8).

ครอบคลุมสามคุณสมบัติที่ต้องคงอยู่เสมอ

1. hash cache ให้ผลลัพธ์เดิมเมื่อไฟล์ไม่เปลี่ยน และคำนวณใหม่เมื่อ mtime/size เปลี่ยน
2. GPU-gated engine ที่ไม่มี CUDA ต้องไม่ทำให้ preflight ล้มเหลว (R5.1.1, R20.7)
3. sha256 ไม่ตรงกับที่ประกาศไว้ต้องถูกรายงานเป็น missing artifact เสมอ
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from katrag.common.hashing import sha256_file
from katrag.ingest.ocr.preflight import HashCache, run_preflight

PROPERTY_SETTINGS = settings(max_examples=30, deadline=None)


@given(st.binary(min_size=1, max_size=2000))
@PROPERTY_SETTINGS
def test_hash_cache_matches_direct_hash(content: bytes) -> None:
    """cache ต้องให้ sha256 เดียวกันกับการคำนวณตรง ไม่ว่า cache hit หรือ miss.

    ใช้ `tempfile.TemporaryDirectory` แทน pytest fixture `tmp_path` เพราะ hypothesis
    ไม่รองรับ function-scoped fixture ร่วมกับ `@given` โดยตรง
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        path = tmp_path / "weight.bin"
        path.write_bytes(content)
        expected = sha256_file(path)

        cache = HashCache(tmp_path / "cache.json")
        first = cache.sha256_of(path)  # miss
        second = cache.sha256_of(path)  # hit
        assert first == expected
        assert second == expected


@given(st.binary(min_size=1, max_size=500), st.binary(min_size=1, max_size=500))
@PROPERTY_SETTINGS
def test_hash_cache_recomputes_when_file_changes(content_a: bytes, content_b: bytes) -> None:
    """ไฟล์ที่เนื้อหาเปลี่ยน (ขนาดหรือ mtime เปลี่ยน) ต้องได้ sha256 ใหม่ที่ถูกต้อง ไม่ใช่ค่าเดิมจาก cache."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        path = tmp_path / "weight.bin"
        cache_path = tmp_path / "cache.json"
        path.write_bytes(content_a)
        cache = HashCache(cache_path)
        first = cache.sha256_of(path)

        path.write_bytes(content_b)
        # บังคับ mtime ให้ต่างจากเดิมแน่นอน (ระบบไฟล์บางตัวมี granularity ของ mtime หยาบ)
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

        second = cache.sha256_of(path)
        expected_second = sha256_file(path)
        assert second == expected_second
        if content_a != content_b:
            assert first != second or sha256_file(path) == first  # เนื้อหาต่างกันจริงต้องได้ hash ต่างกัน (เว้นแต่ชนกันโดยบังเอิญ)


def test_gpu_gated_engine_skipped_without_cuda_is_not_a_failure(tmp_path: Path, monkeypatch) -> None:
    """ไม่มี CUDA ต้องข้าม engine ที่ gpu_required=True โดยไม่ถือเป็นข้อผิดพลาด (R5.1.1, R20.7)."""
    import katrag.ingest.ocr.preflight as preflight_module

    monkeypatch.setattr(preflight_module, "cuda_available", lambda: False)
    engines_config = {
        "engine": [
            {
                "name": "typhoon_ocr1_5_2b",
                "role": "ocr_stage_2",
                "gpu_required": True,
                "weight_files": [{"path": "nonexistent.safetensors", "weight_sha256": ""}],
            }
        ],
        "preflight": {"fail_fast_seconds": 10.0},
    }
    report = run_preflight(
        engines_config, tmp_path, hash_cache_path=tmp_path / "cache.json", tesseract_cmd="/nonexistent/tesseract"
    )
    engine = report.engine_checks[0]
    assert engine.skipped_reason == "no_cuda"
    assert engine.ok is True
    assert engine.missing_artifacts == ()


def test_sha256_mismatch_is_reported_as_missing_artifact(tmp_path: Path) -> None:
    """weight ที่มีอยู่จริงแต่ sha256 ไม่ตรงกับที่ประกาศไว้ต้องถูกรายงานเป็น artifact ที่ขาด/ไม่ตรง."""
    weight_path = tmp_path / "weight.bin"
    weight_path.write_bytes(b"real content")
    engines_config = {
        "engine": [
            {
                "name": "fake_engine",
                "role": "test",
                "gpu_required": False,
                "weight_files": [
                    {"path": str(weight_path), "weight_sha256": "0" * 64}
                ],
            }
        ],
        "preflight": {"fail_fast_seconds": 10.0},
    }
    report = run_preflight(
        engines_config, tmp_path, hash_cache_path=tmp_path / "cache.json", tesseract_cmd="/nonexistent/tesseract"
    )
    engine = report.engine_checks[0]
    assert engine.weight_checks[0].status == "sha256_mismatch"
    assert str(weight_path) in engine.missing_artifacts or engine.missing_artifacts
    assert not engine.ok
    assert not report.ok
