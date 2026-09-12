# Partial backend configuration, deliberately. Bucket/key/region/lock
# table are supplied at `tofu init` time via -backend-config flags (see
# infra/scripts/bootstrap.sh step 1), because the region this environment
# lands in is a live spot-price/interruption-frequency discovery result
# (spec §14.8), never a value hardcoded in a committed file. Nothing
# secret lives in backend config either way (bucket names/regions are not
# secrets), but the *value* is intentionally not fixed here so a repeat
# bring-up in a different discovered region doesn't require editing
# committed HCL.

terraform {
  required_version = ">= 1.6.0"

  backend "s3" {
    # Supplied via: tofu init \
    #   -backend-config="bucket=<state-bucket>" \
    #   -backend-config="key=pilot-aws-g7e/terraform.tfstate" \
    #   -backend-config="region=<discovered-region>" \
    #   -backend-config="dynamodb_table=<lock-table>" \
    #   -backend-config="encrypt=true"
  }
}
