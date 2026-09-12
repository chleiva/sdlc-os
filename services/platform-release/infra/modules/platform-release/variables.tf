# platform-release module — the platform's own deployed version, as a
# real (if local-standing-in-for-cloud) OpenTofu-managed resource.
#
# See ../../../README.md "What's real vs. a local stand-in" for why this
# uses the `hashicorp/local` provider rather than a cloud provider: no
# live cloud account exists in this build environment (same hard
# constraint F1/D6 document), but `local_file` lets every mechanic this
# deliverable must prove -- a real `tofu apply` deploying "version N", a
# real rollback re-applying a prior version, both leaving a real,
# inspectable artifact on disk, driven by tofu's own state -- be genuine
# and subprocess-executable without any cloud credentials. The
# versioning/rollback *logic* here is identical to what a real
# `orchestrator`/`job-dispatcher`/`run-registry` container-version or
# module-version deploy would do against a real cloud composition (spec
# §14.2/§14.15): apply a version, observe it live, and if needed, apply
# the prior pinned version -- via tofu itself, never a manual undo.

variable "environment" {
  type        = string
  description = "Name of the platform environment this deployment applies to, e.g. \"platform-prod\" or \"platform-canary\". Threaded into every output path/label so two environments never collide on disk."
}

variable "platform_version" {
  type        = string
  description = "The platform release version currently applied, e.g. \"v7\" or \"2026.09.12-1\". This is the single attribute a rollback changes -- re-applying this module with the prior pinned value is what a platform rollback *is* (spec §14.15: \"a tofu apply against a prior state, not a manual undo\")."
}

variable "component_versions" {
  type        = map(string)
  description = "Per-component version pins that make up this platform release, e.g. {orchestrator = \"v7\", dispatcher = \"v7\", registry-service = \"v6\"}. Mirrors spec §14.15's \"orchestrator, dispatcher, Registry-Service version\" -- each is versioned and rolled back independently in principle, but this module records them together as one release manifest for the common case where they move in lockstep."
  default     = {}
}

variable "release_id" {
  type        = string
  description = "Opaque identifier for the release event that produced this apply (e.g. a UUID minted by the Python release orchestrator). Recorded in the manifest purely for audit/traceability -- never interpreted by this module."
}

variable "rolled_back_from" {
  type        = string
  description = "When this apply is itself a rollback, the platform_version it is rolling back *from* (null on an ordinary forward release). Recorded in the manifest so the on-disk artifact says plainly \"this is a rollback\" rather than looking like an ordinary new release."
  default     = null
}

variable "output_dir" {
  type        = string
  description = "Directory the deployed-version manifest is written into. Defaults to this module's own working directory when null."
  default     = null
}
