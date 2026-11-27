---
name: claude-session-mover
description: Install claude-session-mover and copy or move Claude Code sessions (chat history) from one Claude account to another on this machine, after a dry run the user confirms, or set up automatic sync so every Claude Desktop account shows the same chats. Use when the user switched Claude accounts and wants old chats back, says "set up session moving", "move my sessions to this account", "restore my chats", "keep my chats in sync across accounts", or names claude-session-mover.
---

# claude-session-mover

Moves session history between Claude accounts on one machine. Two stores:

- **desktop**: Claude Desktop's Code-tab chat index, one folder per account+org: `<root>/<account>/<org>/local_<id>.json`. Transcripts are shared, so only these index files move. Account names look like `a1b2c3d4/e5f6a7b8` (prefixes of the two folder ids).
- **cli**: Claude Code CLI config dirs (`~/.claude`, `$CLAUDE_CONFIG_DIR`, `~/.claude-*`). An account is named by its folder path.

## Steps

1. **Install if missing.** Run `claude-session-mover --version`. If it is not found, run on macOS/Linux:
   `curl -fsSL https://raw.githubusercontent.com/Marzipane/claude-session-mover/main/install.sh | sh`
   or on Windows (PowerShell): `irm https://raw.githubusercontent.com/Marzipane/claude-session-mover/main/install.ps1 | iex`
   then check `--version` again (it may live in `~/.local/bin` or `%LOCALAPPDATA%\claude-session-mover\bin`).
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
     On Windows, start the waiter with PowerShell instead:
     ```powershell
     Start-Process -WindowStyle Hidden claude-session-mover -ArgumentList 'copy','--from','SRC','--to','DST','--all','--wait-for-app-exit','900' `
       -RedirectStandardOutput "$HOME\claude-session-mover-last-run.log"
     ```
     After the restart, read that log and show the user the result.
7. **Report.** Show the `Done:` line, the backup name, and the undo command `claude-session-mover restore-backup <name>`. For Desktop, the moved chats appear after the app restarts.

## Automatic sync (Claude Desktop on macOS and Windows)

When the user wants every Desktop account to show the same chats from now on, or switches accounts often:

1. Run `claude-session-mover sync --dry-run` and show the summary: how many files each folder gains, which chats would be moved to the trash because they were deleted in another account.
2. After a clear yes, run `claude-session-mover install-agent`, then `claude-session-mover status` and show its result. It is a LaunchAgent on macOS (every 20 s) and a hidden scheduled task on Windows (every minute); neither needs admin rights.
3. Tell the user that chats from other accounts appear after a restart of Claude Desktop or an account switch.

If chats are missing later, run `claude-session-mover status` first: it names a missing script, an unloaded agent or a failing run, and `install-agent` fixes the first two. `uninstall-agent` stops it.

## Rules

- Never pass `--allow-running-app` unless the user asks for it by name.
- Never read, copy or edit credential files (`.credentials.json`, `.claude.json`) or the keychain. The tool does not touch them either.
- If anything goes wrong, offer `claude-session-mover restore-backup latest --dry-run` first, then the real restore after the user confirms.
- `claude-session-mover <command> --help` documents every flag.
