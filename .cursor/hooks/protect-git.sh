#!/usr/bin/env bash
# Ask before destructive git shell commands the agent might run.
# Input: JSON on stdin from Cursor (includes .command)
# Output: JSON with permission allow|ask|deny

set -euo pipefail

input=$(cat)
command=$(printf '%s' "$input" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("command") or "")')

# Patterns that are easy to regret
risky='git[[:space:]]+(push[[:space:]]+.*--force|push[[:space:]]+.*-f[[:space:]]|reset[[:space:]]+--hard|clean[[:space:]]+-fd|rebase[[:space:]]+.*--force)'

if [[ "$command" =~ $risky ]]; then
  python3 -c 'import json; print(json.dumps({
    "permission": "ask",
    "user_message": "This git command can rewrite history or discard work. Review it before continuing.",
    "agent_message": "A project hook flagged a potentially destructive git command."
  }))'
  exit 0
fi

echo '{ "permission": "allow" }'
exit 0
