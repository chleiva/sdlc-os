"""D10 adversarial pass on D2 (orchestrator) Rev 9's ephemeral Docker
container sandbox tier: `orchestrator.docker_sandbox.DockerContainerSandboxRuntime`
(master spec Section 10.3). Against the REAL orchestrator code, driven
against a real local Docker daemon when one is reachable
(`orchestrator.docker_sandbox.docker_available()` -- reused, not
reinvented) and cleanly SKIPPED (not disabled) otherwise, same
discipline as `services/orchestrator/tests/test_docker_sandbox.py`.

Four real adversarial attempts, each an actual attempt against the real
runtime/a real container, not a description of one:

  (a) container escape via a bind-mount/volume path-traversal argument
      -- proves `execute()` has no such parameter to abuse at all (a
      caller-supplied `volumes=[...]`/`mount=...` kwarg is rejected
      outright by the real call signature), backed by a structural
      (grep) sweep of `docker_sandbox.py` confirming it never
      constructs a `-v`/`--volume`/`--mount` flag anywhere, and by a
      real container run that tries to read an on-host secret file by
      its exact absolute host path and fails to see it.
  (b) reaching the Docker host's own `docker.sock` from inside the
      sandboxed container -- proves the path does not exist inside the
      container and a real AF_UNIX connect attempt against it is
      refused, plus a structural sweep confirming `docker_sandbox.py`
      never mounts/references it.
  (c) a real fork-bomb-style command -- proves the real
      `--pids-limit` flag this runtime always passes genuinely halts
      runaway forking (fork() calls inside the container fail for
      real) well within a short wall-clock bound, rather than the
      call hanging or the host being put at risk.
  (d) reaching an arbitrary external host with no egress allowlist
      configured -- proves the real `--network none` this runtime
      falls back to in that case is a genuine, kernel-enforced absence
      of any network stack (a raw socket connect fails, not merely "no
      proxy env var was set").
"""

from __future__ import annotations

import inspect
import time
from pathlib import Path

import pytest

from orchestrator import docker_sandbox
from orchestrator.docker_sandbox import DockerContainerSandboxRuntime, docker_available
from orchestrator.sandbox import ResourceLimits

pytestmark = pytest.mark.skipif(
    not docker_available(),
    reason="No real Docker daemon reachable via the `docker` CLI in this environment.",
)

_DOCKER_SANDBOX_SOURCE = Path(docker_sandbox.__file__).read_text()


# ---------------------------------------------------------------------------
# (a) Bind-mount / volume path-traversal escape.
# ---------------------------------------------------------------------------


def test_execute_has_no_volume_mount_or_bind_parameter_to_abuse():
    """Structural proof, before even attempting a real escape: the real
    `execute()` call signature has no parameter whose name suggests a
    caller-supplied host mount at all."""
    sig = inspect.signature(DockerContainerSandboxRuntime.execute)
    suspicious = {
        name for name in sig.parameters if any(k in name.lower() for k in ("volume", "mount", "bind", "hostpath"))
    }
    assert suspicious == set(), f"execute() must not accept a volume/mount/bind-shaped parameter: {suspicious}"


def test_execute_rejects_a_caller_supplied_volumes_kwarg_outright():
    """A real attempt: try to smuggle a host bind-mount through the real
    call, exactly the way a caller who assumed this parameter existed
    (or a caller trying to abuse one) would. It must be rejected by
    Python's own argument binding before any container is even
    started."""
    runtime = DockerContainerSandboxRuntime()
    with pytest.raises(TypeError):
        runtime.execute(  # type: ignore[call-arg]
            ["true"],
            resource_limits=ResourceLimits(timeout_seconds=5),
            volumes=["/:/host"],
        )


def test_docker_sandbox_source_never_constructs_a_volume_or_mount_flag():
    """Structural (grep) sweep: the real `docker run` invocation this
    module builds never includes a `-v`/`--volume`/`--mount` flag
    anywhere in its source, i.e. there is no code path -- reachable or
    not through the public API -- that could mount a host path into the
    container."""
    forbidden_tokens = ('"-v"', "'-v'", "--volume", "--mount")
    offenders = [t for t in forbidden_tokens if t in _DOCKER_SANDBOX_SOURCE]
    assert offenders == [], f"docker_sandbox.py must never construct a volume/mount flag: {offenders}"


def test_real_path_traversal_attempt_cannot_reach_an_on_host_secret_file(tmp_path):
    """A real attempt: a malicious command tries to read an exact,
    absolute on-host path (the shape an attacker would try if they
    assumed -- correctly, for many container runtimes when misconfigured
    -- that the host filesystem might be reachable via a bind mount or a
    path-traversal escape). With no mount of any kind, this must fail
    from inside a real container."""
    host_secret = tmp_path / "host_only_secret.txt"
    host_secret.write_text("HOST-SECRET-DO-NOT-LEAK-0123456789")

    runtime = DockerContainerSandboxRuntime()
    script = (
        f"cat '{host_secret}' 2>&1; echo EXIT:$?\n"
        # Also try a classic relative path-traversal shape, in case a
        # future change ever gave the container a partial/mis-scoped
        # mount somewhere -- this must fail too.
        f"cat '../../../../../../../..{host_secret}' 2>&1; echo EXIT:$?\n"
    )
    result = runtime.execute(["sh", "-c", script], resource_limits=ResourceLimits(timeout_seconds=10))

    assert "HOST-SECRET-DO-NOT-LEAK" not in result.stdout
    assert "HOST-SECRET-DO-NOT-LEAK" not in result.stderr
    assert "No such file" in result.stdout or "No such file" in result.stderr


# ---------------------------------------------------------------------------
# (b) Reaching the Docker host's own docker.sock from inside the sandbox.
# ---------------------------------------------------------------------------


def test_docker_sandbox_source_never_mounts_or_references_the_docker_socket():
    assert "docker.sock" not in _DOCKER_SANDBOX_SOURCE


def test_real_attempt_to_reach_the_docker_socket_from_inside_the_container_fails():
    """A real attempt from inside a real container: check the well-known
    `/var/run/docker.sock` path does not exist, then try a real AF_UNIX
    connect against it anyway -- both must fail. If this ever succeeded,
    the sandboxed container could talk to the host's Docker daemon and
    trivially escape (spin up a new, unsandboxed container, mount the
    host root, etc.)."""
    runtime = DockerContainerSandboxRuntime()
    script = (
        "import os, socket\n"
        "print('SOCK_EXISTS', os.path.exists('/var/run/docker.sock'))\n"
        "try:\n"
        "    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
        "    s.settimeout(3)\n"
        "    s.connect('/var/run/docker.sock')\n"
        "    print('CONNECTED')\n"
        "except OSError as exc:\n"
        "    print('REFUSED', exc)\n"
    )
    result = runtime.execute(["python3", "-c", script], resource_limits=ResourceLimits(timeout_seconds=10))

    assert result.timed_out is False
    assert "SOCK_EXISTS False" in result.stdout
    assert "CONNECTED" not in result.stdout
    assert "REFUSED" in result.stdout


# ---------------------------------------------------------------------------
# (c) Fork-bomb-style command vs. the real --pids-limit.
# ---------------------------------------------------------------------------


def test_real_fork_bomb_is_genuinely_stopped_by_pids_limit_well_before_a_timeout():
    """A real fork-bomb attempt (`f() { f | f & }; f`, the POSIX-portable
    shape that also runs under busybox `ash`, avoiding bash-specific
    `:(){ :|:& };:` syntax quirks under alpine's `/bin/sh`). If
    `--pids-limit` genuinely fires, forking fails for real inside the
    container (`can't fork: Resource temporarily unavailable` on
    busybox), the bomb's own shell then falls through to the rest of
    the script and the call returns quickly and cleanly -- proving
    containment, as opposed to either (i) the call hanging until the
    configured wall-clock timeout because the bomb was never actually
    stopped, or (ii) the host being put at risk by unbounded forking.
    """
    runtime = DockerContainerSandboxRuntime()
    script = (
        "f() { f | f & }\n"
        "f\n"
        "sleep 1\n"
        "echo FORK_BOMB_CONTAINED\n"
    )
    started = time.monotonic()
    result = runtime.execute(
        ["sh", "-c", script],
        resource_limits=ResourceLimits(cpu_seconds=30, timeout_seconds=25),
    )
    elapsed = time.monotonic() - started

    # Contained well before the configured wall-clock timeout -- if
    # --pids-limit had not fired for real, this call would either hang
    # until the 25s timeout (a hung/near-unresponsive host under a real
    # unbounded fork bomb) or never reach the trailing echo at all.
    assert elapsed < 15, f"fork bomb was not contained quickly -- took {elapsed:.1f}s (timeout was 25s)"
    assert result.timed_out is False
    assert "FORK_BOMB_CONTAINED" in result.stdout
    # Direct evidence forking was actually refused for real (not just
    # "nothing forked because the shell didn't try"): busybox's own
    # diagnostic for a fork() call that failed against the pids cgroup
    # controller.
    combined_output = result.stdout + result.stderr
    assert "can't fork" in combined_output or "Resource temporarily unavailable" in combined_output


def test_pids_limit_flag_is_actually_present_on_every_real_invocation():
    """Structural confirmation, paired with the functional proof above,
    that the containment is not incidental: `docker_sandbox.py` always
    passes a real `--pids-limit` flag on every `docker run` it builds,
    not only in some code path."""
    assert '"--pids-limit"' in _DOCKER_SANDBOX_SOURCE


# ---------------------------------------------------------------------------
# (d) Reaching an arbitrary external host with no egress allowlist.
# ---------------------------------------------------------------------------


def test_real_attempt_to_reach_an_arbitrary_external_host_with_no_allowlist_is_blocked():
    """A real attempt, with no `egress_allowlist` at all: per
    `docker_sandbox.py`'s own module docstring, this tier reaches for
    the coarser `--network none` in that case -- a genuinely
    kernel-enforced absence of any network stack, not merely
    "cooperative proxy convention, unconfigured". A raw socket connect
    to a real, arbitrary external IP (bypassing any notion of a proxy
    entirely) must fail, and a DNS resolution attempt against an
    arbitrary hostname must fail too (proving there is no network stack
    at all, not just that routing to that one IP happens to be
    unreachable)."""
    runtime = DockerContainerSandboxRuntime()
    script = (
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 80), timeout=3)\n"
        "    print('CONNECTED_IP')\n"
        "except OSError as exc:\n"
        "    print('REFUSED_IP', exc)\n"
        "try:\n"
        "    socket.gethostbyname('example.com')\n"
        "    print('RESOLVED_DNS')\n"
        "except OSError as exc:\n"
        "    print('REFUSED_DNS', exc)\n"
    )
    result = runtime.execute(["python3", "-c", script], resource_limits=ResourceLimits(timeout_seconds=15))

    assert result.timed_out is False
    assert "CONNECTED_IP" not in result.stdout
    assert "REFUSED_IP" in result.stdout
    assert "RESOLVED_DNS" not in result.stdout
    assert "REFUSED_DNS" in result.stdout
    assert runtime.audit_log[-1].network_mode == "none"
