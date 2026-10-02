#!/usr/bin/env bash
# Run the demo scenarios through the stack's own simulator and print each histogram.
#
#   scripts/demo.sh                 # normal, then loud, then spread
#   scripts/demo.sh loud            # one scenario
#
# Env: STACK_NAME (default probe-watch).
set -euo pipefail

STACK="${STACK_NAME:-probe-watch}"
output() {
  aws cloudformation describe-stacks --stack-name "$STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

FN="$(output SimulatorFunction)"
ALARM="$(output AlarmName)"
MODE="$(output WafMode)"
SCENARIOS=("$@")
if [ $# -eq 0 ]; then SCENARIOS=(normal loud spread); fi

echo "stack: $STACK   waf mode: $MODE   simulator: $FN"
for s in "${SCENARIOS[@]}"; do
  case "$s" in normal|loud|spread) ;; *) echo "unknown scenario: $s (normal|loud|spread)" >&2; exit 2 ;; esac
  echo
  echo "== $s  started $(date -u +%H:%M:%SZ)"
  out="/tmp/probe-watch-$s.json"
  # --cli-read-timeout 0: the spread run takes about two minutes, longer than the CLI default of 60 s.
  meta="$(aws lambda invoke --function-name "$FN" \
    --cli-binary-format raw-in-base64-out --cli-read-timeout 0 \
    --payload "{\"scenario\":\"$s\"}" "$out")"
  if grep -q FunctionError <<<"$meta"; then
    echo "simulator failed:" >&2
    cat "$out" >&2
    echo >&2
    exit 1
  fi
  python3 -m json.tool "$out"
done

echo
echo "== alarm"
aws cloudwatch describe-alarms --alarm-names "$ALARM" \
  --query 'MetricAlarms[0].[AlarmName,StateValue,StateUpdatedTimestamp]' --output text
echo "The alarm evaluates whole 5-minute windows. Give it up to five minutes to change state."
