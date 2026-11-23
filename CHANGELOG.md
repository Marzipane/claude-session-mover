# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

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

[0.1.0]: https://github.com/Marzipane/claude-session-mover/releases/tag/v0.1.0
