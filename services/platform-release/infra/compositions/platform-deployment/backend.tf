# Explicit local backend, deliberately pinned to a fixed, named state
# file rather than left to tofu's own unconfigured default -- this is
# the "versioned state" substrate a real platform rollback re-applies
# against (spec §14.15/§14.2). A real cloud deployment would point this
# at the same remote state backend (e.g. S3 + DynamoDB lock table, as
# infra/environments/pilot-aws-g7e/backend.tf does) that F1's own
# environments use; the *mechanics* a rollback exercises -- tofu reading
# existing state, diffing against a prior pinned config, and applying
# only the delta -- are identical regardless of which backend stores
# that state. See ../../../README.md for the full stand-in rationale.

terraform {
  backend "local" {
    path = "terraform.tfstate"
  }
}
