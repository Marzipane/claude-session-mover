"""End-to-end tests through the CLI entry point, against synthetic config dirs in a temp HOME.

Every account id, path and transcript here is invented.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import plistlib
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import claude_session_mover as csm  # noqa: E402

ACCOUNT_A = "aaaaaaaa-0000-4000-8000-000000000001"
ACCOUNT_B = "bbbbbbbb-0000-4000-8000-000000000002"
ORG = "cccccccc-0000-4000-8000-000000000003"
PROJECT_ONE = "/home/dev/demo-app"
PROJECT_TWO = "/home/dev/other-app"
SESSION_1 = "11111111-1111-4111-8111-111111111111"
SESSION_2 = "22222222-2222-4222-8222-222222222222"
SESSION_3 = "33333333-3333-4333-8333-333333333333"
# A process name that always runs, to stand in for Claude Desktop in the running-app checks.
ALWAYS_RUNNING = {"darwin": "launchd", "win32": "svchost"}.get(sys.platform)


def snapshot(root: Path) -> dict:
    """Every file under root with its bytes, so a test can prove nothing changed."""
    if not root.exists():
        return {}
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


class Sandbox(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.desktop = self.home / "desktop-sessions"
        self.backups = self.home / "backups"
        env = {
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),  # Path.home() on Windows
            "APPDATA": str(self.home / "AppData" / "Roaming"),
            "LOCALAPPDATA": str(self.home / "AppData" / "Local"),
            "CLAUDE_DESKTOP_SESSIONS_DIR": str(self.desktop),
            "CLAUDE_SESSION_MOVER_BACKUP_DIR": str(self.backups),
            "CLAUDE_SESSION_MOVER_APP_NAME": "no-such-process-csm-test",
        }
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
        os.environ.pop("XDG_DATA_HOME", None)
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = csm.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    # --- desktop fixtures

    def desktop_account(self, account: str) -> Path:
        folder = self.desktop / account / ORG
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def desktop_chat(self, account: str, session_id: str, cwd: str, last_activity: int, title="Demo chat") -> Path:
        path = self.desktop_account(account) / f"local_{session_id}.json"
        path.write_text(json.dumps({
            "sessionId": f"local_{session_id}", "cliSessionId": session_id, "cwd": cwd,
            "lastActivityAt": last_activity, "title": title,
        }))
        return path

    # --- cli fixtures

    def config_dir(self, name: str) -> Path:
        folder = self.home / name
        (folder / "projects").mkdir(parents=True, exist_ok=True)
        return folder

    def transcript(self, config: Path, cwd: str, session_id: str, lines: int, sidecar=False) -> Path:
        project = config / "projects" / csm.encode_project(cwd)
        project.mkdir(parents=True, exist_ok=True)
        path = project / f"{session_id}.jsonl"
        path.write_text("".join(
            json.dumps({"type": "user", "cwd": cwd, "sessionId": session_id, "n": n}) + "\n" for n in range(lines)
        ))
        if sidecar:
            (project / session_id / "subagents").mkdir(parents=True, exist_ok=True)
            (project / session_id / "subagents" / "agent-1.jsonl").write_text('{"type":"assistant"}\n')
        return path


class ListTests(Sandbox):
    def test_lists_desktop_accounts_and_cli_dirs_with_sessions_per_project(self):
        self.desktop_chat(ACCOUNT_A, SESSION_1, PROJECT_ONE, 1_700_000_000_000)
        self.desktop_chat(ACCOUNT_A, SESSION_2, PROJECT_ONE, 1_700_000_100_000)
        self.desktop_chat(ACCOUNT_A, SESSION_3, PROJECT_TWO, 1_700_000_200_000)
        self.desktop_account(ACCOUNT_B)
        work = self.config_dir(".claude-work")
        self.transcript(work, PROJECT_ONE, SESSION_1, 3)

        code, out, _ = self.run_cli("list", "-v")

        self.assertEqual(code, 0)
        self.assertIn(f"{ACCOUNT_A}/{ORG}  (3 sessions", out)
        self.assertIn(f"{ACCOUNT_B}/{ORG}  (0 sessions", out)
        self.assertIn(f"   2  {PROJECT_ONE}", out)
        self.assertIn(f"   1  {PROJECT_TWO}", out)
        self.assertIn(str(work), out)
        self.assertIn(SESSION_1, out)

    def test_every_command_has_working_help(self):
        commands = ["list", "copy", "move", "restore-backup", "sync", "install-agent", "uninstall-agent", "status"]
        for command in [[]] + [[c] for c in commands]:
            with self.subTest(command=command), self.assertRaises(SystemExit) as exited:
                self.run_cli(*command, "--help")
            self.assertEqual(exited.exception.code, 0)

    def test_missing_dirs_are_reported_not_crashed_on(self):
        code, out, _ = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertIn("Claude Desktop accounts", out)
        self.assertEqual(out.count("none found"), 2)


class DesktopTransferTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.chat_1 = self.desktop_chat(ACCOUNT_A, SESSION_1, PROJECT_ONE, 1_700_000_000_000)
        self.chat_2 = self.desktop_chat(ACCOUNT_A, SESSION_2, PROJECT_TWO, 1_700_000_100_000)
        self.target = self.desktop_account(ACCOUNT_B)

    def test_copy_all_adds_every_chat_and_keeps_the_source(self):
        source_before = snapshot(self.chat_1.parent)

        code, out, _ = self.run_cli("copy", "--from", "aaaa", "--to", "bbbb", "--all")

        self.assertEqual(code, 0, out)
        self.assertEqual(snapshot(self.target), source_before)
        self.assertEqual(snapshot(self.chat_1.parent), source_before)
        self.assertIn("Backup:", out)

    def test_dry_run_changes_nothing_and_makes_no_backup(self):
        before = snapshot(self.desktop)

        code, out, _ = self.run_cli("move", "--from", "aaaa", "--to", "bbbb", "--all", "--dry-run")

        self.assertEqual(code, 0)
        self.assertEqual(snapshot(self.desktop), before)
        self.assertFalse(self.backups.exists())
        self.assertIn(f"would write  {self.target / self.chat_1.name}  (new)", out)
        self.assertIn(f"would remove {self.chat_1}", out)

    def test_move_by_session_id_moves_only_that_chat(self):
        code, out, _ = self.run_cli("move", "--from", ACCOUNT_A, "--to", ACCOUNT_B, "--session", SESSION_1[:8])

        self.assertEqual(code, 0, out)
        self.assertTrue((self.target / self.chat_1.name).exists())
        self.assertFalse(self.chat_1.exists())
        self.assertTrue(self.chat_2.exists())
        self.assertFalse((self.target / self.chat_2.name).exists())

    def test_copy_twice_changes_nothing_the_second_time(self):
        self.run_cli("copy", "--from", "aaaa", "--to", "bbbb", "--all")
        after_first = snapshot(self.target)
        backups_after_first = list(self.backups.iterdir())

        code, out, _ = self.run_cli("copy", "--from", "aaaa", "--to", "bbbb", "--all")

        self.assertEqual(code, 0)
        self.assertIn("Nothing to do: 2 already in the target", out)
        self.assertEqual(snapshot(self.target), after_first)
        self.assertEqual(list(self.backups.iterdir()), backups_after_first)

    def test_newer_target_chat_is_kept(self):
        newer = self.desktop_chat(ACCOUNT_B, SESSION_1, PROJECT_ONE, 1_800_000_000_000, title="Newer")
        before = newer.read_bytes()

        code, out, _ = self.run_cli("copy", "--from", "aaaa", "--to", "bbbb", "--session", SESSION_1)

        self.assertEqual(code, 0)
        self.assertIn("target copy is newer", out)
        self.assertEqual(newer.read_bytes(), before)

    def test_older_target_chat_is_updated(self):
        older = self.desktop_chat(ACCOUNT_B, SESSION_1, PROJECT_ONE, 1_600_000_000_000)

        self.run_cli("copy", "--from", "aaaa", "--to", "bbbb", "--session", SESSION_1)

        self.assertEqual(older.read_bytes(), self.chat_1.read_bytes())

    def test_chat_deleted_in_target_is_not_brought_back(self):
        (self.target / f"deleted_{SESSION_1}").write_text("")

        code, out, _ = self.run_cli("copy", "--from", "aaaa", "--to", "bbbb", "--all")

        self.assertEqual(code, 0)
        self.assertIn("deleted in the target account", out)
        self.assertFalse((self.target / self.chat_1.name).exists())

    def test_ambiguous_or_unknown_account_is_a_usage_error(self):
        self.desktop_account("aaaa9999-0000-4000-8000-000000000009")
        code, _, err = self.run_cli("copy", "--from", "aaaa", "--to", "bbbb", "--all")
        self.assertEqual(code, 2)
        self.assertIn("matches more than one account folder", err)

        code, _, err = self.run_cli("copy", "--from", "zzzz", "--to", "bbbb", "--all")
        self.assertEqual(code, 2)
        self.assertIn("no Claude Desktop account folder matches 'zzzz'", err)

    def test_a_selection_is_required(self):
        code, _, err = self.run_cli("copy", "--from", ACCOUNT_A, "--to", ACCOUNT_B)
        self.assertEqual(code, 2)
        self.assertIn("--all, --project PATH or --session ID", err)

    @unittest.skipUnless(ALWAYS_RUNNING, "the running-app check exists on macOS and Windows")
    def test_refuses_to_write_while_the_desktop_app_runs(self):
        os.environ["CLAUDE_SESSION_MOVER_APP_NAME"] = ALWAYS_RUNNING
        before = snapshot(self.desktop)

        code, _, err = self.run_cli("copy", "--from", "aaaa", "--to", "bbbb", "--all")

        self.assertEqual(code, 2)
        self.assertIn("Claude Desktop is running", err)
        self.assertEqual(snapshot(self.desktop), before)

    @unittest.skipUnless(ALWAYS_RUNNING, "the running-app check exists on macOS and Windows")
    def test_waiting_for_the_app_gives_up_after_the_timeout(self):
        os.environ["CLAUDE_SESSION_MOVER_APP_NAME"] = ALWAYS_RUNNING
        before = snapshot(self.desktop)

        code, _, err = self.run_cli("copy", "--from", "aaaa", "--to", "bbbb", "--all", "--wait-for-app-exit", "1")

        self.assertEqual(code, 2)
        self.assertIn("still running after 1 s", err)
        self.assertEqual(snapshot(self.desktop), before)

    def test_waiting_for_an_app_that_is_not_running_proceeds_at_once(self):
        code, out, _ = self.run_cli("copy", "--from", "aaaa", "--to", "bbbb", "--all", "--wait-for-app-exit")

        self.assertEqual(code, 0, out)
        self.assertTrue((self.target / self.chat_1.name).exists())


class CliTransferTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.source = self.config_dir(".claude")
        self.target = self.config_dir(".claude-work")
        self.t1 = self.transcript(self.source, PROJECT_ONE, SESSION_1, 4, sidecar=True)
        self.t2 = self.transcript(self.source, PROJECT_TWO, SESSION_2, 2)
        (self.source / ".credentials.json").write_text('{"token": "synthetic-not-a-secret"}')
        (self.source.parent / ".claude.json").write_text('{"oauthAccount": "synthetic"}')

    def target_file(self, path: Path) -> Path:
        return self.target / path.relative_to(self.source)

    def test_copy_per_project_copies_transcript_and_sidecar_only_for_that_project(self):
        code, out, _ = self.run_cli("copy", "--from", str(self.source), "--to", str(self.target),
                                    "--project", PROJECT_ONE)

        self.assertEqual(code, 0, out)
        self.assertEqual(self.target_file(self.t1).read_bytes(), self.t1.read_bytes())
        self.assertTrue((self.target_file(self.t1).parent / SESSION_1 / "subagents" / "agent-1.jsonl").exists())
        self.assertFalse(self.target_file(self.t2).exists())

    def test_credentials_are_never_copied(self):
        self.run_cli("copy", "--from", str(self.source), "--to", str(self.target), "--all")

        copied = set(snapshot(self.target))
        self.assertTrue(all(name.startswith("projects/") for name in copied), copied)

    def test_move_removes_source_files_and_empty_folders(self):
        code, out, _ = self.run_cli("move", "--from", str(self.source), "--to", str(self.target), "--all")

        self.assertEqual(code, 0, out)
        self.assertEqual(snapshot(self.source / "projects"), {})
        self.assertEqual(list((self.source / "projects").iterdir()), [])
        self.assertTrue((self.source / ".credentials.json").exists())

    def test_longer_source_transcript_updates_the_target(self):
        self.run_cli("copy", "--from", str(self.source), "--to", str(self.target), "--all")
        with self.t1.open("a") as transcript:
            transcript.write('{"type":"user","n":99}\n')

        code, out, _ = self.run_cli("copy", "--from", str(self.source), "--to", str(self.target), "--all")

        self.assertEqual(code, 0)
        self.assertIn("update", out)
        self.assertEqual(self.target_file(self.t1).read_bytes(), self.t1.read_bytes())

    def test_longer_target_and_diverged_transcripts_are_left_alone(self):
        self.run_cli("copy", "--from", str(self.source), "--to", str(self.target), "--all")
        with self.target_file(self.t1).open("a") as transcript:
            transcript.write('{"type":"user","n":100}\n')
        self.target_file(self.t2).write_text('{"type":"user","n":"rewritten"}\n')
        before = snapshot(self.target)

        code, out, _ = self.run_cli("copy", "--from", str(self.source), "--to", str(self.target), "--all")

        self.assertEqual(code, 0)
        self.assertIn("target copy is newer", out)
        self.assertIn("both copies changed", out)
        self.assertEqual(snapshot(self.target), before)

    def test_missing_folders_are_usage_errors(self):
        missing = self.home / "no-such-config"
        code, _, err = self.run_cli("copy", "--from", str(missing), "--to", str(self.target), "--all")
        self.assertEqual(code, 2)
        self.assertIn("folder not found", err)

        code, _, err = self.run_cli("copy", "--from", str(self.source), "--to", str(missing), "--all")
        self.assertEqual(code, 2)
        self.assertIn("folder not found", err)

        empty = self.home / "empty"
        empty.mkdir()
        code, _, err = self.run_cli("copy", "--from", str(empty), "--to", str(self.target), "--all")
        self.assertEqual(code, 2)
        self.assertIn("has no projects/ folder", err)

    def test_unknown_project_and_session_are_usage_errors(self):
        code, _, err = self.run_cli("copy", "--from", str(self.source), "--to", str(self.target),
                                    "--project", "/home/dev/nothing-here")
        self.assertEqual(code, 2)
        self.assertIn("no session in the source belongs to project", err)

        code, _, err = self.run_cli("copy", "--from", str(self.source), "--to", str(self.target), "--session", "ffff")
        self.assertEqual(code, 2)
        self.assertIn("no session in the source starts with 'ffff'", err)


ACCOUNT_C = "dddddddd-0000-4000-8000-000000000004"


class SyncTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.trash = self.home / "sync-trash"
        self.folders = [self.desktop_account(a) for a in (ACCOUNT_A, ACCOUNT_B, ACCOUNT_C)]

    def sync(self, *extra):
        return self.run_cli("sync", "--trash", str(self.trash), *extra)

    def test_a_chat_in_one_account_reaches_every_account_with_its_mtime(self):
        chat = self.desktop_chat(ACCOUNT_A, SESSION_1, PROJECT_ONE, 1_700_000_000_000)
        os.utime(chat, (1_700_000_000, 1_700_000_000))

        code, out, _ = self.sync()

        self.assertEqual(code, 0, out)
        for folder in self.folders:
            copy = folder / chat.name
            self.assertEqual(copy.read_bytes(), chat.read_bytes())
            self.assertEqual(copy.stat().st_mtime, 1_700_000_000)
        self.assertIn(f"add {chat.name} (2 folders)", out)

    def test_larger_last_activity_wins_even_over_a_newer_mtime(self):
        active = self.desktop_chat(ACCOUNT_A, SESSION_1, PROJECT_ONE, 1_800_000_000_000, title="Active")
        stale = self.desktop_chat(ACCOUNT_B, SESSION_1, PROJECT_ONE, 1_700_000_000_000, title="Stale")
        os.utime(active, (1_000_000_000, 1_000_000_000))

        self.sync()

        for folder in self.folders:
            self.assertEqual((folder / active.name).read_bytes(), active.read_bytes())
        self.assertIn("Active", stale.read_text())

    def test_other_files_take_the_newest_mtime(self):
        old, new = self.folders[0] / "scheduled-tasks.json", self.folders[1] / "scheduled-tasks.json"
        old.write_text('{"tasks": "old"}')
        new.write_text('{"tasks": "new"}')
        os.utime(old, (1_000_000_000, 1_000_000_000))
        os.utime(new, (1_100_000_000, 1_100_000_000))
        nested = self.folders[2] / "waiting-input" / f"local_{SESSION_2}.json"
        nested.parent.mkdir()
        nested.write_text("{}")

        self.sync()

        for folder in self.folders:
            self.assertEqual((folder / "scheduled-tasks.json").read_text(), '{"tasks": "new"}')
            self.assertTrue((folder / "waiting-input" / nested.name).exists())

    def test_a_deleted_marker_moves_that_chat_out_of_every_folder_into_the_trash(self):
        for account in (ACCOUNT_A, ACCOUNT_B, ACCOUNT_C):
            self.desktop_chat(account, SESSION_1, PROJECT_ONE, 1_700_000_000_000)
        keep = self.desktop_chat(ACCOUNT_A, SESSION_2, PROJECT_ONE, 1_700_000_000_000)
        (self.folders[1] / f"deleted_{SESSION_1}").write_text("")

        code, out, _ = self.sync()

        self.assertEqual(code, 0, out)
        for folder in self.folders:
            self.assertFalse((folder / f"local_{SESSION_1}.json").exists())
            self.assertTrue((folder / f"deleted_{SESSION_1}").exists())
            self.assertTrue((folder / keep.name).exists())
        trashed = sorted(p.name for p in self.trash.rglob(f"local_{SESSION_1}.json"))
        self.assertEqual(len(trashed), 3)

    def test_an_unparsable_chat_is_left_alone(self):
        half_saved = self.desktop_account(ACCOUNT_A) / f"local_{SESSION_1}.json"
        half_saved.write_text('{"lastActivityAt": 17')
        self.desktop_chat(ACCOUNT_B, SESSION_1, PROJECT_ONE, 1_700_000_000_000)

        code, _, _ = self.sync()

        self.assertEqual(code, 0)
        self.assertEqual(half_saved.read_text(), '{"lastActivityAt": 17')
        self.assertFalse((self.folders[2] / half_saved.name).exists())

    def test_dry_run_changes_nothing_and_a_second_run_is_in_step(self):
        self.desktop_chat(ACCOUNT_A, SESSION_1, PROJECT_ONE, 1_700_000_000_000)
        before = snapshot(self.desktop)

        code, out, _ = self.sync("--dry-run")
        self.assertEqual(code, 0)
        self.assertIn(f"would add local_{SESSION_1}.json (2 folders)", out)
        self.assertEqual(snapshot(self.desktop), before)

        self.sync()
        code, out, _ = self.sync()
        self.assertEqual(out, "3 account folders in step, nothing to do\n")
        code, out, _ = self.sync("--quiet")
        self.assertEqual(out, "")

    def test_a_second_sync_while_one_runs_does_nothing(self):
        self.desktop_chat(ACCOUNT_A, SESSION_1, PROJECT_ONE, 1_700_000_000_000)
        before = snapshot(self.desktop)

        with csm.sync_lock(self.desktop) as held:
            self.assertTrue(held)
            code, out, _ = self.sync()

        self.assertEqual((code, out), (0, ""))
        self.assertEqual(snapshot(self.desktop), before)

    def test_log_appends_output_and_records_a_failure_with_exit_code_1(self):
        self.desktop_chat(ACCOUNT_A, SESSION_1, PROJECT_ONE, 1_700_000_000_000)
        log = self.home / "logs" / "sync.log"

        code, out, _ = self.sync("--log", str(log))
        self.assertEqual((code, out), (0, ""))
        self.assertIn(f"add local_{SESSION_1}.json (2 folders)", log.read_text())

        (self.folders[0] / f"deleted_{SESSION_1}").write_text("")
        self.trash.write_text("a file where the trash folder should be")
        code, _, _ = self.sync("--log", str(log))
        self.assertEqual(code, 1)
        self.assertIn("Traceback", log.read_text())


FAKE_LAUNCHCTL = """#!/bin/sh
# Stands in for launchctl: logs every call; 'print' succeeds only while the job is bootstrapped.
echo "$*" >> "$FAKE_LAUNCHCTL_LOG"
case "$1" in
  print) [ -f "$FAKE_LAUNCHCTL_STATE" ] && cat "$FAKE_LAUNCHCTL_STATE" && exit 0; exit 113 ;;
  bootstrap) printf '\\tstate = waiting\\n\\tlast exit code = (never exited)\\n' > "$FAKE_LAUNCHCTL_STATE" ;;
  bootout) rm -f "$FAKE_LAUNCHCTL_STATE" ;;
esac
"""


@unittest.skipUnless(sys.platform == "darwin", "the sync agent is a macOS LaunchAgent")
class MacAgentTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.desktop_account(ACCOUNT_A)
        self.desktop_account(ACCOUNT_B)
        fake = self.home / "fake-launchctl"
        fake.write_text(FAKE_LAUNCHCTL)
        fake.chmod(0o755)
        self.calls = self.home / "launchctl-calls.log"
        self.state = self.home / "launchctl-state"
        os.environ.update({
            "CLAUDE_SESSION_MOVER_LAUNCHCTL": str(fake),
            "FAKE_LAUNCHCTL_LOG": str(self.calls),
            "FAKE_LAUNCHCTL_STATE": str(self.state),
        })
        self.plist = self.home / "Library" / "LaunchAgents" / f"{csm.agent_name()}.plist"

    def launchctl_calls(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def test_install_writes_an_agent_that_runs_sync_and_loads_it(self):
        code, out, err = self.run_cli("install-agent")

        self.assertEqual(code, 0, err)
        with self.plist.open("rb") as f:
            agent = plistlib.load(f)
        self.assertEqual(agent["Label"], csm.agent_name())
        self.assertEqual(agent["ProgramArguments"][2:], [
            str(Path(csm.__file__).resolve()), "sync", "--quiet", "--desktop-root", str(self.desktop)])
        self.assertEqual(agent["StartInterval"], 20)
        self.assertTrue(agent["RunAtLoad"])
        self.assertIn(str(self.desktop), agent["WatchPaths"])
        self.assertIn(f"bootstrap gui/{os.getuid()} {self.plist}", self.launchctl_calls())

    def test_reinstalling_unloads_the_old_agent_first(self):
        self.run_cli("install-agent")
        self.run_cli("install-agent")

        calls = [c.split()[0] for c in self.launchctl_calls() if not c.startswith("print")]
        self.assertEqual(calls, ["bootstrap", "bootout", "bootstrap"])

    def test_status_is_healthy_after_install_and_reports_a_failing_run(self):
        self.run_cli("install-agent")
        code, out, _ = self.run_cli("status")
        self.assertEqual(code, 0, out)
        self.assertIn("healthy", out)

        self.state.write_text("\tlast exit code = 2\n")
        code, out, _ = self.run_cli("status")
        self.assertEqual(code, 1)
        self.assertIn("last run failed (exit code 2)", out)

    def test_status_reports_a_missing_script(self):
        self.run_cli("install-agent")
        with self.plist.open("rb") as f:
            agent = plistlib.load(f)
        agent["ProgramArguments"][2] = str(self.home / "gone.py")
        with self.plist.open("wb") as f:
            plistlib.dump(agent, f)

        code, out, _ = self.run_cli("status")

        self.assertEqual(code, 1)
        self.assertIn(f"script missing: {self.home / 'gone.py'}", out)

    def test_uninstall_unloads_and_removes_the_agent(self):
        self.run_cli("install-agent")

        code, out, _ = self.run_cli("uninstall-agent")

        self.assertEqual(code, 0, out)
        self.assertFalse(self.plist.exists())
        self.assertIn(f"bootout gui/{os.getuid()}/{csm.agent_name()}", self.launchctl_calls())
        code, out, _ = self.run_cli("status")
        self.assertEqual(code, 1)
        self.assertIn("not installed", out)


@unittest.skipUnless(sys.platform == "win32", "Windows only")
class WindowsDesktopRootTests(Sandbox):
    def setUp(self):
        super().setUp()
        os.environ.pop("CLAUDE_DESKTOP_SESSIONS_DIR")
        self.roaming = self.home / "AppData" / "Roaming" / "Claude" / "claude-code-sessions"
        self.packaged = (self.home / "AppData" / "Local" / "Packages" / "Claude_test123" / "LocalCache"
                         / "Roaming" / "Claude" / "claude-code-sessions")

    def chat_in(self, root: Path, mtime: int) -> None:
        folder = root / ACCOUNT_A / ORG
        folder.mkdir(parents=True)
        chat = folder / f"local_{SESSION_1}.json"
        chat.write_text("{}")
        os.utime(chat, (mtime, mtime))

    def listed_root(self) -> str:
        _, out, _ = self.run_cli("list", "--store", "desktop")
        return out.splitlines()[0]

    def test_defaults_to_appdata_roaming(self):
        self.assertEqual(self.listed_root(), f"Claude Desktop accounts  ({self.roaming})")

    def test_uses_the_store_package_folder_when_only_it_exists(self):
        self.chat_in(self.packaged, 1_700_000_000)
        self.assertEqual(self.listed_root(), f"Claude Desktop accounts  ({self.packaged})")

    def test_picks_the_folder_with_the_newest_file_when_both_exist(self):
        self.chat_in(self.roaming, 1_700_000_000)
        self.chat_in(self.packaged, 1_600_000_000)
        self.assertEqual(self.listed_root(), f"Claude Desktop accounts  ({self.roaming})")


@unittest.skipUnless(sys.platform == "win32", "the sync agent is a scheduled task on Windows")
class WindowsAgentTests(Sandbox):
    """Against the real Task Scheduler, under a task name of their own."""

    def setUp(self):
        super().setUp()
        os.environ["CLAUDE_SESSION_MOVER_AGENT_NAME"] = f"csm-test-{os.getpid()}-{id(self)}"
        self.addCleanup(csm._powershell,
                        f"Unregister-ScheduledTask -TaskName '{csm.agent_name()}' -Confirm:$false "
                        "-ErrorAction SilentlyContinue")
        self.chat = self.desktop_chat(ACCOUNT_A, SESSION_1, PROJECT_ONE, 1_700_000_000_000)
        self.other = self.desktop_account(ACCOUNT_B)

    def wait_for(self, condition, seconds=90):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(1)
        self.fail(f"gave up after {seconds} s; status says: {self.run_cli('status')[1]}")

    def test_install_runs_sync_hidden_and_status_reports_healthy(self):
        code, out, err = self.run_cli("install-agent")
        self.assertEqual(code, 0, err)

        self.wait_for(lambda: (self.other / self.chat.name).exists())
        self.wait_for(lambda: self.run_cli("status")[0] == 0)
        state = csm.agent_state()
        self.assertTrue(state.defined and state.loaded)
        self.assertEqual(state.script, Path(csm.__file__).resolve())
        self.assertIn(f"add {self.chat.name} (1 folder)", (self.home / "AppData" / "Local" / csm.PROG
                                                           / "sync.log").read_text())

    def test_status_reports_a_failing_run_and_uninstall_removes_the_task(self):
        self.run_cli("install-agent")
        csm._powershell(f"Set-ScheduledTask -TaskName '{csm.agent_name()}' "
                        "-Action (New-ScheduledTaskAction -Execute 'cmd.exe' -Argument '/c exit 2') | Out-Null; "
                        f"Start-ScheduledTask -TaskName '{csm.agent_name()}'")

        self.wait_for(lambda: "last run failed (exit code 2)" in self.run_cli("status")[1])

        code, out, _ = self.run_cli("uninstall-agent")
        self.assertEqual(code, 0)
        self.assertIn("Removed", out)
        code, out, _ = self.run_cli("status")
        self.assertEqual(code, 1)
        self.assertIn("not installed", out)
        self.assertIn("was not installed", self.run_cli("uninstall-agent")[1])


class BackupAndRestoreTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.source = self.config_dir(".claude")
        self.target = self.config_dir(".claude-work")
        self.t1 = self.transcript(self.source, PROJECT_ONE, SESSION_1, 6, sidecar=True)
        self.old_target = self.transcript(self.target, PROJECT_ONE, SESSION_1, 2)

    def test_backup_records_the_overwritten_file_and_the_new_ones(self):
        original = self.old_target.read_bytes()

        self.run_cli("copy", "--from", str(self.source), "--to", str(self.target), "--all")

        (backup,) = list(self.backups.iterdir())
        manifest = json.loads((backup / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "complete")
        by_path = {e["path"]: e for e in manifest["entries"]}
        self.assertTrue(by_path[str(self.old_target)]["existed"])
        self.assertEqual((backup / by_path[str(self.old_target)]["saved"]).read_bytes(), original)
        self.assertEqual(sum(not e["existed"] for e in manifest["entries"]), 1)

    def test_restore_puts_the_target_back_and_is_idempotent(self):
        before = snapshot(self.target)
        self.run_cli("copy", "--from", str(self.source), "--to", str(self.target), "--all")
        self.assertNotEqual(snapshot(self.target), before)

        code, out, _ = self.run_cli("restore-backup", "latest", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("would restore", out)
        self.assertNotEqual(snapshot(self.target), before)

        (copy_backup,) = [b.name for b in self.backups.iterdir()]
        code, out, _ = self.run_cli("restore-backup", copy_backup)
        self.assertEqual(code, 0, out)
        self.assertEqual(snapshot(self.target), before)
        self.assertFalse((self.old_target.parent / SESSION_1).exists(), "created sidecar folder is removed")

        code, out, _ = self.run_cli("restore-backup", copy_backup)
        self.assertEqual(code, 0)
        self.assertIn("Nothing to restore", out)
        self.assertEqual(snapshot(self.target), before)

    def test_restore_after_move_brings_the_source_back(self):
        source_before = snapshot(self.source)
        target_before = snapshot(self.target)
        self.run_cli("move", "--from", str(self.source), "--to", str(self.target), "--all")
        self.assertFalse(self.t1.exists())

        code, out, _ = self.run_cli("restore-backup", "latest")

        self.assertEqual(code, 0, out)
        self.assertEqual(snapshot(self.source), source_before)
        self.assertEqual(snapshot(self.target), target_before)

    def test_listing_backups(self):
        code, out, _ = self.run_cli("restore-backup")
        self.assertEqual(code, 0)
        self.assertIn("none", out)

        self.run_cli("copy", "--from", str(self.source), "--to", str(self.target), "--all")
        code, out, _ = self.run_cli("restore-backup")
        self.assertIn("copy cli", out)
        self.assertIn("[complete]", out)


if __name__ == "__main__":
    unittest.main()
