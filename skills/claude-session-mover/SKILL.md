---
name: claude-session-mover
description: Install claude-session-mover and copy or move Claude Code sessions (chat history) from one Claude account to another on this machine, after a dry run the user confirms. Use when the user switched Claude accounts and wants old chats back, says "set up session moving", "move my sessions to this account", "restore my chats", or names claude-session-mover.
---

# claude-session-mover

Moves session history between Claude accounts on one machine. Two stores:

- **desktop**: Claude Desktop's Code-tab chat index, one folder per account+org: `<root>/<account>/<org>/local_<id>.json`. Transcripts are shared, so only these index files move. Account names look like `a1b2c3d4/e5f6a7b8` (prefixes of the two folder ids).
- **cli**: Claude Code CLI config dirs (`~/.claude`, `$CLAUDE_CONFIG_DIR`, `~/.claude-*`). An account is named by its folder path.

## Steps

1. **Install if missing.** Run `claude-session-mover --version`. If it is not found, run:
   `curl -fsSL https://raw.githubusercontent.com/Marzipane/claude-session-mover/main/install.sh | sh`
   then check `--version` again (it may live in `~/.local/bin`).
2. **Detect accounts.** Run `claude-session-mover list`. Show the user the accounts, their session counts and newest activity.
   - Running inside Claude Desktop, the current account is in this session's working directory path: `.../scratch-workspaces/<account>/<org>/...`. Otherwise, the folder with the newest activity is usually the one in use.
   - Only one CLI config dir and one Desktop folder means there is nothing to move: say so and stop.
3. **Agree source, target and scope.** Ask which account to take sessions from and which to bring them to, unless the user already said. Default to `copy` and `--all`. Use `move` only when the user asks to remove them from the source. Use `--project PATH` or `--session ID` when they name a project or a chat.
4. **Dry run.** Run the same command with `--dry-run` and show the summary line plus a sample of the planned writes. Explain any `skip` lines (target newer, diverged, deleted in target).
5. **Confirm.** Ask the user to confirm the exact command. Do not write without a clear yes.
6. **Apply.**
   - **cli store, or Claude Desktop not running:** run the command without `--dry-run`.
   - **desktop store while Claude Desktop runs** (always true when you run inside Desktop): the app rewrites its index when it quits, so the copy must happen after it quits. Start a detached waiter, then ask the user to quit Claude Desktop fully (Cmd+Q), wait 10 seconds, and reopen it:
     ```bash
     nohup claude-session-mover copy --from SRC --to DST --all --wait-for-app-exit 900 \
       > "$HOME/claude-session-mover-last-run.log" 2>&1 & disown
     ```
     After the restart, read that log and show the user the result.
7. **Report.** Show the `Done:` line, the backup name, and the undo command `claude-session-mover restore-backup <name>`. For Desktop, the moved chats appear after the app restarts.

## Rules

- Never pass `--allow-running-app` unless the user asks for it by name.
- Never read, copy or edit credential files (`.credentials.json`, `.claude.json`) or the keychain. The tool does not touch them either.
- If anything goes wrong, offer `claude-session-mover restore-backup latest --dry-run` first, then the real restore after the user confirms.
- `claude-session-mover <command> --help` documents every flag.
