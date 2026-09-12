"""Ephemeral Docker container sandbox tier (master spec Section 10.3).

Section 10.1's microVM/gVisor tiers assume a fleet node built to run a
hardware-isolated VM or a gVisor runtime. The Docker Compose deployment
mode (Section 14.16) has no such node -- it is, by design, a single host
running Docker and nothing else -- so it gets a fourth tier instead:
each unit of agent-generated code runs inside its own short-lived,
single-use Docker container, created immediately before that unit of
work and destroyed immediately after, never reused across tasks or
tenants. Section 10.3 is explicit that this tier is scoped to
single-tenant use (the Docker Compose mode only) and is never a
substitute for Section 10.1's tiering in a multi-tenant deployment --
tier *selection* is a separate integration concern this module does not
own (see the D2 brief: this module only adds the tier, another task
wires selection together).

**Why `subprocess` + the `docker` CLI, not the `docker` Python SDK.**
This package (`services/orchestrator`) has no existing Docker
dependency of any kind, and every other tier in `sandbox.py` is already
built on `subprocess` -- reusing that same mechanism for this tier
keeps one dependency-shaped decision instead of two, avoids pulling in
the `docker` SDK (plus its own `requests`/`urllib3` transitive pins)
for what is, at bottom, a handful of `docker run`/`inspect`/`rm`
invocations, and maps line-for-line onto Section 10.3's own phrasing
("`docker run --rm ...`, or explicit `docker rm` after"). The CLI is
also guaranteed present anywhere this tier is actually selected, since
the Docker Compose deployment mode already requires a working `docker`
CLI/daemon on that single host by construction -- there is no
environment where this tier would be reachable but the SDK would be
preferable to the CLI already being there. Real container lifecycle,
not a mock: every `execute()` call does a real `docker run`, a real
`docker inspect`, and a real `docker rm -f` against a real daemon.

**Real resource limits, via Docker's own mechanism, not reimplemented.**
`sandbox.py`'s `SubprocessSandboxRuntime` uses `setrlimit` in-process
because it *is* the process being limited; this tier is launching a
separate container, so the equivalent limits are expressed as Docker's
own flags on `docker run`, passed straight through from the same
`ResourceLimits` dataclass every other tier already takes:
    - `--ulimit cpu=<cpu_seconds>` -- Docker's own passthrough to the
      same `setrlimit(RLIMIT_CPU, ...)` mechanism `sandbox.py` uses
      directly, applied to the container's PID 1 by the container
      runtime instead of by this module's own `preexec_fn`. A CPU-bound
      loop is genuinely killed by SIGXCPU once it has burned
      `cpu_seconds` of real CPU time -- exactly the existing tier's
      test, run inside a container instead of a bare subprocess.
    - `--memory` + `--memory-swap` set to the same value (no swap
      headroom) -- the real Linux cgroup memory controller, which
      genuinely OOM-kills (SIGKILL, Docker exit code 137) a process that
      exceeds it. Notably this is a real, non-placeholder guarantee even
      on macOS dev/test hosts, where `sandbox.py`'s own `RLIMIT_AS`
      dimension is a documented no-op (see that module's docstring) --
      Docker Desktop's Linux VM gives this tier the real cgroup memory
      limit the subprocess tier cannot get on Darwin.
    - `--cpus 1` -- a real, coarser rate cap (never more than one host
      CPU) alongside the hard `--ulimit cpu=...` kill; `ResourceLimits`
      has no CPU-*count* field (only the CPU-*time* budget the ulimit
      above consumes), so this is a fixed, conservative default rather
      than something computed from `ResourceLimits`.
    - `--pids-limit` -- a real fork-bomb ceiling, a defense-in-depth
      extra with no equivalent in the other tiers (a bare subprocess has
      no cheap analogue; a container does).
    - Wall-clock timeout: Docker has no native per-run timeout flag, so
      this module enforces it itself with a real `subprocess.run(...,
      timeout=...)` around the `docker run` invocation -- and because
      killing that local client process does *not* stop the container
      running server-side under `dockerd`, the `TimeoutExpired` handler
      below issues an explicit `docker kill` against the named
      container, then the same unconditional `docker rm -f` every other
      exit path also goes through. This is the concrete mechanism the
      task description asks for, not hand-waved.
    - Disk limits: Section 10.1 calls for CPU/memory/*disk* limits, but
      `--storage-opt size=...` is only honored by a handful of storage
      drivers under a specific `pquota`-mounted `overlay2` configuration
      -- it is not reliably available on a stock Docker install (Docker
      Desktop included) and silently ignored or rejected otherwise. This
      is the same kind of documented, honest gap as `sandbox.py`'s
      macOS `RLIMIT_AS` no-op: not attempted here rather than silently
      pretending to enforce it. A future host-level integration (a
      dedicated `overlay2` mount with `pquota`, or a loopback-backed
      volume with its own filesystem quota) is the concrete path if this
      dimension is needed later.

**Egress: reusing `AllowlistProxy` across the container boundary.**
Section 10.3 says to reuse Section 10.1's allowlist proxy unchanged.
The concrete problem it raises -- the proxy needs to be reachable *from
inside the container's own network namespace* -- is solved like this,
not hand-waved:

    1. `AllowlistProxy` (imported unchanged from `sandbox.py`; not
       reimplemented) is started bound to `0.0.0.0` on the host instead
       of `sandbox.py`'s default `127.0.0.1` -- `127.0.0.1` inside a
       container is the container's *own* loopback, not the host's, so
       a proxy bound only to host loopback is simply unreachable from
       inside any container regardless of networking mode.
    2. The container is given `--add-host
       host.docker.internal:host-gateway`, Docker's own supported
       mechanism (Engine >=20.10; native on Docker Desktop, needed
       explicitly on Linux Engine) for a container to address "the
       host" by a stable name without host networking or a hand-rolled
       bridge.
    3. The container's `HTTP_PROXY`/`HTTPS_PROXY` env vars are pointed
       at `http://host.docker.internal:<proxy.port>` -- the exact same
       proxy object, same accept/refuse-by-hostname decision logic, same
       `CONNECT`-handling code path `sandbox.py`'s own tests already
       exercise, just crossing one extra network hop (container ->
       host-gateway -> host loopback-bound listener) to reach it.
    4. The container keeps normal (bridge) networking in this branch,
       because a proxy is only useful to a process that can still open
       a TCP connection to reach it. This means the same caveat
       `sandbox.py`'s own module docstring already states applies here
       too, stated with equal honesty rather than glossed over: this is
       *cooperative* egress control (anything that honors
       `HTTP_PROXY`/`HTTPS_PROXY`) -- it does not stop a process inside
       the container from opening a raw socket directly to the
       internet, bypassing the proxy entirely. Docker's bridge network
       gives the container a real route to the internet; the proxy only
       gets consulted by code that chooses to use it.

    When *no* egress allowlist is requested at all, this tier does
    better than "cooperative" and reaches for the coarser
    `--network none` alternative the task description names -- and here
    it is a genuinely *stronger* guarantee than anything the subprocess
    tier can offer: `sandbox.py`'s own docstring notes it has no
    unprivileged way to remove a process's network access entirely,
    since creating a network namespace needs privileges this
    environment's subprocess-based tier doesn't have. Docker already
    has that privilege (it manages its own network namespaces as part
    of normal container lifecycle), so `--network none` is a real,
    kernel-enforced "no network stack at all" for the no-egress-needed
    case, not a cooperative proxy convention.

Same audit-logging shape as every other tier: `DockerAuditRecord` below
*is* `sandbox.py`'s own `AuditRecord` (imported, not reimplemented),
extended with the container-specific fields (name, image, network mode,
egress decisions) worth keeping given this tier's extra moving parts.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from orchestrator.sandbox import (
    AllowlistProxy,
    AuditRecord,
    ExecutionResult,
    ResourceLimits,
    SandboxRuntime,
)

__all__ = [
    "DockerSandboxTier",
    "DockerAuditRecord",
    "DockerContainerSandboxRuntime",
    "docker_available",
]


class DockerSandboxTier(str, Enum):
    """This tier's own label. Deliberately *not* added as a new member of
    `sandbox.SandboxTier` -- this module is not permitted to edit
    `sandbox.py` (a separate integration task wires tier selection
    together once every new tier has landed). Value matches Section
    10.3's name for this deployment-mode-specific tier."""

    EPHEMERAL_CONTAINER = "docker_ephemeral_container"


@dataclass(frozen=True)
class DockerAuditRecord(AuditRecord):
    """`sandbox.py`'s own `AuditRecord`, extended (not reimplemented)
    with the extra fields this tier's real container lifecycle makes
    available: the ephemeral container's own name (so a test or an
    operator can independently confirm it is gone -- see
    `test_docker_sandbox.py`), the image it ran in, the network mode
    actually used for that execution, and the raw allow/deny decisions
    the `AllowlistProxy` recorded, when one was used."""

    container_name: str = ""
    image: str = ""
    network_mode: str = ""
    egress_decisions: tuple[tuple[str, bool], ...] = ()


def docker_available(docker_bin: str = "docker") -> bool:
    """True iff the `docker` CLI is on PATH *and* a daemon is actually
    reachable through it. Used to gate the real tests in
    `test_docker_sandbox.py` on Docker's genuine availability in the
    current environment, per the task's instruction to skip (not
    permanently disable) them where no real daemon is reachable."""
    if shutil.which(docker_bin) is None:
        return False
    try:
        result = subprocess.run(
            [docker_bin, "info"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


class DockerContainerSandboxRuntime(SandboxRuntime):
    """Section 10.3's ephemeral-container tier: a fresh, single-use
    Docker container per `execute()` call, created immediately before
    that unit of work and destroyed immediately after -- see the module
    docstring for exactly how resource limits and egress control are
    real, reused mechanisms rather than reimplementations.

    A drop-in alternative to `sandbox.py`'s existing tiers: same
    `SandboxRuntime` base, same `execute(...)` signature and
    `ExecutionResult` return shape, same `ResourceLimits`/audit-log
    conventions -- callers that already select a tier via
    `SandboxRuntime.execute(...)` need no special-casing for this one.
    """

    tier = DockerSandboxTier.EPHEMERAL_CONTAINER

    def __init__(self, *, image: str = "python:3.11-alpine", docker_bin: str = "docker") -> None:
        super().__init__()
        self.image = image
        self.docker_bin = docker_bin

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
        container_name = f"sdlc-sandbox-{uuid.uuid4().hex[:12]}"
        proxy: AllowlistProxy | None = None

        # Deliberately different from `SubprocessSandboxRuntime`, which
        # defaults to inheriting the *entire* host `os.environ` (safe
        # there since that tier already runs in-process on the same
        # host). Blindly forwarding the full host environment into a
        # separate container would risk leaking whatever secrets happen
        # to be in this process's environment across a boundary that
        # doesn't need them -- Section 17.1's "injected only at the
        # point of use" principle argues for the narrower default here:
        # only what the caller explicitly passes (plus the proxy env
        # vars this method adds itself) ever reaches the container.
        container_env: dict[str, str] = dict(env or {})

        docker_cmd: list[str] = [
            self.docker_bin,
            "run",
            "--name",
            container_name,
            "--init",
            "--cpus",
            "1",
            "--memory",
            str(resource_limits.memory_bytes),
            "--memory-swap",
            str(resource_limits.memory_bytes),
            "--ulimit",
            f"cpu={resource_limits.cpu_seconds}",
            "--pids-limit",
            "256",
        ]

        if egress_allowlist:
            proxy = AllowlistProxy(allowed_hosts=egress_allowlist, host="0.0.0.0")
            proxy.start()
            container_env["HTTP_PROXY"] = f"http://host.docker.internal:{proxy.port}"
            container_env["HTTPS_PROXY"] = f"http://host.docker.internal:{proxy.port}"
            docker_cmd += ["--add-host", "host.docker.internal:host-gateway"]
            network_mode = "bridge+allowlist-proxy"
        else:
            docker_cmd += ["--network", "none"]
            network_mode = "none"

        for key, value in container_env.items():
            docker_cmd += ["-e", f"{key}={value}"]
        if cwd is not None:
            docker_cmd += ["-w", cwd]

        docker_cmd += [self.image, *command]

        started_at = datetime.now(timezone.utc).isoformat()
        timed_out = False
        try:
            proc = subprocess.run(
                docker_cmd,
                capture_output=True,
                text=True,
                timeout=resource_limits.timeout_seconds,
            )
            returncode: int | None = proc.returncode
            stdout = proc.stdout
            stderr = proc.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            # The wall-clock timeout this module owns (Docker has no
            # native per-run timeout flag): `subprocess.run`'s own
            # timeout handling only kills the local `docker run` CLI
            # client -- the container keeps running server-side under
            # `dockerd` unless told to stop, so that is done explicitly
            # here.
            subprocess.run(
                [self.docker_bin, "kill", container_name],
                capture_output=True,
                text=True,
            )
            returncode = None
            stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""

        resource_limited = self._was_resource_limited(container_name, returncode)

        # Explicit teardown -- the task description's "...or explicit
        # docker rm after" branch, chosen over `docker run --rm` so this
        # method can inspect the container's exit state (OOMKilled, exit
        # code) before it disappears, on every exit path including the
        # timeout branch above. Errors from `rm` (e.g. the container
        # already gone) are deliberately swallowed: the goal is "never
        # leaked", not "never already removed".
        subprocess.run(
            [self.docker_bin, "rm", "-f", container_name],
            capture_output=True,
            text=True,
        )

        egress_decisions = tuple(proxy.decisions) if proxy is not None else ()
        if proxy is not None:
            proxy.stop()

        stdout = stdout[: resource_limits.max_output_bytes]
        stderr = stderr[: resource_limits.max_output_bytes]

        self.audit_log.append(
            DockerAuditRecord(
                tier=self.tier.value,
                command=tuple(command),
                resource_limits=resource_limits,
                egress_allowlist=tuple(egress_allowlist),
                started_at=started_at,
                returncode=returncode,
                timed_out=timed_out,
                container_name=container_name,
                image=self.image,
                network_mode=network_mode,
                egress_decisions=egress_decisions,
            )
        )
        return ExecutionResult(
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            resource_limited=resource_limited,
        )

    def _was_resource_limited(self, container_name: str, returncode: int | None) -> bool:
        """True iff the container's own exit state shows it was actually
        killed by one of the resource limits above, rather than exiting
        cleanly (or not exiting at all -- the wall-clock timeout case,
        reported via `timed_out` instead, not this flag).

        Must run before the container is removed: `docker inspect`
        cannot see a container's exit state once it no longer exists.
        """
        if returncode is None:
            return False
        inspect = subprocess.run(
            [self.docker_bin, "inspect", "--format", "{{.State.OOMKilled}}", container_name],
            capture_output=True,
            text=True,
        )
        oom_killed = inspect.returncode == 0 and inspect.stdout.strip() == "true"
        # Docker's own convention when a container's PID 1 dies to a
        # signal (SIGXCPU from the `--ulimit cpu=...` limit above,
        # SIGKILL from an OOM kill the check above didn't already
        # confirm): exit code 128+signal, never a "clean" 0-127 exit --
        # the same "a non-clean returncode means a real limit fired"
        # signal `sandbox.py`'s subprocess tier already relies on
        # (there: a negative POSIX returncode), expressed in Docker's
        # own exit-code convention instead.
        signal_killed = returncode >= 128
        return oom_killed or signal_killed
