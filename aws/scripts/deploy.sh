#!/usr/bin/env bash
# Create or update the Probe Watch stack from AWS CloudShell. AWS CLI only.
#
#   scripts/deploy.sh you@example.com            # phase 1: WAF rule in COUNT mode
#   scripts/deploy.sh you@example.com BLOCK      # phase 2: same stack, rule now blocks
#
# Env: STACK_NAME (default probe-watch). The region comes from the CloudShell session.
set -euo pipefail

EMAIL="${1:?usage: scripts/deploy.sh ALERT_EMAIL [COUNT|BLOCK]}"
MODE="${2:-COUNT}"
STACK="${STACK_NAME:-probe-watch}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

case "$MODE" in COUNT|BLOCK) ;; *) echo "WafMode must be COUNT or BLOCK" >&2; exit 2 ;; esac

echo "deploying $STACK with WafMode=$MODE (this takes 2-4 minutes)"
aws cloudformation deploy \
  --stack-name "$STACK" \
  --template-file "$HERE/probe-watch.yaml" \
  --capabilities CAPABILITY_IAM \
  --no-fail-on-empty-changeset \
  --parameter-overrides "AlertEmail=$EMAIL" "WafMode=$MODE"

echo
aws cloudformation describe-stacks --stack-name "$STACK" \
  --query 'Stacks[0].Outputs[].[OutputKey,OutputValue]' --output table

echo
echo "If this was the first deploy: open the 'AWS Notification - Subscription Confirmation'"
echo "email sent to $EMAIL and click Confirm, or the alarm has nowhere to go."
