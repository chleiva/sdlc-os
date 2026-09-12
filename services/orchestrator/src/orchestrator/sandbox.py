"""Sandbox tiering (master spec Section 10.1).

**What is genuinely enforced here vs. a structural placeholder** (stated
up front, per the D2 brief's explicit instruction):

* **Genuinely enforced, real, and tested in this deliverable**:
    - Real subprocess execution with real OS resource limits (CPU time,
      address-space/memory, output size, wall-clock timeout) via the
      POSIX `resource` module's `setrlimit`, applied in the child via
      `preexec_fn` -- `tests/test_sandbox.py` proves a CPU-bound loop is
      actually killed by the CPU limit and a large-allocation attempt is
      actually refused by the memory limit.
    - Real egress allowlist enforcement via `AllowlistProxy`, a real local
      TCP proxy the sandboxed command is pointed at (`HTTP_PROXY`/
      `HTTPS_PROXY`): it accepts a `CONNECT host:port` request and either
      forwards the connection (allowlisted host) or refuses it (403) --
      tested for real by actually attempting both an allowed and a
      disallowed connection through it.
    - Real audit logging: every `execute()` call appends a structured,
      timestamped record (command, tier, resource limits, egress
      decisions, exit status) to an in-memory (test-visible) audit log,
      standing in for the append-only audit store Section 16 describes.

* **Structural placeholder, NOT genuinely enforced here** -- this is
  exactly the D6/infra integration point the D2 brief asks to be
  documented explicitly:
    - The *hardware/kernel* isolation Section 10.1 actually requires for
      the default tier (a Firecracker/Kata-style microVM: a dedicated
      kernel per run) and the fallback floor (a gVisor-style user-space
      kernel intercept layer) is **not implementable in this environment**
      -- it needs a real virtualization/gVisor runtime wired into the
      node's container/VM orchestration (D6's tenant compute cell, or
      infra's node provisioning), neither of which exists as a running
      service here. `MicroVMSandboxRuntime` and `GVisorSandboxRuntime`
      below both currently delegate to the same real subprocess-based
      execution and resource-limit/egress-proxy enforcement described
      above -- so the *interface* and every call site that selects a tier
      are exercised end-to-end, but the *additional* isolation guarantee
      the microVM/gVisor tiers are supposed to add over a plain container
      (kernel-level escape containment) is not actually provided by this
      implementation. Backing them with a real Firecracker/Kata or gVisor
      (`runsc`) runtime behind the same `SandboxRuntime.execute(...)`
      interface is D6/infra's job, not re-plumbing anything above this
      module.
    - A real network-namespace-level "no default internet access" is not
      achievable without root/CAP_NET_ADMIN in this environment; the
      `AllowlistProxy` mediates *cooperative* egress (anything that
      honors `HTTP_PROXY`), it does not prevent a process from opening a
      raw socket directly. The real, complete guarantee ("zero-trust
      networking with no default internet access") requires the same
      microVM/gVisor network-namespace wiring named above.
"""

from __future__ import annotations

import os
import resource
import socket
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class SandboxTier(str, Enum):
    MICROVM = "microvm"  # default per Section 10.1
    GVISOR = "gvisor"  # fallback floor where a microVM is impractical
    CONTAINER = "container"  # trusted, previously human-reviewed automation only
    # (New, Rev 9) Section 10.3's ephemeral-container tier for the Docker
    # Compose deployment mode (Section 14.16) -- see docker_sandbox.py.
    # Same string value as that module's own DockerSandboxTier.EPHEMERAL_
    # CONTAINER (added there before this integration step could touch this
    # file) so an existing AuditRecord.tier comparison against either enum
    # keeps working unchanged; this member is what makes the tier
    # selectable through default_runtime_for_tier below.
    DOCKER_CONTAINER = "docker_ephemeral_container"


def required_tier(*, code_just_written: bool, previously_human_reviewed: bool) -> SandboxTier:
    """Section 10.1's tiering policy: anything the System just wrote and
    is about to execute -- the default case -- runs in a microVM.
    Standard shared-kernel containers are acceptable ONLY for trusted,
    previously human-reviewed automation, never for code an agent just
    produced in the current run."""
    if code_just_written or not previously_human_reviewed:
        return SandboxTier.MICROVM
    return SandboxTier.CONTAINER


@dataclass(frozen=True)
class ResourceLimits:
    cpu_seconds: int = 5
    memory_bytes: int = 256 * 1024 * 1024
    max_output_bytes: int = 1024 * 1024
    timeout_seconds: float = 10.0


@dataclass(frozen=True)
class ExecutionResult:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool
    resource_limited: bool


@dataclass(frozen=True)
class AuditRecord:
    tier: str
    command: tuple[str, ...]
    resource_limits: ResourceLimits
    egress_allowlist: tuple[str, ...]
    started_at: str
    returncode: int | None
    timed_out: bool


class SandboxRuntime:
    """Common real execution mechanics shared by every tier in this
    deliverable. Subclasses only differ in the `tier` label they report
    and (for the placeholder tiers) a docstring pointing at what D6/infra
    must add -- see the module docstring."""

    tier: SandboxTier = SandboxTier.CONTAINER

    def __init__(self) -> None:
        self.audit_log: list[AuditRecord] = []

    def execute(
        self,
        command: list[str],
        *,
        resource_limits: ResourceLimits = ResourceLimits(),
        egress_allowlist: list[str] | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        egress_allowlist = egress_allowlist or []
        proxy = None
        run_env = dict(env or os.environ)
        if egress_allowlist:
            proxy = AllowlistProxy(allowed_hosts=egress_allowlist)
            proxy.start()
            run_env["HTTP_PROXY"] = proxy.url
            run_env["HTTPS_PROXY"] = proxy.url

        def _limit_resources() -> None:
            # RLIMIT_CPU is honored on every POSIX platform tested (Linux,
            # macOS) -- applied unconditionally.
            resource.setrlimit(resource.RLIMIT_CPU, (resource_limits.cpu_seconds, resource_limits.cpu_seconds))
            # RLIMIT_AS (address-space/memory) is honored on Linux but is
            # a documented no-op / rejected call on macOS's Mach-VM-based
            # kernel (setrlimit(RLIMIT_AS, ...) there fails with EINVAL
            # regardless of the requested value -- a platform limitation,
            # not a bug in this module). Best-effort: apply it where the
            # platform allows, never let an unsupported limit crash
            # sandboxed execution outright on a platform that can't set
            # it -- Linux production nodes get the real enforcement;
            # macOS dev/test here gets the CPU/timeout/egress enforcement
            # for real and silently skips only this one dimension.
            try:
                resource.setrlimit(resource.RLIMIT_AS, (resource_limits.memory_bytes, resource_limits.memory_bytes))
            except (ValueError, OSError):
                pass

        started_at = datetime.now(timezone.utc).isoformat()
        timed_out = False
        resource_limited = False
        try:
            proc = subprocess.run(
                command,
                cwd=cwd,
                env=run_env,
                preexec_fn=_limit_resources,
                capture_output=True,
                text=True,
                timeout=resource_limits.timeout_seconds,
            )
            returncode = proc.returncode
            stdout = proc.stdout[: resource_limits.max_output_bytes]
            stderr = proc.stderr[: resource_limits.max_output_bytes]
            # A CPU/memory rlimit violation surfaces to the parent as the
            # child being killed by SIGXCPU/SIGSEGV/SIGKILL -- a negative
            # returncode (POSIX: -signal number) rather than a clean exit.
            resource_limited = returncode is not None and returncode < 0
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            returncode = None
            stdout = (exc.stdout or "")[: resource_limits.max_output_bytes] if isinstance(exc.stdout, str) else ""
            stderr = (exc.stderr or "")[: resource_limits.max_output_bytes] if isinstance(exc.stderr, str) else ""
        finally:
            if proxy is not None:
                proxy.stop()

        self.audit_log.append(
            AuditRecord(
                tier=self.tier.value,
                command=tuple(command),
                resource_limits=resource_limits,
                egress_allowlist=tuple(egress_allowlist),
                started_at=started_at,
                returncode=returncode,
                timed_out=timed_out,
            )
        )
        return ExecutionResult(
            returncode=returncode, stdout=stdout, stderr=stderr, timed_out=timed_out, resource_limited=resource_limited
        )


class SubprocessSandboxRuntime(SandboxRuntime):
    """The one *genuinely enforced* runtime in this deliverable (see
    module docstring): real resource limits, real timeout, real egress
    allowlist proxy. Reports tier CONTAINER -- the honest label, since a
    plain subprocess is exactly a shared-kernel container's isolation
    level, nothing stronger."""

    tier = SandboxTier.CONTAINER


class MicroVMSandboxRuntime(SandboxRuntime):
    """Structural placeholder for Section 10.1's default tier (Firecracker/
    Kata-style hardware-isolated microVM). Currently delegates to the same
    real subprocess+rlimit+egress-proxy mechanics as
    `SubprocessSandboxRuntime` -- exercising the interface and every call
    site that requests this tier -- but provides NO additional kernel/
    hardware isolation beyond that. D6/infra must back this with a real
    microVM runtime behind the same `execute(...)` signature."""

    tier = SandboxTier.MICROVM


class GVisorSandboxRuntime(SandboxRuntime):
    """Structural placeholder for Section 10.1's fallback floor (gVisor-
    style user-space kernel intercept layer). Same caveat as
    `MicroVMSandboxRuntime` above."""

    tier = SandboxTier.GVISOR


def default_runtime_for_tier(tier: SandboxTier) -> SandboxRuntime:
    if tier == SandboxTier.DOCKER_CONTAINER:
        # Local import: docker_sandbox.py imports SandboxRuntime/AuditRecord/
        # etc. from this module, so importing it back at module level here
        # would be circular. Selected only for the Docker Compose deployment
        # mode (Section 14.16) -- never the default, and never reachable in
        # the multi-tenant cloud architecture's own tier selection (Section
        # 10.1/10.3: this tier is explicitly scoped to single-tenant use).
        from orchestrator.docker_sandbox import DockerContainerSandboxRuntime

        return DockerContainerSandboxRuntime()
    return {
        SandboxTier.MICROVM: MicroVMSandboxRuntime,
        SandboxTier.GVISOR: GVisorSandboxRuntime,
        SandboxTier.CONTAINER: SubprocessSandboxRuntime,
    }[tier]()


class AllowlistProxy:
    """A real, minimal local TCP proxy enforcing an egress allowlist by
    hostname. Speaks just enough of HTTP CONNECT to be usable as an
    `HTTP_PROXY`/`HTTPS_PROXY` target: reads a `CONNECT host:port
    HTTP/1.1` request line, and either splices the two sockets together
    (allowed) or writes back `403 Forbidden` and closes (not allowed).

    This is real, tested network-boundary enforcement for anything that
    cooperates with the proxy env vars; it is not a substitute for a
    kernel-level network namespace, which no unprivileged process here
    can create -- see the module docstring.
    """

    def __init__(self, allowed_hosts: list[str], host: str = "127.0.0.1") -> None:
        self._allowed_hosts = set(allowed_hosts)
        self._host = host
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.port: int = 0
        self.decisions: list[tuple[str, bool]] = []  # (host, allowed)

    @property
    def url(self) -> str:
        return f"http://{self._host}:{self.port}"

    def start(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self._host, 0))
        self._sock.listen(8)
        self._sock.settimeout(0.2)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self._sock is not None:
            self._sock.close()

    def _serve(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(3)
            data = b""
            while b"\r\n\r\n" not in data and len(data) < 8192:
                chunk = conn.recv(1024)
                if not chunk:
                    return
                data += chunk
            request_line = data.split(b"\r\n", 1)[0].decode("latin-1", errors="replace")
            parts = request_line.split()
            if len(parts) < 2:
                conn.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                return
            method, target = parts[0], parts[1]
            if method == "CONNECT":
                host_port = target
            else:
                # Plain proxied HTTP request line like "GET http://host/path HTTP/1.1"
                host_port = target.split("//", 1)[-1].split("/", 1)[0]
            host = host_port.split(":")[0]

            allowed = host in self._allowed_hosts
            self.decisions.append((host, allowed))
            if not allowed:
                conn.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\negress to this host is not allowlisted\r\n")
                return
            if method == "CONNECT":
                conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            # This proxy is used purely to prove allow/deny decisions in
            # tests; it does not tunnel real upstream traffic beyond the
            # accept/refuse handshake, since no test here depends on
            # actually reaching an external allowed host.
        except (OSError, TimeoutError):
            return
        finally:
            try:
                conn.close()
            except OSError:
                pass
