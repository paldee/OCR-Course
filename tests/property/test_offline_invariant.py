"""Property test ของ offline invariant และ demo CLI (R20.1, R20.9).

ครอบคลุมสองคุณสมบัติ:

1. **No outbound network connection** — เมื่อ net_guard active ทุก socket.connect()
   ไปยัง non-loopback address ต้องถูก block ด้วย OfflineViolationError เสมอ
   ไม่ว่า address จะอยู่ในรูปแบบ IPv4, IPv6 หรือ hostname ใด ๆ

2. **CLI commands terminate without hanging** — argparse + command dispatch
   ต้องจบภายในเวลาที่กำหนด ไม่ค้างตลอดไป (property: finite execution)
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from katrag.common.net_guard import (
    NetGuard,
    enforce_offline,
    global_guard,
    is_loopback_host,
)
from katrag.errors import OfflineViolationError

PROPERTY_SETTINGS = settings(max_examples=50, deadline=None)


# ══════════════════════════════════════════════════════════════════════
# Property 1: No outbound network connection (net_guard active)
# ══════════════════════════════════════════════════════════════════════


# Strategy: generate non-loopback IPv4 addresses
_ipv4_non_loopback = st.tuples(
    st.integers(min_value=1, max_value=254).filter(lambda x: x != 127),
    st.integers(min_value=0, max_value=255),
    st.integers(min_value=0, max_value=255),
    st.integers(min_value=1, max_value=254),
).map(lambda t: f"{t[0]}.{t[1]}.{t[2]}.{t[3]}")

# Strategy: generate non-loopback hostnames
_non_loopback_hostnames = st.sampled_from([
    "example.com",
    "google.com",
    "192.168.1.1",
    "10.0.0.1",
    "172.16.0.1",
    "8.8.8.8",
    "2001:db8::1",
    "fc00::1",
])

# Strategy: ports
_ports = st.integers(min_value=1, max_value=65535)


@given(host=_ipv4_non_loopback, port=_ports)
@PROPERTY_SETTINGS
def test_net_guard_blocks_all_non_loopback_ipv4(host: str, port: int) -> None:
    """net_guard ต้อง block ทุก non-loopback IPv4 address — OfflineViolationError.

    Property: ∀ address ∉ 127.0.0.0/8, socket.connect() → OfflineViolationError
    """
    guard = NetGuard()
    guard.install()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(OfflineViolationError):
                sock.connect((host, port))
        finally:
            sock.close()
    finally:
        guard.uninstall()


@given(host=_non_loopback_hostnames, port=_ports)
@PROPERTY_SETTINGS
def test_net_guard_blocks_all_non_loopback_hostnames(host: str, port: int) -> None:
    """net_guard ต้อง block ทุก hostname ที่ไม่ใช่ loopback.

    Property: ∀ host ∉ {localhost, 127.*, ::1}, connect → OfflineViolationError
    """
    assume(not is_loopback_host(host))
    guard = NetGuard()
    guard.install()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(OfflineViolationError):
                sock.connect((host, port))
        finally:
            sock.close()
    finally:
        guard.uninstall()


@given(
    host=st.sampled_from(["127.0.0.1", "localhost", "::1"]),
    port=st.just(0),
)
@PROPERTY_SETTINGS
def test_net_guard_allows_loopback(host: str, port: int) -> None:
    """net_guard ต้องอนุญาต loopback addresses เสมอ.

    Property: ∀ loopback address, connect ไม่ raise OfflineViolationError
    (อาจ raise ConnectionRefusedError ซึ่งเป็นเรื่องปกติ)
    """
    guard = NetGuard()
    guard.install()
    try:
        # Use port 0 to avoid actually connecting; we only test the guard check
        # The guard checks address before the OS-level connect
        # For loopback: the guard passes through, then OS may refuse
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            # Should NOT raise OfflineViolationError; may raise other errors
            try:
                sock.connect((host, port))
            except OfflineViolationError:
                pytest.fail(f"net_guard blocked loopback address {host}")
            except (ConnectionRefusedError, OSError):
                pass  # Expected — no server listening
        finally:
            sock.close()
    finally:
        guard.uninstall()


def test_net_guard_stats_track_blocked_attempts() -> None:
    """สถิติ blocked_attempts ต้องนับถูกต้อง."""
    guard = NetGuard()
    guard.install()
    try:
        for _ in range(3):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.connect(("8.8.8.8", 53))
            except OfflineViolationError:
                pass
            finally:
                sock.close()
        assert guard.stats.blocked_attempts == 3
        assert len(guard.stats.blocked_targets) == 3
    finally:
        guard.uninstall()


def test_enforce_offline_context_manager_restores_socket() -> None:
    """enforce_offline() ต้องคืน socket.connect เดิมเมื่อออกจาก context."""
    original = socket.socket.connect
    with enforce_offline() as guard:
        assert guard.active
        assert socket.socket.connect is not original
    # After exiting, should be restored
    assert socket.socket.connect is original


# ══════════════════════════════════════════════════════════════════════
# Property 2: CLI commands terminate without hanging
# ══════════════════════════════════════════════════════════════════════

_CLI_COMMANDS = [
    ["--help"],
    ["preflight", "--help"],
    ["ingest", "--help"],
    ["index", "--help"],
    ["evaluate", "--help"],
    ["serve", "--help"],
    ["demo", "--help"],
]


_CLI_ENV = {**__import__("os").environ, "PYTHONIOENCODING": "utf-8"}
_CLI_CWD = str(Path(__file__).resolve().parent.parent.parent)


@pytest.mark.parametrize("cmd_args", _CLI_COMMANDS)
def test_cli_help_terminates(cmd_args: list[str]) -> None:
    """CLI --help commands ต้องจบภายใน 10 วินาที ไม่ค้าง.

    Property: ∀ command ∈ CLI, `katrag <command> --help` terminates in finite time
    """
    result = subprocess.run(
        [sys.executable, "-m", "katrag.cli"] + cmd_args,
        capture_output=True,
        text=True,
        timeout=10,
        cwd=_CLI_CWD,
        env=_CLI_ENV,
    )
    # --help exits with 0
    assert result.returncode == 0, f"Failed: {cmd_args}\nstderr: {result.stderr}"


def test_cli_unknown_command_terminates() -> None:
    """CLI with unknown command ต้องจบโดยไม่ค้าง."""
    result = subprocess.run(
        [sys.executable, "-m", "katrag.cli", "nonexistent_command"],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=_CLI_CWD,
        env=_CLI_ENV,
    )
    # Unknown command — argparse exits with 2 (error) which is correct behavior
    # Key property: it terminates (does not hang)
    assert result.returncode in (0, 1, 2)


def test_cli_no_command_shows_help() -> None:
    """CLI ที่ไม่มี command ต้องแสดง help แล้วจบ."""
    result = subprocess.run(
        [sys.executable, "-m", "katrag.cli"],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=_CLI_CWD,
        env=_CLI_ENV,
    )
    assert result.returncode == 0
    assert "katrag" in result.stdout.lower() or "usage" in result.stdout.lower()


@given(st.sampled_from(["preflight", "ingest", "index", "evaluate", "demo"]))
@PROPERTY_SETTINGS
def test_cli_command_dispatch_does_not_hang(command: str) -> None:
    """ทุก CLI command ที่ไม่ต้อง interactive ต้อง dispatch ได้ภายใน timeout.

    เราทดสอบด้วย --help เพราะ command จริงอาจต้อง data — ที่สำคัญคือ argparse
    dispatch ต้องไม่ค้าง (property: finite execution of parser + dispatch logic)
    """
    result = subprocess.run(
        [sys.executable, "-m", "katrag.cli", command, "--help"],
        capture_output=True,
        text=True,
        timeout=10,
        cwd=_CLI_CWD,
        env=_CLI_ENV,
    )
    assert result.returncode == 0
