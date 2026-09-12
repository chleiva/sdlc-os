# sandbox-runtime/firecracker

The isolation tier built out for real in this pass (spec §10.1). Registers
a Kubernetes RuntimeClass and renders (but does not yet wire in) a node
bootstrap script installing the firecracker-containerd shim.

**Known integration gap:** `gpu-node-pool/aws`'s `EC2NodeClass` does not
yet accept this module's `bootstrap_script` output as its `userData`.
Until that one wire-up is added, nodes come up without the shim
installed, so pods requesting `runtimeClassName: sdlc-auto-sandbox` will
fail to schedule. This is a small, well-defined follow-up, not a
redesign — flagged explicitly rather than silently left broken.
