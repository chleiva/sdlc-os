#!/usr/bin/env bash
# Node bootstrap for the firecracker sandbox-runtime tier (spec §10.1).
# Rendered by main.tf's `local.bootstrap_script` and intended to run as
# part of node launch (EC2 user data on AWS) BEFORE kubelet registers the
# node, so the containerd shim is in place when the RuntimeClass's
# matching pods get scheduled.
#
# This installs firecracker-containerd and registers it as a containerd
# runtime named "firecracker", matching the RuntimeClass handler this
# module registers (${runtime_class_name}).
set -euo pipefail

FIRECRACKER_CONTAINERD_VERSION="v1.0.0"

# Install the firecracker-containerd shim binaries.
curl -fsSL \
  "https://github.com/firecracker-microvm/firecracker-containerd/releases/download/$${FIRECRACKER_CONTAINERD_VERSION}/firecracker-containerd.tgz" \
  -o /tmp/firecracker-containerd.tgz
tar -xzf /tmp/firecracker-containerd.tgz -C /usr/local/bin

# Register the shim with containerd so a pod's runtimeClassName
# "${runtime_class_name}" resolves to it.
cat <<'TOML' >> /etc/containerd/config.toml
[plugins."io.containerd.grpc.v1.cri".containerd.runtimes.firecracker]
  runtime_type = "aws.firecracker"
TOML

systemctl restart containerd
