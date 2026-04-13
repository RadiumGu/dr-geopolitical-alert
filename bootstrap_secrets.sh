#!/usr/bin/env bash
# =============================================================================
# bootstrap_secrets.sh — Write secrets.profile values to SSM Parameter Store
# =============================================================================
# Usage:
#   cp secrets.profile.example secrets.profile   # first time only
#   vim secrets.profile                          # fill in your values
#   ./bootstrap_secrets.sh
#
# Options:
#   --region <region>    Override AWS region (default: value in secrets.profile)
#   --dry-run            Print what would be written, without calling AWS
# =============================================================================
set -euo pipefail

PROFILE_FILE="$(dirname "$0")/secrets.profile"
DRY_RUN=false
REGION_OVERRIDE=""

# --- Parse args ---
while [[ $# -gt 0 ]]; do
  case $1 in
    --dry-run) DRY_RUN=true ;;
    --region)  REGION_OVERRIDE="$2"; shift ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
  shift
done

# --- Load profile ---
if [[ ! -f "$PROFILE_FILE" ]]; then
  echo "❌  secrets.profile not found."
  echo "    Run: cp secrets.profile.example secrets.profile"
  exit 1
fi

# shellcheck disable=SC1090
source "$PROFILE_FILE"

REGION="${REGION_OVERRIDE:-${AWS_REGION:-us-west-2}}"

echo "📍 Region: $REGION"
$DRY_RUN && echo "🔍 Dry-run mode — no AWS calls will be made"
echo ""

# --- Helper: put one SSM parameter ---
put_param() {
  local name="$1"
  local value="$2"
  local description="$3"
  local type="${4:-SecureString}"

  if [[ -z "$value" ]]; then
    echo "⏭️  Skipping $name (empty)"
    return
  fi

  if $DRY_RUN; then
    echo "✅ [dry-run] would write: $name"
    return
  fi

  aws ssm put-parameter \
    --region "$REGION" \
    --name "$name" \
    --value "$value" \
    --description "$description" \
    --type "$type" \
    --overwrite \
    --output json > /dev/null

  echo "✅ Written: $name"
}

# --- Write each secret ---
put_param "/dr-alert/acled-api-key"         "${ACLED_API_KEY:-}"         "ACLED API key for conflict collector"
put_param "/dr-alert/acled-email"           "${ACLED_EMAIL:-}"           "ACLED account email for conflict collector"
put_param "/dr-alert/ucdp-access-token"     "${UCDP_ACCESS_TOKEN:-}"     "UCDP access token for conflict collector"
put_param "/dr-alert/cf-radar-token"        "${CF_RADAR_TOKEN:-}"        "Cloudflare Radar API token for BGP collector"
put_param "/dr-alert/cloudflare-api-token"  "${CLOUDFLARE_API_TOKEN:-}"  "Cloudflare API token for infrastructure collector (optional)"
put_param "/dr-alert/slack-webhook-url"     "${SLACK_WEBHOOK_URL:-}"     "Slack incoming webhook URL for notifications" "String"

echo ""
echo "🎉 Done. You can now run: cdk deploy"
