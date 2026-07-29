"""Offline enforcement — บังคับว่าไม่มี outbound request ออกนอก loopback.

ข้อกำหนด R20.1 และ R20.9 บังคับสองข้อ

1. เมื่อ network adapter ปิดทั้งหมด ระบบต้องทำงานได้ทุกฟังก์ชัน
2. การพยายามเรียก address ที่ไม่ใช่ loopback ต้องถูกปฏิเสธ พร้อม error
   ที่ระบุว่าละเมิดข้อจำกัด offline และข้อมูลใน store ต้องไม่เปลี่ยน

การบังคับทำที่ระดับ `socket` เพื่อดักทุกไลบรารีที่อยู่เหนือขึ้นไป
(llama.cpp HTTP client, onnxruntime, requests ฯลฯ) โดยไม่ต้องแก้โค้ดแต่ละจุด
"""

from __future__ import annotations

import ipaddress
import socket
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from katrag.errors import OfflineViolationError

_LOOPBACK_HOSTNAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})


def is_loopback_host(host: str | None) -> bool:
    """คืน True เมื่อ host เป็น loopback (127.0.0.0/8, ::1 หรือชื่อ localhost)."""
    if host is None:
        return False
    candidate = host.strip()
    if not candidate:
        return False
    if candidate.lower() in _LOOPBACK_HOSTNAMES:
        return True
    # รองรับรูปแบบ IPv6 ที่ครอบด้วยวงเล็บ เช่น "[::1]"
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return False


@dataclass(slots=True)
class NetGuardStats:
    """สถิติที่ใช้ยืนยัน property "outbound request นอก loopback = 0"."""

    allowed_connections: int = 0
    blocked_attempts: int = 0
    blocked_targets: list[str] = field(default_factory=list)

    def record_allowed(self) -> None:
        self.allowed_connections += 1

    def record_blocked(self, target: str) -> None:
        self.blocked_attempts += 1
        self.blocked_targets.append(target)


class NetGuard:
    """ตัวบังคับ offline ที่ patch `socket.socket.connect` ระหว่างที่เปิดใช้.

    ใช้เป็น context manager หรือเรียก `install()` / `uninstall()` เอง
    การเรียกซ้อนกันปลอดภัย (nested) เพราะนับ depth ก่อนถอน patch
    """

    __slots__ = (
        "_stats",
        "_depth",
        "_original_connect",
        "_original_connect_ex",
        "_allow_external",
    )

    def __init__(self) -> None:
        self._stats = NetGuardStats()
        self._depth = 0
        self._original_connect: Any = None
        self._original_connect_ex: Any = None
        # เมื่อ True: อนุญาต egress นอก loopback (นับเป็น allowed ไม่ block)
        # ใช้เฉพาะ `serve` ที่จงใจเรียก external LLM API — ข้อยกเว้นที่ผู้ใช้ยอมรับ
        # ต่อ R20.1 ส่วน pipeline อื่น (ingest/index/evaluate) ยังบังคับ offline เข้ม
        self._allow_external = False

    def set_allow_external(self, value: bool) -> None:
        """เปิด/ปิดการอนุญาต egress นอก loopback (ใช้กับ serve เท่านั้น)."""
        self._allow_external = value

    @property
    def stats(self) -> NetGuardStats:
        return self._stats

    @property
    def active(self) -> bool:
        return self._depth > 0

    def install(self) -> None:
        self._depth += 1
        if self._depth > 1:
            return
        self._original_connect = socket.socket.connect
        self._original_connect_ex = socket.socket.connect_ex
        guard = self

        def guarded_connect(sock: socket.socket, address: Any) -> Any:
            guard._check(address)
            return guard._original_connect(sock, address)

        def guarded_connect_ex(sock: socket.socket, address: Any) -> Any:
            guard._check(address)
            return guard._original_connect_ex(sock, address)

        socket.socket.connect = guarded_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = guarded_connect_ex  # type: ignore[method-assign]

    def uninstall(self) -> None:
        if self._depth == 0:
            return
        self._depth -= 1
        if self._depth > 0:
            return
        if self._original_connect is not None:
            socket.socket.connect = self._original_connect  # type: ignore[method-assign]
        if self._original_connect_ex is not None:
            socket.socket.connect_ex = self._original_connect_ex  # type: ignore[method-assign]
        self._original_connect = None
        self._original_connect_ex = None

    def __enter__(self) -> "NetGuard":
        self.install()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.uninstall()

    def _check(self, address: Any) -> None:
        host = _host_of(address)
        if is_loopback_host(host):
            self._stats.record_allowed()
            return
        if self._allow_external:
            # ข้อยกเว้นที่จงใจสำหรับ serve — external LLM API (R20.1 waiver)
            self._stats.record_allowed()
            return
        target = str(host) if host is not None else repr(address)
        self._stats.record_blocked(target)
        raise OfflineViolationError(
            "ละเมิดข้อจำกัด offline: อนุญาตเฉพาะการเชื่อมต่อไป loopback",
            target=target,
        )


def _host_of(address: Any) -> str | None:
    """ดึง host ออกจาก address ของ socket ทุกรูปแบบ (AF_INET, AF_INET6, AF_UNIX)."""
    if isinstance(address, (tuple, list)) and address:
        first = address[0]
        return first if isinstance(first, str) else None
    if isinstance(address, (str, bytes)):
        # AF_UNIX socket path — เป็น local IPC ไม่ใช่ network egress
        return "localhost"
    return None


_GLOBAL_GUARD = NetGuard()


def global_guard() -> NetGuard:
    """คืน guard ตัวเดียวของ process — CLI ติดตั้งตัวนี้ตอนเริ่มทุกคำสั่ง (R20.1)."""
    return _GLOBAL_GUARD


@contextmanager
def enforce_offline() -> Iterator[NetGuard]:
    """เปิดการบังคับ offline ในขอบเขตของ with block."""
    guard = global_guard()
    guard.install()
    try:
        yield guard
    finally:
        guard.uninstall()


def assert_no_external_egress() -> None:
    """ยืนยันว่ายังไม่มี outbound request นอก loopback (ใช้ในเทสต์และตอนจบคำสั่ง)."""
    stats = global_guard().stats
    if stats.blocked_attempts:
        raise OfflineViolationError(
            "พบการพยายามเชื่อมต่อออกนอก loopback",
            attempts=stats.blocked_attempts,
            targets=stats.blocked_targets[:10],
        )
