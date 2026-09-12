#!/usr/bin/env bash
# infra/scripts/lib/spot-discovery.sh <environment-dir>
#
# Live spot price + interruption-frequency discovery (spec §14.8): "queries
# current spot price and interruption-frequency rating across the
# environment's configured candidate region/AZ/instance-type set, and
# selects the cheapest pool that clears a configured maximum-interruption-
# frequency threshold — never simply the cheapest price on offer."
#
# Data sources, both real and queried live, not hardcoded:
#   - Interruption-frequency rating: AWS's own public Spot Instance
#     Advisor data feed (the same feed the AWS console's Spot Advisor
#     page reads), giving a 0-4 rating band per (region, instance type).
#   - Current spot price: `aws ec2 describe-spot-price-history` against
#     the caller's own AWS account/credentials, per candidate region.
#
# Requires: aws CLI (authenticated), tofu (to read this environment's
# candidate_regions/instance_types/max_interruption_frequency variables),
# python3.
#
# Prints exactly one line on success: "<region>\t<instance_type>"
# Exits non-zero if every candidate is unreachable/unqueryable (a real
# failure, distinct from "no pool clears the frequency threshold" — the
# latter is a logged on-demand-fallback case, not a hard failure, since
# spec §14.8 says the system pays on-demand and keeps running rather than
# blocking on spot availability).
set -euo pipefail

ENV_DIR="$1"
ADVISOR_DATA_URL="https://spot-bid-advisor.s3.amazonaws.com/spot-advisor-data.json"
ADVISOR_CACHE="/tmp/sdlc-auto-spot-advisor-data.json"
ADVISOR_CACHE_MAX_AGE_SECONDS=86400

cd "$ENV_DIR"

# This environment's own variables define the search space — never a
# region/instance-type hardcoded in this script (spec §14.8's central
# rule). Read via `tofu console` so the *actual configured* candidate set
# (defaults merged with any terraform.tfvars override) is what gets
# queried, not a copy of it duplicated into this script.
tf_console_var() {
  echo "$1" | tofu console -var-file=terraform.tfvars 2>/dev/null \
    || echo "$1" | tofu console 2>/dev/null
}

CANDIDATE_REGIONS_RAW="$(tf_console_var 'var.candidate_regions')"
INSTANCE_TYPES_RAW="$(tf_console_var 'var.instance_types')"
MAX_FREQ_RAW="$(tf_console_var 'var.max_interruption_frequency')"

if [[ -z "$CANDIDATE_REGIONS_RAW" || -z "$INSTANCE_TYPES_RAW" ]]; then
  echo "spot-discovery: could not read candidate_regions/instance_types from $ENV_DIR (does this environment define them?)" >&2
  exit 1
fi

# Refresh the advisor data feed if stale/missing.
if [[ ! -f "$ADVISOR_CACHE" ]] || [[ $(( $(date +%s) - $(stat -f %m "$ADVISOR_CACHE" 2>/dev/null || stat -c %Y "$ADVISOR_CACHE" 2>/dev/null || echo 0) )) -gt $ADVISOR_CACHE_MAX_AGE_SECONDS ]]; then
  curl -fsSL --max-time 30 "$ADVISOR_DATA_URL" -o "$ADVISOR_CACHE" \
    || { echo "spot-discovery: could not fetch $ADVISOR_DATA_URL" >&2; exit 1; }
fi

python3 - "$ADVISOR_CACHE" "$CANDIDATE_REGIONS_RAW" "$INSTANCE_TYPES_RAW" "$MAX_FREQ_RAW" <<'PYEOF'
import json, subprocess, sys, re

advisor_path, regions_hcl, instance_types_hcl, max_freq = sys.argv[1:5]

def parse_hcl_list(s):
    return [x.strip().strip('"') for x in re.findall(r'"([^"]+)"', s)]

regions = parse_hcl_list(regions_hcl)
instance_types = parse_hcl_list(instance_types_hcl)
max_freq = (max_freq or "medium").strip().strip('"')

# Interruption-frequency band index (0=lowest, "<5%") -> label, matching
# the advisor feed's own "ranges" table.
THRESHOLD_INDEX = {"low": 1, "medium": 2, "high": 3, "very-high": 4}.get(max_freq, 2)

with open(advisor_path) as f:
    advisor = json.load(f)

candidates = []  # (region, instance_type, interruption_index)
for region in regions:
    region_data = advisor.get("spot_advisor", {}).get(region, {}).get("Linux", {})
    for itype in instance_types:
        entry = region_data.get(itype)
        if entry is None:
            continue  # this instance type isn't offered as spot in this region
        candidates.append((region, itype, entry["r"]))

qualifying = [c for c in candidates if c[2] <= THRESHOLD_INDEX]

if not qualifying:
    print(
        f"spot-discovery: no (region, instance_type) candidate clears "
        f"max_interruption_frequency={max_freq} (checked {len(candidates)} candidates). "
        f"Falling back to the lowest-interruption-rated candidate available; "
        f"gpu-node-pool's mixed-instance policy will use its on-demand fallback "
        f"at runtime if spot capacity isn't actually available there (spec §14.8).",
        file=sys.stderr,
    )
    if not candidates:
        sys.exit(1)
    candidates.sort(key=lambda c: c[2])
    qualifying = [candidates[0]]

# Among qualifying candidates, pick by live spot price via the AWS CLI —
# real account/region pricing, not the advisor feed's savings estimate.
priced = []
for region, itype, freq_idx in qualifying:
    try:
        out = subprocess.run(
            [
                "aws", "ec2", "describe-spot-price-history",
                "--region", region,
                "--instance-types", itype,
                "--product-descriptions", "Linux/UNIX",
                "--max-items", "1",
                "--query", "SpotPriceHistory[0].SpotPrice",
                "--output", "text",
            ],
            capture_output=True, text=True, timeout=20,
        )
        price = float(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() not in ("", "None") else None
    except Exception:
        price = None
    priced.append((region, itype, freq_idx, price))

# Prefer candidates with a real queried price; fall back to interruption
# rank alone if AWS credentials aren't available in this environment
# (e.g. this script being exercised outside a real AWS account).
with_price = [p for p in priced if p[3] is not None]
if with_price:
    with_price.sort(key=lambda p: p[3])
    chosen = with_price[0]
else:
    print("spot-discovery: no live price available (no AWS credentials?) — selecting by interruption rank only", file=sys.stderr)
    priced.sort(key=lambda p: p[2])
    chosen = priced[0]

print(f"{chosen[0]}\t{chosen[1]}")
PYEOF
