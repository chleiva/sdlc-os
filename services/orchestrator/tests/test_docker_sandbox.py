"""Section 10.3 ephemeral Docker container sandbox tier: real container
lifecycle, real Docker-native resource limits, real egress-allowlist
enforcement reused across the container boundary -- see
`docker_sandbox.py`'s module docstring for exactly how each of these is
real versus a documented, honest gap.

Every test in this module needs a real Docker daemon reachable through
the `docker` CLI. They are guarded with `pytest.mark.skipif` on
`docker_available()` rather than permanently disabled, so they run for
real wherever Docker is actually present (this environment included, at
the time this file was written -- see the final agent report for
whether they were actually executed here) and skip cleanly, with a
clear reason, wherever it is not.
"""

from __future__ import annotations

import socket
import subprocess
import sys

import pytest

from orchestrator.docker_sandbox import (
    DockerContainerSandboxRuntime,
    DockerSandboxTier,
    docker_available,
)
from orchestrator.sandbox import ResourceLimits

pytestmark = pytest.mark.skipif(
    not docker_available(),
    reason="No real Docker daemon reachable via the `docker` CLI in this environment.",
)


def _list_container_names(name_filter: str) -> list[str]:
    result = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name={name_filter}", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def test_real_cpu_time_limit_actually_kills_a_cpu_bound_loop_in_a_real_container():
    runtime = DockerContainerSandboxRuntime()
    result = runtime.execute(
        ["python3", "-c", "\nwhile True:\n    pass\n"],
        resource_limits=ResourceLimits(cpu_seconds=1, timeout_seconds=30),
    )
    # Killed by the container's own real `--ulimit cpu=1` (SIGXCPU) well
    # before the 30s wall-clock timeout would otherwise fire.
    assert result.timed_out is False
    assert result.returncode is not None and result.returncode != 0
    assert result.resource_limited is True


def test_real_memory_limit_actually_refuses_a_large_allocation_in_a_real_container():
    runtime = DockerContainerSandboxRuntime()
    script = "data = bytearray(1024 * 1024 * 1024)\n"  # 1GB, over the 128MB limit below
    result = runtime.execute(
        ["python3", "-c", script],
        resource_limits=ResourceLimits(cpu_seconds=10, memory_bytes=128 * 1024 * 1024, timeout_seconds=30),
    )
    # The real Linux cgroup memory controller (via `--memory`/
    # `--memory-swap`) OOM-kills the process -- unlike `sandbox.py`'s
    # subprocess tier, this is a genuine, non-placeholder guarantee even
    # on a macOS host, since Docker Desktop's Linux VM actually enforces
    # cgroup memory limits.
    assert result.timed_out is False
    assert result.returncode is not None and result.returncode != 0
    assert result.resource_limited is True


def test_egress_to_disallowed_host_is_refused_from_inside_the_container():
    runtime = DockerContainerSandboxRuntime()
    script = (
        "import os, socket\n"
        "proxy = os.environ['HTTP_PROXY'].split('://')[1]\n"
        "host, port = proxy.split(':')\n"
        "s = socket.create_connection((host, int(port)), timeout=5)\n"
        "s.sendall(b'CONNECT evil.example.com:443 HTTP/1.1\\r\\n\\r\\n')\n"
        "print(s.recv(4096).decode(errors='replace'))\n"
    )
    result = runtime.execute(
        ["python3", "-c", script],
        resource_limits=ResourceLimits(timeout_seconds=15),
        egress_allowlist=["good.example.com"],
    )
    assert result.timed_out is False
    assert "403" in result.stdout
    assert result.returncode == 0


def test_egress_to_an_allowed_host_succeeds_from_inside_the_container():
    runtime = DockerContainerSandboxRuntime()
    script = (
        "import os, socket\n"
        "proxy = os.environ['HTTP_PROXY'].split('://')[1]\n"
        "host, port = proxy.split(':')\n"
        "s = socket.create_connection((host, int(port)), timeout=5)\n"
        "s.sendall(b'CONNECT good.example.com:443 HTTP/1.1\\r\\n\\r\\n')\n"
        "print(s.recv(4096).decode(errors='replace'))\n"
    )
    result = runtime.execute(
        ["python3", "-c", script],
        resource_limits=ResourceLimits(timeout_seconds=15),
        egress_allowlist=["good.example.com"],
    )
    assert result.timed_out is False
    assert "200" in result.stdout
    assert result.returncode == 0
    assert runtime.audit_log[-1].egress_decisions == (("good.example.com", True),)


def test_no_egress_allowlist_means_real_network_none_not_just_a_cooperative_proxy():
    """With no allowlist at all, this tier reaches for the coarser
    `--network none` alternative -- a real, kernel-enforced absence of
    any network stack, not merely "no proxy env var set". A raw socket
    connection attempt (bypassing the cooperative-proxy convention
    entirely) must fail, proving this isn't just "the proxy wasn't
    configured"."""
    runtime = DockerContainerSandboxRuntime()
    script = (
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 80), timeout=3)\n"
        "    print('CONNECTED')\n"
        "except OSError as exc:\n"
        "    print('REFUSED', exc)\n"
    )
    result = runtime.execute(
        ["python3", "-c", script],
        resource_limits=ResourceLimits(timeout_seconds=15),
    )
    assert result.timed_out is False
    assert "CONNECTED" not in result.stdout
    assert "REFUSED" in result.stdout
    assert runtime.audit_log[-1].network_mode == "none"


def test_container_is_confirmed_gone_after_execution_completes():
    runtime = DockerContainerSandboxRuntime()
    result = runtime.execute(
        ["python3", "-c", "print('hi')"],
        resource_limits=ResourceLimits(timeout_seconds=10),
    )
    assert result.returncode == 0
    container_name = runtime.audit_log[-1].container_name
    assert container_name.startswith("sdlc-sandbox-")
    # Genuinely ephemeral, not leaked: `docker ps -a` (which shows
    # stopped-but-not-removed containers too) has nothing by this name.
    assert _list_container_names(container_name) == []


def test_isolation_is_real_container_isolation_not_secretly_the_subprocess_tier():
    """The whole point of this deliverable: prove `execute()` genuinely
    runs inside a separate Docker container rather than silently
    delegating to `SubprocessSandboxRuntime` the way the microVM/gVisor
    placeholder tiers currently do (see `sandbox.py`'s module
    docstring). `/.dockerenv` is a file Docker itself creates inside
    every container's root filesystem and nowhere else -- a bare
    subprocess on the host (macOS or a non-containerized Linux host)
    will never see it."""
    host_has_dockerenv = __import__("os").path.exists("/.dockerenv")
    assert not host_has_dockerenv, "this test must itself run outside a container to be meaningful"

    runtime = DockerContainerSandboxRuntime()
    result = runtime.execute(
        [
            "python3",
            "-c",
            "import os, socket, platform\n"
            "print('DOCKERENV', os.path.exists('/.dockerenv'))\n"
            "print('HOSTNAME', socket.gethostname())\n",
        ],
        resource_limits=ResourceLimits(timeout_seconds=10),
    )
    assert result.returncode == 0
    assert "DOCKERENV True" in result.stdout
    # Docker sets the container's hostname to its (truncated) container
    # ID by default -- necessarily different from this test process's
    # own host hostname.
    assert socket.gethostname() not in result.stdout


def test_wall_clock_timeout_is_enforced_by_this_module_and_container_is_still_cleaned_up():
    runtime = DockerContainerSandboxRuntime()
    result = runtime.execute(
        ["python3", "-c", "import time; time.sleep(30)"],
        resource_limits=ResourceLimits(cpu_seconds=60, timeout_seconds=2),
    )
    assert result.timed_out is True
    container_name = runtime.audit_log[-1].container_name
    assert _list_container_names(container_name) == []


def test_execute_appends_a_real_audit_record_with_the_expected_shape():
    runtime = DockerContainerSandboxRuntime()
    runtime.execute(["python3", "-c", "print(1)"], resource_limits=ResourceLimits(timeout_seconds=10))
    record = runtime.audit_log[-1]
    assert record.tier == DockerSandboxTier.EPHEMERAL_CONTAINER.value
    assert record.command == ("python3", "-c", "print(1)")
    assert record.image == "python:3.11-alpine"
    assert record.returncode == 0
    assert record.container_name.startswith("sdlc-sandbox-")


if sys.platform == "win32":  # pragma: no cover - this suite targets the Docker Compose (Linux/macOS) mode
    pytest.skip("Docker Compose deployment mode sandbox tier is not targeted at Windows hosts.", allow_module_level=True)
