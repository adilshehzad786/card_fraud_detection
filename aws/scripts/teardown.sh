#!/usr/bin/env bash
# Delete the stack and check that nothing was left behind.
#
#   scripts/teardown.sh
#
# Env: STACK_NAME (default probe-watch).
set -euo pipefail

STACK="${STACK_NAME:-probe-watch}"

echo "deleting $STACK"
aws cloudformation delete-stack --stack-name "$STACK"
aws cloudformation wait stack-delete-complete --stack-name "$STACK"
echo "stack deleted"

left=0
check() {
  local what="$1" found="$2"
  if [ -n "$found" ]; then echo "still present ($what): $found"; left=1; fi
}
check "log groups" "$(aws logs describe-log-groups --log-group-name-prefix "/probe-watch/$STACK/" \
  --query 'logGroups[].logGroupName' --output text)"
check "functions" "$(aws lambda list-functions \
  --query "Functions[?starts_with(FunctionName, '$STACK-')].FunctionName" --output text)"
check "web ACLs" "$(aws wafv2 list-web-acls --scope REGIONAL \
  --query "WebACLs[?Name=='$STACK-edge'].Name" --output text)"
check "alarms" "$(aws cloudwatch describe-alarms --alarm-name-prefix "$STACK-" \
  --query 'MetricAlarms[].AlarmName' --output text)"
check "topics" "$(aws sns list-topics --query "Topics[?ends_with(TopicArn, ':$STACK-alerts')].TopicArn" --output text)"

if [ "$left" -eq 0 ]; then echo "nothing left behind"; else exit 1; fi
