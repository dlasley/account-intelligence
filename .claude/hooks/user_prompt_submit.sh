#!/usr/bin/env bash
# UserPromptSubmit hook — inject current date + branch so Claude has accurate context.
# Output on stdout is injected as additional context; keep it minimal.
#
# Also emits a periodic permissions reminder so the user retains visibility into
# what's been auto-allowed in .claude/settings.json. Counter persists in
# .claude/.cache/prompt-count (gitignored).

set -euo pipefail

DATE="$(date +%Y-%m-%d)"
BRANCH="$(git branch --show-current 2>/dev/null || echo '(not a git repo)')"
DIRTY=""
if git rev-parse --git-dir >/dev/null 2>&1; then
  if ! git diff-index --quiet HEAD -- 2>/dev/null; then
    DIRTY=" (uncommitted changes present)"
  fi
fi

cat <<EOF
# Session context

- Date: ${DATE}
- Branch: ${BRANCH}${DIRTY}
EOF

# --- Periodic permissions reminder ---
# Every N user prompts, inject a one-line reminder of what's been auto-allowed.
# Tune REMIND_EVERY to taste; 20 is a balance between visibility and noise.
REMIND_EVERY=20
CACHE_DIR=".claude/.cache"
COUNTER_FILE="${CACHE_DIR}/prompt-count"

if [[ -d .claude ]]; then
  mkdir -p "${CACHE_DIR}" 2>/dev/null || true
  COUNT=0
  if [[ -f "${COUNTER_FILE}" ]]; then
    COUNT=$(cat "${COUNTER_FILE}" 2>/dev/null || echo 0)
  fi
  COUNT=$((COUNT + 1))
  echo "${COUNT}" > "${COUNTER_FILE}" 2>/dev/null || true

  if (( COUNT % REMIND_EVERY == 0 )) && [[ -f .claude/settings.json ]]; then
    ALLOW_COUNT=$(jq '.permissions.allow | length' .claude/settings.json 2>/dev/null || echo 0)
    cat <<REMINDER

# Permissions reminder (every ${REMIND_EVERY} prompts)

Auto-allowed: ${ALLOW_COUNT} entries in \`.claude/settings.json\` (Bash patterns + MCP tools + file ops). Inspect with \`cat .claude/settings.json\` to review what's been delegated. Run \`/fewer-permission-prompts\` to scan recent prompts and propose additions.

Surface this one-liner to the user briefly when next responding.
REMINDER
  fi
fi
