#!/usr/bin/env bash
# Run the two Logs Insights queries from the terminal and print the tables.
# Useful when the console is slow during the demo.
#
#   scripts/results.sh          # last 60 minutes
#   scripts/results.sh 30       # last 30 minutes
#
# Env: STACK_NAME (default probe-watch).
set -euo pipefail

STACK="${STACK_NAME:-probe-watch}"
MINUTES="${1:-60}"
output() {
  aws cloudformation describe-stacks --stack-name "$STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

GROUP="$(output EventsLogGroup)"
END="$(date +%s)"
START="$(( END - MINUTES * 60 ))"

run_query() {
  local title="$1" query="$2" id status
  id="$(aws logs start-query --log-group-name "$GROUP" \
        --start-time "$START" --end-time "$END" \
        --query-string "$query" --query queryId --output text)"
  status=Running
  while [ "$status" = Running ] || [ "$status" = Scheduled ]; do
    sleep 2
    status="$(aws logs get-query-results --query-id "$id" --query status --output text)"
  done
  echo
  echo "== $title  ($status, last $MINUTES min)"
  echo "   $query"
  # Header from the first row's field names, then one tab-separated line per row.
  aws logs get-query-results --query-id "$id" --query 'results[0][*].field' --output text
  aws logs get-query-results --query-id "$id" --query 'results[*][*].value' --output text
}

run_query "Per device: attempts and distinct cards per 5-minute window" "$(output PerDeviceQuery)"
run_query "Checkout-wide: probe-shaped attempts per 5-minute window" "$(output CheckoutWideQuery)"
echo
echo "Leads: a device window with 20+ attempts and 15+ cards; a checkout window with 20+ candidates."
