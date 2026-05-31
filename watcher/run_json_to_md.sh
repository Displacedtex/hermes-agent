#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

for candidate in "$REPO_ROOT/.venv" "$REPO_ROOT/venv" "$HOME/.hermes/hermes-agent/venv"; do
  if [ -x "$candidate/bin/python" ]; then
    exec "$candidate/bin/python" "$REPO_ROOT/watcher/json_to_md.py" "$@"
  fi
done

echo "error: no Hermes Python virtualenv found" >&2
exit 1
