# sandbox-runtime/gvisor — stub

Not a working gVisor isolation tier. Per this deliverable's constraints,
only the firecracker variant was built out for real in this pass; kata
and gvisor are explicit stubs. Satisfies the module contract (same
`variables.tf`/`outputs.tf` shape as `sandbox-runtime/firecracker`) so
`runtime_class_name` selection works in code review; registers no actual
RuntimeClass or node bootstrap.
