#!/usr/bin/env bash
# Stop hook — reminder to update PROGRESS.md if there are uncommitted changes.
# Quiet if clean or not a git repo.

set -euo pipefail

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  exit 0
fi

if git diff-index --quiet HEAD -- 2>/dev/null && [[ -z "$(git ls-files --others --exclude-standard)" ]]; then
  exit 0
fi

cat <<'EOF'
# Reminder

Uncommitted changes detected. Before ending the session, consider running the
`update-progress` skill to refresh PROGRESS.md with this session's work.
EOF
