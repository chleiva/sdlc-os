"""Section 10.1 sandbox tiering: real resource limits, real egress
allowlist proxy enforcement, real audit logging -- and the tiering
*selection* logic. See `sandbox.py`'s module docstring for exactly what
is genuinely enforced here vs. a structural placeholder for D6/infra."""
from __future__ import annotations

import socket
import sys

from orchestrator.sandbox import (
    AllowlistProxy,
    ResourceLimits,
    SandboxTier,
    SubprocessSandboxRuntime,
    default_runtime_for_tier,
    required_tier,
)


def test_required_tier_defaults_to_microvm_for_just_written_code():
    assert required_tier(code_just_written=True, previously_human_reviewed=False) == SandboxTier.MICROVM
    assert required_tier(code_just_written=True, previously_human_reviewed=True) == SandboxTier.MICROVM


def test_required_tier_allows_container_only_for_trusted_reviewed_automation():
    assert required_tier(code_just_written=False, previously_human_reviewed=True) == SandboxTier.CONTAINER


def test_required_tier_never_downgrades_unreviewed_code_to_container():
    assert required_tier(code_just_written=False, previously_human_reviewed=False) == SandboxTier.MICROVM


def test_real_cpu_time_limit_actually_kills_a_cpu_bound_loop():
    runtime = SubprocessSandboxRuntime()
    result = runtime.execute(
        [sys.executable, "-c", "\nwhile True:\n    pass\n"],
        resource_limits=ResourceLimits(cpu_seconds=1, timeout_seconds=5),
    )
    # Killed by the real RLIMIT_CPU (SIGXCPU) well before the 5s wall
    # timeout would otherwise fire -- returncode is negative (POSIX:
    # -signal) or a non-zero exit, never a clean 0.
    assert result.returncode != 0
    assert not result.timed_out


def test_real_memory_limit_actually_refuses_a_large_allocation():
    runtime = SubprocessSandboxRuntime()
    script = "data = bytearray(2 * 1024 * 1024 * 1024)\n"  # 2GB, over the limit below
    result = runtime.execute(
        [sys.executable, "-c", script], resource_limits=ResourceLimits(cpu_seconds=5, memory_bytes=128 * 1024 * 1024, timeout_seconds=5)
    )
    assert result.returncode != 0


def test_real_wall_clock_timeout_is_enforced():
    runtime = SubprocessSandboxRuntime()
    result = runtime.execute(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        resource_limits=ResourceLimits(cpu_seconds=30, memory_bytes=256 * 1024 * 1024, timeout_seconds=0.5),
    )
    assert result.timed_out is True


def test_execute_appends_a_real_audit_record_for_every_call():
    runtime = SubprocessSandboxRuntime()
    runtime.execute(["true"], resource_limits=ResourceLimits(timeout_seconds=2))
    runtime.execute(["false"], resource_limits=ResourceLimits(timeout_seconds=2))
    assert len(runtime.audit_log) == 2
    assert runtime.audit_log[0].tier == "container"
    assert runtime.audit_log[1].command == ("false",)


def test_default_runtime_for_tier_returns_the_right_tier_label():
    assert default_runtime_for_tier(SandboxTier.MICROVM).tier == SandboxTier.MICROVM
    assert default_runtime_for_tier(SandboxTier.GVISOR).tier == SandboxTier.GVISOR
    assert default_runtime_for_tier(SandboxTier.CONTAINER).tier == SandboxTier.CONTAINER


# ---------------------------------------------------------------------
# AllowlistProxy: real local TCP proxy, real allow/deny enforcement.
# ---------------------------------------------------------------------

def test_allowlist_proxy_permits_an_allowed_host_connect():
    proxy = AllowlistProxy(allowed_hosts=["allowed.example.com"])
    proxy.start()
    try:
        sock = socket.create_connection(("127.0.0.1", proxy.port), timeout=2)
        sock.sendall(b"CONNECT allowed.example.com:443 HTTP/1.1\r\nHost: allowed.example.com:443\r\n\r\n")
        response = sock.recv(4096)
        sock.close()
        assert b"200" in response
    finally:
        proxy.stop()
    assert proxy.decisions == [("allowed.example.com", True)]


def test_allowlist_proxy_refuses_a_disallowed_host_connect():
    proxy = AllowlistProxy(allowed_hosts=["allowed.example.com"])
    proxy.start()
    try:
        sock = socket.create_connection(("127.0.0.1", proxy.port), timeout=2)
        sock.sendall(b"CONNECT evil.example.com:443 HTTP/1.1\r\nHost: evil.example.com:443\r\n\r\n")
        response = sock.recv(4096)
        sock.close()
        assert b"403" in response
    finally:
        proxy.stop()
    assert proxy.decisions == [("evil.example.com", False)]


def test_execute_wires_egress_allowlist_via_proxy_env_vars_and_records_decisions():
    """End-to-end through `SandboxRuntime.execute`: a script that makes an
    allowed and a disallowed CONNECT through whatever HTTP_PROXY it was
    handed -- proving `execute()` really points the child at the real
    `AllowlistProxy`, not merely accepting the argument."""
    runtime = SubprocessSandboxRuntime()
    script = (
        "import os, socket\n"
        "proxy = os.environ['HTTP_PROXY'].split('://')[1]\n"
        "host, port = proxy.split(':')\n"
        "def connect(target):\n"
        "    s = socket.create_connection((host, int(port)), timeout=2)\n"
        "    s.sendall(f'CONNECT {target}:443 HTTP/1.1\\r\\n\\r\\n'.encode())\n"
        "    r = s.recv(4096)\n"
        "    s.close()\n"
        "    return r\n"
        "print(connect('good.example.com').decode(errors='replace'))\n"
        "print(connect('bad.example.com').decode(errors='replace'))\n"
    )
    result = runtime.execute(
        [sys.executable, "-c", script],
        resource_limits=ResourceLimits(timeout_seconds=5),
        egress_allowlist=["good.example.com"],
    )
    assert "200" in result.stdout
    assert "403" in result.stdout
    assert result.returncode == 0
