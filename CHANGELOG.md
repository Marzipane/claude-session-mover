# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [0.2.1] - 2026-11-27

### Fixed
- Commands could crash with `FileNotFoundError` while the sync agent was running: a file listed during a folder scan could be gone (a temp file renamed into place, or a chat moved to the trash) before it was read. Seen on Windows, where every command scans the Desktop folder to pick it. Files that vanish mid-scan are now skipped.

## [0.2.0] - 2026-11-27

### Added
- `sync`: makes every Claude Desktop account folder hold the newest copy of every file, so each account shows the same chats. Chat index files: larger `lastActivityAt` wins, then the newer mtime; other files: newer mtime wins; a `deleted_<id>` marker moves that chat out of every folder into a trash folder. Idempotent, locked per root, `--dry-run` and `--quiet`.
- `install-agent` / `uninstall-agent`: run `sync` in the background: a LaunchAgent on macOS (every 20 s and whenever the account folders change), a hidden scheduled task on Windows (every minute, no admin rights).
- `status`: reports whether the agent is installed, loaded and succeeding, and how many changes are pending; exits 1 when it is not healthy, for use in hooks and scripts.
- Installer flag `--auto-sync`: `curl ... | sh -s -- --auto-sync` installs the tool and the agent.
- Windows support: `install.ps1` one-line installer (`irm ... | iex`, `CSM_AUTO_SYNC=1` for the agent), the Desktop folder under `%APPDATA%` or the Microsoft Store package, the running-app check via `tasklist`, data under `%LOCALAPPDATA%\claude-session-mover`, and retries when Windows reports a file in use.
- `sync --log FILE` for agents that have no console.

### Changed
- Backups, the sync trash and the agent log live in one data folder: `$XDG_DATA_HOME/claude-session-mover` (unchanged) or `%LOCALAPPDATA%\claude-session-mover` on Windows.

## [0.1.0] - 2026-11-23

### Added
- `list`: Claude Desktop account folders and Claude Code CLI config dirs, with session counts per project (`-v` for every session).
- `copy` and `move` between accounts: all sessions, per project (`--project`), or by session id (`--session`).
- `--dry-run` that prints every file that would be written or removed.
- Automatic timestamped backup before any write, and `restore-backup` to undo (restores are backed up too).
- Newest-copy-wins merging: Desktop index by `lastActivityAt`, CLI transcripts by append-only prefix; diverged copies are skipped.
- Chats deleted in the target account (Desktop `deleted_<id>` markers) are not brought back.
- Refuses to write Desktop folders while Claude Desktop runs; `--wait-for-app-exit` waits for it to quit.
- Claude Code skill and plugin marketplace entry, and a one-line installer.

[0.2.1]: https://github.com/Marzipane/claude-session-mover/releases/tag/v0.2.1
[0.2.0]: https://github.com/Marzipane/claude-session-mover/releases/tag/v0.2.0
[0.1.0]: https://github.com/Marzipane/claude-session-mover/releases/tag/v0.1.0
