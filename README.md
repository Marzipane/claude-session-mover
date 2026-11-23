# claude-session-mover

Copy or move Claude Code sessions (chat history) from one Claude account to another on the same machine. You can then keep working on those conversations after you switch accounts.

```bash
curl -fsSL https://raw.githubusercontent.com/Marzipane/claude-session-mover/main/install.sh | sh
claude-session-mover list
```

## The problem

Claude keeps the list of your Code sessions per account. Sign in with a different account and your earlier chats disappear from the sidebar or from `claude --resume`, even though they are still on disk. This tool brings them over, shows you every change first, and backs up whatever it is about to change.

## How Claude Code stores sessions

The tool handles two stores.

| Store | Where | What one session is |
|---|---|---|
| `desktop` (Claude Desktop, Code tab) | macOS: `~/Library/Application Support/Claude/claude-code-sessions/<account>/<org>/` | `local_<id>.json`, an index entry with title, working directory, `lastActivityAt` and the CLI session id |
| `cli` (Claude Code in a terminal) | `~/.claude`, or the folder `CLAUDE_CONFIG_DIR` points at | `projects/<encoded-working-dir>/<id>.jsonl` (the transcript), plus an optional `<id>/` folder (subagent transcripts, tool output) |

Claude Desktop shares one transcript folder (`~/.claude/projects`) across all its accounts. That means only the small index files need to move between Desktop accounts.

With the CLI, people usually keep one config dir per account (`CLAUDE_CONFIG_DIR=~/.claude-work claude`), so the transcripts themselves are copied.

## Install

| Way | Command |
|---|---|
| One-liner (uses uv or pipx if you have them, else drops one file into `~/.local/bin`) | `curl -fsSL https://raw.githubusercontent.com/Marzipane/claude-session-mover/main/install.sh \| sh` |
| pipx | `pipx install git+https://github.com/Marzipane/claude-session-mover` |
| uv | `uv tool install git+https://github.com/Marzipane/claude-session-mover` |
| By hand | download `claude_session_mover.py`, `chmod +x`, put it on your `PATH` |

Needs Python 3.8 or newer and nothing else (standard library only).

## Usage

```bash
# What is on this machine
claude-session-mover list            # accounts / config dirs, sessions per project
claude-session-mover list -v         # every session with id, last activity, title

# Claude Desktop: accounts are <account>/<org> folder ids; unique prefixes are enough
claude-session-mover copy --from a1b2 --to c3d4 --all --dry-run
claude-session-mover copy --from a1b2 --to c3d4 --all --wait-for-app-exit

# Claude Code CLI: accounts are config dir paths
claude-session-mover copy --from ~/.claude --to ~/.claude-work --project ~/code/my-app --dry-run
claude-session-mover move --from ~/.claude --to ~/.claude-work --session 1a2b3c4d

# Undo
claude-session-mover restore-backup                 # list backups
claude-session-mover restore-backup latest --dry-run
claude-session-mover restore-backup 20261123-101500-copy
```

Choose sessions with `--all`, `--project PATH` (repeatable) or `--session ID` (repeatable, unique prefix of at least 4 characters).

What happens to each session:

| Situation | Result |
|---|---|
| Not in the target | added |
| Identical in the target | nothing (so running twice changes nothing) |
| Source is newer (Desktop: larger `lastActivityAt`; CLI: target transcript is a prefix of the source) | target updated |
| Target is newer | skipped, target kept |
| CLI transcripts that diverged | skipped, both kept |
| Desktop chat deleted in the target account (`deleted_<id>` marker) | skipped, not brought back |

`move` removes a session from the source only after it is in the target, and never for skipped sessions.

### Settings

| Flag | Environment variable | Default |
|---|---|---|
| `--desktop-root` | `CLAUDE_DESKTOP_SESSIONS_DIR` | macOS `~/Library/Application Support/Claude/claude-code-sessions`; Linux `$XDG_CONFIG_HOME/Claude/claude-code-sessions` |
| `list --config-dir` (repeatable) | `CLAUDE_CONFIG_DIR` | also finds `~/.claude`, `~/.claude-*`, `~/.config/claude*` |
| `--backup-dir` | `CLAUDE_SESSION_MOVER_BACKUP_DIR` | `$XDG_DATA_HOME/claude-session-mover/backups` (`~/.local/share/...`) |
| | `CLAUDE_SESSION_MOVER_APP_NAME` | `Claude`, the process name checked to see whether Claude Desktop runs |

## Let Claude do it

The repo is also a Claude Code plugin with a skill that installs the tool and finds your accounts. It runs a dry run, waits for your yes, and then moves the sessions.

In Claude Code:

```text
/plugin marketplace add Marzipane/claude-session-mover
/plugin install claude-session-mover@claude-session-mover
```

Or install the skill by hand:

```bash
mkdir -p ~/.claude/skills/claude-session-mover
curl -fsSL https://raw.githubusercontent.com/Marzipane/claude-session-mover/main/skills/claude-session-mover/SKILL.md \
  -o ~/.claude/skills/claude-session-mover/SKILL.md
```

Then tell Claude: *"Use claude-session-mover to set up session moving on this machine."*

## Safety

- **Backup before every write.** Before changing anything, the tool copies every file it will overwrite or remove into a timestamped folder under the backup dir. It also writes a `manifest.json` listing every file it will create. The manifest is complete before the first change, so an interrupted run can still be undone. `restore-backup` puts each file back, deletes files the run created, and removes folders it created once they are empty. A restore is itself backed up, so you can undo it too.
- **Dry run first.** `--dry-run` prints every path that would be written (`new` or `overwrite`) or removed, then exits without touching anything.
- **Credentials are never touched.** Only `local_*.json` (Desktop) and `projects/` (CLI) are read or written. `.credentials.json`, `.claude.json`, the macOS keychain and settings files are never opened.
- **Atomic writes.** Each file is written to a temporary file in the same folder and then renamed into place. Modification times are kept.
- **Claude Desktop must be closed.** It rewrites its session index when it quits, which would undo a change made while it runs. On macOS the tool refuses to write Desktop folders while the app runs. `--wait-for-app-exit [SECONDS]` waits for you to quit it (default 600 s) and then writes. Reopen the app afterwards to see the chats.
- Backups contain copies of your session data. They stay on your machine with `0700` folder permissions. Delete old ones when you no longer need them.

## Limitations

- **Windows is not supported.** It is untested, and the Desktop path and app check are macOS/Linux only.
- **Linux Desktop path is unverified.** Claude Desktop has no official Linux build. The Linux default Desktop path is a best guess, so use `--desktop-root` if yours differs. The "is the app running" check only runs on macOS.
- **Not migrated:** the CLI prompt history (`history.jsonl`), todos, settings, MCP config and Desktop's `archived-sessions.idx`. An archived Desktop chat keeps its `isArchived` flag in its own index file.
- **Same-machine only.** For the `desktop` store, the transcripts must already be in the shared `~/.claude/projects`, which is true on the machine that created them. Moving to another computer is not supported.
- **Undocumented formats.** Anthropic does not document these storage formats, and they may change. The tool only copies whole files and never edits their contents. Even so, run a dry run first after Claude updates.

## Development

```bash
python3 -m unittest discover -s tests -v
```

The tests build synthetic Desktop and CLI account folders in a temporary `HOME`. They cover list, copy, move, dry run, backups, restore, idempotency, conflict handling, missing folders and the running-app guard.

## License

MIT, see [LICENSE](LICENSE).
