#!/bin/sh
# Install claude-session-mover. Uses uv or pipx when present; otherwise drops the
# single-file script into ~/.local/bin (override with CSM_BIN_DIR).
#   curl -fsSL https://raw.githubusercontent.com/Marzipane/claude-session-mover/main/install.sh | sh
# Add --auto-sync (macOS) to also install the agent that keeps every Claude Desktop account's chats in step:
#   curl -fsSL https://raw.githubusercontent.com/Marzipane/claude-session-mover/main/install.sh | sh -s -- --auto-sync
set -eu

auto_sync=0
for arg in "$@"; do
  case "$arg" in
    --auto-sync) auto_sync=1 ;;
    *) echo "unknown option: $arg (the only option is --auto-sync)" >&2; exit 2 ;;
  esac
done

REPO="https://github.com/Marzipane/claude-session-mover"
REF="${CSM_REF:-main}"
RAW="https://raw.githubusercontent.com/Marzipane/claude-session-mover/$REF/claude_session_mover.py"

if ! command -v python3 >/dev/null 2>&1; then
  echo "claude-session-mover needs python3 (3.8 or newer). Install it, then run this again." >&2
  exit 1
fi

if command -v uv >/dev/null 2>&1; then
  uv tool install --force "git+$REPO@$REF"
elif command -v pipx >/dev/null 2>&1; then
  pipx install --force "git+$REPO@$REF"
else
  bin_dir="${CSM_BIN_DIR:-$HOME/.local/bin}"
  mkdir -p "$bin_dir"
  tmp="$(mktemp)"
  trap 'rm -f "$tmp"' EXIT
  curl -fsSL --retry 3 --max-time 60 "$RAW" -o "$tmp"
  python3 -c 'import ast, sys; ast.parse(open(sys.argv[1]).read())' "$tmp"
  install -m 0755 "$tmp" "$bin_dir/claude-session-mover"
  case ":$PATH:" in
    *":$bin_dir:"*) ;;
    *) echo "Add $bin_dir to your PATH to run claude-session-mover." ;;
  esac
fi

echo "Installed. Try: claude-session-mover list"

if [ "$auto_sync" = 1 ]; then
  csm="$(command -v claude-session-mover || true)"
  [ -n "$csm" ] || csm="${CSM_BIN_DIR:-$HOME/.local/bin}/claude-session-mover"
  "$csm" install-agent
fi
