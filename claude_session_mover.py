#!/usr/bin/env python3
"""claude-session-mover: copy or move Claude Code sessions between accounts.

Two places hold sessions, and this tool calls each one a *store*:

  desktop  Claude Desktop's Code-tab chat index, one folder per signed-in account
           and organisation: <desktop-root>/<account>/<org>/local_<id>.json.
           The transcripts behind them live in ~/.claude/projects and are shared
           by every Desktop account, so only the index files need to move.
  cli      Claude Code CLI config dirs (~/.claude, or whatever CLAUDE_CONFIG_DIR
           points at). Each keeps its transcripts in projects/<project>/<id>.jsonl.

Only session files are read or written. Credentials (.credentials.json,
.claude.json, the keychain) are never opened.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

__version__ = "0.1.0"
PROG = "claude-session-mover"
MANIFEST = "manifest.json"
MIN_ID_PREFIX = 4


class UsageError(Exception):
    """The arguments do not name something that exists or is unambiguous."""


# --------------------------------------------------------------------------- model


@dataclass(frozen=True)
class Location:
    """One account's session folder in one store."""

    store: str
    root: Path
    label: str


@dataclass(frozen=True)
class Session:
    session_id: str
    project: str
    project_key: str
    files: Tuple[str, ...]
    last_activity: float
    title: str


@dataclass(frozen=True)
class FileOp:
    """One file change. `source` is set for writes; `prune_up_to` bounds empty-dir cleanup after removes."""

    kind: str
    path: Path
    source: Optional[Path] = None
    prune_up_to: Optional[Path] = None


@dataclass(frozen=True)
class Decision:
    session: Session
    verdict: str
    ops: Tuple[FileOp, ...]


SKIP_REASONS = {
    "target-newer": "target copy is newer",
    "conflict": "both copies changed since they split",
    "deleted-in-target": "deleted in the target account",
    "unreadable": "a copy could not be parsed",
}


def encode_project(path: str) -> str:
    """Claude Code's folder name for a working directory: every non-alphanumeric becomes '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


# --------------------------------------------------------------------------- desktop store


def default_desktop_root() -> Path:
    configured = os.environ.get("CLAUDE_DESKTOP_SESSIONS_DIR")
    if configured:
        return Path(configured).expanduser()
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Claude" / "claude-code-sessions"
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    return config_home / "Claude" / "claude-code-sessions"


def _subdirs(path: Path) -> List[Path]:
    return sorted(p for p in path.iterdir() if p.is_dir() and not p.is_symlink() and not p.name.startswith("."))


def desktop_locations(root: Path) -> List[Location]:
    if not root.is_dir():
        return []
    return [
        Location("desktop", org, f"{account.name}/{org.name}")
        for account in _subdirs(root)
        for org in _subdirs(account)
    ]


def desktop_sessions(location: Location) -> Tuple[List[Session], List[str]]:
    """Sessions in one Desktop account folder, plus the names of index files that could not be parsed."""
    sessions, unreadable = [], []
    for path in sorted(location.root.glob("local_*.json")):
        try:
            index = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            unreadable.append(path.name)
            continue
        cwd = str(index.get("cwd") or index.get("originCwd") or "")
        sessions.append(Session(
            session_id=path.stem[len("local_"):],
            project=cwd,
            project_key=encode_project(cwd),
            files=(path.name,),
            last_activity=(index.get("lastActivityAt") or 0) / 1000,
            title=str(index.get("title") or ""),
        ))
    return sessions, unreadable


def deleted_session_ids(location: Location) -> Set[str]:
    """Claude Desktop leaves a deleted_<id> marker behind when a chat is deleted."""
    return {p.name[len("deleted_"):] for p in location.root.glob("deleted_*")}


# --------------------------------------------------------------------------- cli store


def cli_locations(extra: Sequence[str]) -> List[Location]:
    home = Path.home()
    candidates: List[Path] = []
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.append(home / ".claude")
    candidates += sorted(home.glob(".claude-*"))
    candidates += sorted((home / ".config").glob("claude*"))
    candidates += [Path(p).expanduser() for p in extra]
    found, seen = [], set()
    for path in candidates:
        if not (path / "projects").is_dir():
            continue
        real = path.resolve()
        if real in seen:
            continue
        seen.add(real)
        found.append(Location("cli", path, str(path)))
    return found


def _transcript_cwd(path: Path) -> str:
    """The working directory recorded in a transcript's first lines, or '' when none is."""
    try:
        with path.open(encoding="utf-8", errors="replace") as transcript:
            head = [line for _, line in zip(range(50), transcript)]
    except OSError:
        return ""  # an unreadable transcript is still listed and moved; it just shows no project path
    for line in head:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and isinstance(entry.get("cwd"), str):
            return entry["cwd"]
    return ""


def cli_sessions(location: Location) -> Tuple[List[Session], List[str]]:
    projects = location.root / "projects"
    sessions: List[Session] = []
    if not projects.is_dir():
        return sessions, []
    for project_dir in _subdirs(projects):
        for transcript in sorted(project_dir.glob("*.jsonl")):
            sidecar = project_dir / transcript.stem
            files = [transcript]
            if sidecar.is_dir():
                files += sorted(p for p in sidecar.rglob("*") if p.is_file() and not p.is_symlink())
            sessions.append(Session(
                session_id=transcript.stem,
                project=_transcript_cwd(transcript),
                project_key=project_dir.name,
                files=tuple(str(p.relative_to(location.root)) for p in files),
                last_activity=transcript.stat().st_mtime,
                title="",
            ))
    return sessions, []


def sessions_in(location: Location) -> Tuple[List[Session], List[str]]:
    return desktop_sessions(location) if location.store == "desktop" else cli_sessions(location)


# --------------------------------------------------------------------------- resolving arguments


def resolve_location(value: str, desktop_root: Path) -> Location:
    path = Path(value).expanduser()
    if path.is_dir():
        for location in desktop_locations(desktop_root):
            if location.root.resolve() == path.resolve():
                return location
        return Location("cli", path, str(path))
    if value.startswith(("/", "~", ".")):
        raise UsageError(f"folder not found: {value}")
    return _desktop_location_by_prefix(value, desktop_root)


def _desktop_location_by_prefix(value: str, desktop_root: Path) -> Location:
    account_prefix, _, org_prefix = value.partition("/")
    matches = [
        loc for loc in desktop_locations(desktop_root)
        if loc.root.parent.name.startswith(account_prefix) and loc.root.name.startswith(org_prefix)
    ]
    if not matches:
        raise UsageError(
            f"no Claude Desktop account folder matches '{value}' under {desktop_root} "
            f"(run '{PROG} list' to see them; a CLI config dir must be an existing folder path)"
        )
    if len(matches) > 1:
        names = "\n  ".join(m.label for m in matches)
        raise UsageError(f"'{value}' matches more than one account folder; add more of the id:\n  {names}")
    return matches[0]


def project_matches(session: Session, wanted: str) -> bool:
    expanded = os.path.expanduser(wanted).rstrip("/") or "/"
    if wanted in (session.project, session.project_key) or expanded == session.project:
        return True
    return encode_project(expanded) == session.project_key


def select_sessions(sessions: List[Session], args: argparse.Namespace) -> List[Session]:
    if args.all:
        return list(sessions)
    chosen = {s.session_id: s for s in sessions if any(project_matches(s, p) for p in args.project)}
    for prefix in args.session:
        if len(prefix) < MIN_ID_PREFIX:
            raise UsageError(f"session id '{prefix}' is too short; give at least {MIN_ID_PREFIX} characters")
        hits = [s for s in sessions if s.session_id.startswith(prefix)]
        if not hits:
            raise UsageError(f"no session in the source starts with '{prefix}'")
        if len(hits) > 1:
            raise UsageError(f"'{prefix}' matches {len(hits)} sessions; give more of the id")
        chosen[hits[0].session_id] = hits[0]
    for project in args.project:
        if not any(project_matches(s, project) for s in sessions):
            raise UsageError(f"no session in the source belongs to project '{project}'")
    return sorted(chosen.values(), key=lambda s: (s.project_key, s.session_id))


# --------------------------------------------------------------------------- planning


def _same_bytes(a: Path, b: Path) -> bool:
    return a.stat().st_size == b.stat().st_size and _starts_with(a, b)


def _starts_with(longer: Path, prefix: Path) -> bool:
    """True when the file `longer` begins with every byte of the file `prefix`."""
    remaining = prefix.stat().st_size
    if longer.stat().st_size < remaining:
        return False
    with longer.open("rb") as big, prefix.open("rb") as small:
        while remaining:
            chunk = small.read(min(remaining, 1 << 20))
            if not chunk or big.read(len(chunk)) != chunk:
                return False
            remaining -= len(chunk)
    return True


def _desktop_verdict(session: Session, source: Location, target: Location, target_deleted: Set[str]) -> str:
    if session.session_id in target_deleted:
        return "deleted-in-target"
    name = session.files[0]
    target_file = target.root / name
    if not target_file.exists():
        return "add"
    if _same_bytes(source.root / name, target_file):
        return "same"
    try:
        target_activity = json.loads(target_file.read_text(encoding="utf-8")).get("lastActivityAt") or 0
    except (OSError, ValueError):
        return "unreadable"
    return "update" if session.last_activity * 1000 > target_activity else "target-newer"


def _cli_verdict(session: Session, source: Location, target: Location) -> str:
    transcript = session.files[0]
    src, dst = source.root / transcript, target.root / transcript
    if not dst.exists():
        return "add"
    # Transcripts are append-only, so the copy that extends the other is the newer one.
    if _same_bytes(src, dst):
        return "same" if not _changed_files(session, source, target) else "update"
    if _starts_with(src, dst):
        return "update"
    if _starts_with(dst, src):
        return "target-newer"
    return "conflict"


def _changed_files(session: Session, source: Location, target: Location) -> List[str]:
    return [
        rel for rel in session.files
        if not (target.root / rel).exists() or not _same_bytes(source.root / rel, target.root / rel)
    ]


def plan_transfer(source: Location, target: Location, sessions: List[Session], moving: bool) -> List[Decision]:
    target_deleted = deleted_session_ids(target) if target.store == "desktop" else set()
    decisions = []
    for session in sessions:
        if source.store == "desktop":
            verdict = _desktop_verdict(session, source, target, target_deleted)
        else:
            verdict = _cli_verdict(session, source, target)
        ops: List[FileOp] = []
        if verdict in ("add", "update"):
            ops += [FileOp("write", target.root / rel, source.root / rel) for rel in _changed_files(session, source, target)]
        if moving and verdict in ("add", "update", "same"):
            stop = source.root / "projects" if source.store == "cli" else source.root
            ops += [FileOp("remove", source.root / rel, prune_up_to=stop) for rel in session.files]
        decisions.append(Decision(session, verdict, tuple(ops)))
    return decisions


# --------------------------------------------------------------------------- applying, with backup


def default_backup_root() -> Path:
    configured = os.environ.get("CLAUDE_SESSION_MOVER_BACKUP_DIR")
    if configured:
        return Path(configured).expanduser()
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return data_home / PROG / "backups"


def _new_backup_dir(backup_root: Path, command: str) -> Path:
    backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for n in range(1000):
        candidate = backup_root / (f"{stamp}-{command}" + (f"-{n}" if n else ""))
        try:
            candidate.mkdir(mode=0o700)
            return candidate
        except FileExistsError:
            continue
    raise RuntimeError(f"could not create a backup folder in {backup_root}")


def _atomic_copy(source: Path, destination: Path) -> None:
    fd, tmp = tempfile.mkstemp(dir=destination.parent, prefix=".csm-")
    os.close(fd)
    try:
        shutil.copy2(source, tmp)
        os.replace(tmp, destination)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _missing_parents(path: Path) -> List[Path]:
    missing = []
    parent = path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    return missing


def apply_with_backup(ops: Sequence[FileOp], backup_root: Path, meta: Dict[str, object]) -> Path:
    """Save the current state of every path in `ops`, write the manifest, then apply the ops.

    The manifest is complete before the first change, so an interrupted run can still be restored.
    """
    backup = _new_backup_dir(backup_root, str(meta["command"]))
    (backup / "files").mkdir(mode=0o700)
    entries, created_dirs = [], []
    for index, op in enumerate(ops):
        entry: Dict[str, object] = {"path": str(op.path), "existed": op.path.exists()}
        if entry["existed"]:
            saved = f"files/{index:05d}"
            shutil.copy2(op.path, backup / saved)
            entry["saved"] = saved
        entries.append(entry)
        if op.kind == "write":
            created_dirs += [str(d) for d in _missing_parents(op.path) if str(d) not in created_dirs]
    manifest = dict(meta, version=1, created=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    entries=entries, created_dirs=created_dirs, status="in-progress")
    _write_manifest(backup, manifest)
    for op in ops:
        _apply_one(op)
    manifest["status"] = "complete"
    _write_manifest(backup, manifest)
    return backup


def _write_manifest(backup: Path, manifest: Dict[str, object]) -> None:
    (backup / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def _apply_one(op: FileOp) -> None:
    if op.kind == "write":
        assert op.source is not None
        op.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _atomic_copy(op.source, op.path)
        return
    if op.path.exists():
        op.path.unlink()
    if op.prune_up_to is not None:
        _prune_empty_dirs(op.path.parent, op.prune_up_to)


def _prune_empty_dirs(start: Path, stop: Path) -> None:
    directory = start
    while directory != stop and stop in directory.parents:
        if any(directory.iterdir()):
            return
        directory.rmdir()
        directory = directory.parent


# --------------------------------------------------------------------------- restore


def backup_dirs(backup_root: Path) -> List[Path]:
    if not backup_root.is_dir():
        return []
    return sorted(p for p in backup_root.iterdir() if (p / MANIFEST).is_file())


def load_manifest(backup: Path) -> Dict[str, object]:
    return json.loads((backup / MANIFEST).read_text(encoding="utf-8"))


def find_backup(backup_root: Path, name: str) -> Path:
    backups = backup_dirs(backup_root)
    if not backups:
        raise UsageError(f"no backups in {backup_root}")
    if name == "latest":
        return backups[-1]
    hits = [b for b in backups if b.name.startswith(name)]
    if len(hits) != 1:
        raise UsageError(f"'{name}' matches {len(hits)} backups; run '{PROG} restore-backup' to list them")
    return hits[0]


def plan_restore(backup: Path) -> List[FileOp]:
    """Ops that put every path the backup recorded back the way it was, skipping ones already restored."""
    ops = []
    for entry in reversed(load_manifest(backup)["entries"]):
        path = Path(entry["path"])
        if entry["existed"]:
            saved = backup / entry["saved"]
            if not (path.exists() and _same_bytes(saved, path)):
                ops.append(FileOp("write", path, saved))
        elif path.exists():
            ops.append(FileOp("remove", path))
    return ops


def remove_created_dirs(backup: Path) -> List[str]:
    """Remove folders the backed-up run created, deepest first, when they are empty again."""
    removed = []
    for directory in sorted(load_manifest(backup).get("created_dirs", []), key=len, reverse=True):
        path = Path(directory)
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
            removed.append(directory)
    return removed


# --------------------------------------------------------------------------- safety checks


def desktop_app_running() -> bool:
    """Claude Desktop rewrites its index when it quits, so writing while it runs gets undone."""
    if sys.platform != "darwin":
        return False
    name = os.environ.get("CLAUDE_SESSION_MOVER_APP_NAME", "Claude")
    # pgrep -x misses the app on some macOS setups while ps lists it, so match ps's command paths.
    # The match is case-sensitive on purpose: the bundled CLI runs as .../MacOS/claude.
    listing = subprocess.run(["ps", "-axo", "comm="], capture_output=True, text=True, timeout=5, check=True)
    return any(os.path.basename(line.strip()) == name for line in listing.stdout.splitlines())


def _refuse_if_app_running(store: str, args: argparse.Namespace) -> None:
    if store != "desktop" or args.dry_run or args.allow_running_app or not desktop_app_running():
        return
    if args.wait_for_app_exit:
        print(f"Waiting up to {args.wait_for_app_exit} s for Claude Desktop to quit...", flush=True)
        deadline = time.monotonic() + args.wait_for_app_exit
        while time.monotonic() < deadline:
            time.sleep(1)
            if not desktop_app_running():
                return
        raise UsageError(f"Claude Desktop is still running after {args.wait_for_app_exit} s; nothing was changed.")
    raise UsageError(
            "Claude Desktop is running. It rewrites its session index when it quits, which would undo "
            "this change. Quit it fully (Cmd+Q, wait for its helper processes to exit), then run this "
            "again; or pass --wait-for-app-exit to start now and write once it has quit."
        )


# --------------------------------------------------------------------------- output


def _when(seconds: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(seconds)) if seconds else "unknown"


def _short(session_id: str) -> str:
    return session_id[:8]


def print_location(location: Location, verbose: bool) -> None:
    sessions, unreadable = sessions_in(location)
    newest = max((s.last_activity for s in sessions), default=0)
    print(f"  {location.label}  ({len(sessions)} sessions, newest {_when(newest)})")
    by_project: Dict[str, List[Session]] = {}
    for session in sessions:
        by_project.setdefault(session.project or session.project_key or "(unknown project)", []).append(session)
    for project, members in sorted(by_project.items()):
        print(f"      {len(members):4d}  {project}")
        if verbose:
            for s in sorted(members, key=lambda s: s.last_activity, reverse=True):
                print(f"            {s.session_id}  {_when(s.last_activity)}  {s.title}".rstrip())
    if unreadable:
        print(f"      ({len(unreadable)} index files could not be parsed and are left alone)")


def print_decisions(decisions: List[Decision], dry_run: bool) -> None:
    for d in decisions:
        label = d.session.title or d.session.project or d.session.project_key
        if d.verdict in SKIP_REASONS:
            print(f"skip    {_short(d.session.session_id)}  {label}  ({SKIP_REASONS[d.verdict]})")
            continue
        if not d.ops:
            continue
        print(f"{d.verdict:<7} {_short(d.session.session_id)}  {label}")
        for op in d.ops:
            if op.kind == "write":
                state = "overwrite" if op.path.exists() else "new"
                print(f"          {'would write' if dry_run else 'write'}  {op.path}  ({state})")
            else:
                print(f"          {'would remove' if dry_run else 'remove'} {op.path}")


# --------------------------------------------------------------------------- commands


def cmd_list(args: argparse.Namespace) -> int:
    if args.store in ("desktop", "all"):
        root = args.desktop_root
        locations = desktop_locations(root)
        print(f"Claude Desktop accounts  ({root})")
        if not locations:
            print("  none found")
        for location in locations:
            print_location(location, args.verbose)
    if args.store in ("cli", "all"):
        locations = cli_locations(args.config_dir)
        print("Claude Code CLI config dirs")
        if not locations:
            print("  none found (looked at $CLAUDE_CONFIG_DIR, ~/.claude, ~/.claude-*, ~/.config/claude*)")
        for location in locations:
            print_location(location, args.verbose)
    return 0


def cmd_transfer(args: argparse.Namespace) -> int:
    if not (args.all or args.project or args.session):
        raise UsageError("say which sessions: --all, --project PATH or --session ID")
    source = resolve_location(args.source, args.desktop_root)
    target = resolve_location(args.target, args.desktop_root)
    if source.store != target.store:
        raise UsageError(f"--from is a {source.store} location but --to is a {target.store} location")
    if source.root.resolve() == target.root.resolve():
        raise UsageError("--from and --to are the same folder")
    if source.store == "cli" and not (source.root / "projects").is_dir():
        raise UsageError(f"{source.root} has no projects/ folder, so it holds no sessions")
    _refuse_if_app_running(source.store, args)

    sessions, unreadable = sessions_in(source)
    for name in unreadable:
        print(f"note: {name} in the source could not be parsed and is left alone", file=sys.stderr)
    decisions = plan_transfer(source, target, select_sessions(sessions, args), args.command == "move")
    ops = [op for d in decisions for op in d.ops]

    print(f"{args.command} {source.store} sessions: {source.label} -> {target.label}")
    print_decisions(decisions, args.dry_run)
    skipped = sum(d.verdict in SKIP_REASONS for d in decisions)
    writes = sum(op.kind == "write" for op in ops)
    removes = len(ops) - writes
    if not ops:
        print(f"Nothing to do: {len(decisions) - skipped} already in the target, {skipped} skipped.")
        return 0
    if args.dry_run:
        print(f"Dry run, nothing changed: would write {writes} files and remove {removes}; {skipped} skipped.")
        return 0
    backup = apply_with_backup(ops, args.backup_dir, {
        "command": args.command, "store": source.store, "from": str(source.root), "to": str(target.root),
    })
    print(f"Done: wrote {writes} files, removed {removes}; {skipped} skipped.")
    print(f"Backup: {backup}")
    print(f"Undo with: {PROG} restore-backup {backup.name}")
    if source.store == "desktop":
        print("Restart Claude Desktop to see the change.")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    if not args.backup:
        backups = backup_dirs(args.backup_dir)
        print(f"Backups in {args.backup_dir}")
        if not backups:
            print("  none")
        for b in backups:
            m = load_manifest(b)
            print(f"  {b.name}  {m.get('command')} {m.get('store')}  {len(m.get('entries', []))} files  "
                  f"{m.get('from', '')} -> {m.get('to', '')}  [{m.get('status')}]")
        return 0
    backup = find_backup(args.backup_dir, args.backup)
    store = str(load_manifest(backup).get("store"))
    _refuse_if_app_running(store, args)
    ops = plan_restore(backup)
    print(f"restore {backup.name}")
    for op in ops:
        verb = ("would " if args.dry_run else "") + ("restore" if op.kind == "write" else "remove")
        print(f"  {verb} {op.path}")
    if args.dry_run:
        print(f"Dry run, nothing changed: {len(ops)} files would change.")
        return 0
    if ops:
        undo = apply_with_backup(ops, args.backup_dir, {"command": "restore", "store": store,
                                                        "from": backup.name, "to": ""})
        print(f"Restored {len(ops)} files. The state before this restore is saved in {undo.name}.")
    else:
        print("Nothing to restore: every file already matches the backup.")
    for directory in remove_created_dirs(backup):
        print(f"  removed empty folder {directory}")
    return 0


def _add_app_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--wait-for-app-exit", type=int, nargs="?", const=600, default=0, metavar="SECONDS",
                        help="if Claude Desktop is running, wait for it to quit (default 600 s), then write")
    parser.add_argument("--allow-running-app", action="store_true",
                        help="write Desktop folders even though Claude Desktop is running")


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--desktop-root", type=Path, default=None,
                        help="Claude Desktop's claude-code-sessions folder (env CLAUDE_DESKTOP_SESSIONS_DIR)")
    common.add_argument("--backup-dir", type=Path, default=None,
                        help="where backups go (env CLAUDE_SESSION_MOVER_BACKUP_DIR; "
                             "default ~/.local/share/claude-session-mover/backups)")

    parser = argparse.ArgumentParser(prog=PROG, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    listing = commands.add_parser("list", parents=[common], help="show accounts / config dirs and their sessions per project")
    listing.add_argument("--store", choices=("all", "desktop", "cli"), default="all")
    listing.add_argument("--config-dir", action="append", default=[], help="extra CLI config dir to include (repeatable)")
    listing.add_argument("-v", "--verbose", action="store_true", help="list every session, not just counts")
    listing.set_defaults(handler=cmd_list)

    for name, summary in (("copy", "copy sessions to another account"), ("move", "move sessions to another account")):
        transfer = commands.add_parser(name, parents=[common], help=summary,
                                       description=f"{summary.capitalize()}. A Desktop account is named by its "
                                                   "<account>/<org> id (prefixes are fine); a CLI account by its config dir path.")
        transfer.add_argument("--from", dest="source", required=True, metavar="ACCOUNT")
        transfer.add_argument("--to", dest="target", required=True, metavar="ACCOUNT")
        transfer.add_argument("--all", action="store_true", help="every session in the source")
        transfer.add_argument("--project", action="append", default=[], metavar="PATH",
                              help="sessions whose working directory is PATH (repeatable)")
        transfer.add_argument("--session", action="append", default=[], metavar="ID",
                              help="one session by id or unique id prefix (repeatable)")
        transfer.add_argument("--dry-run", action="store_true", help="print every change and make none")
        _add_app_flags(transfer)
        transfer.set_defaults(handler=cmd_transfer)

    restore = commands.add_parser("restore-backup", parents=[common],
                                  help="undo a copy, move or restore; with no BACKUP, list backups")
    restore.add_argument("backup", nargs="?", metavar="BACKUP", help="backup folder name (prefix) or 'latest'")
    restore.add_argument("--dry-run", action="store_true")
    _add_app_flags(restore)
    restore.set_defaults(handler=cmd_restore)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    args.desktop_root = (args.desktop_root.expanduser() if args.desktop_root else default_desktop_root())
    args.backup_dir = (args.backup_dir.expanduser() if args.backup_dir else default_backup_root())
    try:
        return args.handler(args)
    except UsageError as error:
        print(f"{PROG}: {error}", file=sys.stderr)
        return 2
    except BrokenPipeError:
        # The reader (for example `| head`) closed the pipe; point stdout at /dev/null so the
        # interpreter's final flush does not raise a second time.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 1


if __name__ == "__main__":
    sys.exit(main())
