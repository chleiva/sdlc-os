#!/usr/bin/env bash
# infra/scripts/lib/seed-secrets.sh <environment-dir> <environment-name> <auto-mode>
#
# Step 3 of bootstrap.sh (spec §14.7): "Secret bootstrap — interactive
# locally, or injected from the CI system's own secret store in an
# automated pipeline." This is the ONLY place a secret value should ever
# be typed/read in this whole module tree — it goes straight from here
# (stdin prompt, or the calling CI job's own environment variables) to
# `aws secretsmanager put-secret-value`, never through a `.tf`/`.tfvars`
# file and never through `tofu apply` (spec §14.6, and this deliverable's
# acceptance criterion "no secret ever appears in tofu plan/state").
set -euo pipefail

ENV_DIR="$1"
ENVIRONMENT="$2"
AUTO_MODE="$3"

cd "$ENV_DIR"

# secret_names is read the same way spot-discovery.sh reads its
# variables — from this environment's own OpenTofu config, so the list
# of secrets to seed can never silently drift from what
# modules/secrets/aws-secrets-manager actually provisioned containers
# for in step 2.
SECRET_NAMES_RAW="$(echo 'var.secret_names' | tofu console -var-file=terraform.tfvars 2>/dev/null \
  || echo 'var.secret_names' | tofu console 2>/dev/null)"

# shellcheck disable=SC2207
SECRET_NAMES=($(python3 -c "
import re, sys
print(' '.join(re.findall(r'\"([^\"]+)\"', sys.argv[1])))
" "$SECRET_NAMES_RAW"))

if [[ ${#SECRET_NAMES[@]} -eq 0 ]]; then
  echo "seed-secrets: no secret_names found for $ENVIRONMENT — nothing to seed" >&2
  exit 0
fi

REGION="$(tofu output -raw region 2>/dev/null || echo "${SDLC_AUTO_STATE_REGION:-us-east-1}")"

for name in "${SECRET_NAMES[@]}"; do
  SECRET_ID="${ENVIRONMENT}/${name}"
  ENV_VAR_NAME="SDLC_AUTO_SECRET_$(echo "$name" | tr '[:lower:]-' '[:upper:]_')"

  if [[ "$AUTO_MODE" == "true" ]]; then
    # CI mode: the calling pipeline's own secret store already injected
    # this as an environment variable (e.g. from GitHub Actions
    # encrypted secrets, or Vault-agent-templated env). We only relay
    # it into AWS Secrets Manager; we never generate, log, or persist it
    # ourselves.
    VALUE="${!ENV_VAR_NAME:-}"
    if [[ -z "$VALUE" ]]; then
      echo "seed-secrets: --auto mode but $ENV_VAR_NAME is unset — CI's secret store must inject this before calling bootstrap.sh" >&2
      exit 1
    fi
  else
    # Interactive local mode.
    read -r -s -p "Value for secret '${name}' (input hidden): " VALUE
    echo >&2
  fi

  aws secretsmanager put-secret-value \
    --region "$REGION" \
    --secret-id "$SECRET_ID" \
    --secret-string "$VALUE" \
    >/dev/null \
    || { echo "seed-secrets: failed to write $SECRET_ID" >&2; exit 1; }

  unset VALUE
  echo "seed-secrets: wrote ${SECRET_ID} (value not logged)" >&2
done
