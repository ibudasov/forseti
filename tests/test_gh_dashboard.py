import os
from pathlib import Path
import select
import subprocess
import sys
import termios
import time
from datetime import datetime, timezone
from threading import Event

import pytest

from scripts import gh_dashboard as dashboard


NOW = datetime(2026, 11, 15, 17, 30, tzinfo=timezone.utc)


class FixtureClient:
    def __init__(self, approvals=None, fail_runs=False):
        self.calls = []
        self.approvals = approvals or []
        self.fail_runs = fail_runs

    def query(self, *arguments):
        self.calls.append(arguments)
        if arguments[:2] == ("run", "list"):
            if self.fail_runs:
                raise dashboard.QueryError("HTTP 403: access denied")
            return [{"databaseId": 42, "workflowName": "Tests", "url": "https://example.test/run/42"}]
        if arguments[:2] == ("agent-task", "list"):
            return [
                {"id": "active", "name": "Working", "state": "in_progress", "repository": None},
                {"id": "done", "name": "Finished", "state": "completed"},
                {"id": "idle", "name": "Paused", "state": "idle"},
            ]
        if "pending_deployments" in arguments[1]:
            return self.approvals
        status = arguments[arguments.index("-f") + 1]
        runs = [{"id": 91, "name": "Deploy", "html_url": "https://example.test/run/91"}]
        return [{"workflow_runs": runs if status == "status=waiting" else []}]


def test_snapshot_uses_rolling_two_hour_window_and_keeps_unscoped_active_sessions():
    client = FixtureClient()
    sections = dashboard.fetch_snapshot(client, "owner/repo", NOW)

    assert "--created" in client.calls[0]
    assert ">=2026-11-15T15:30:00Z" in client.calls[0]
    assert "--status" in client.calls[0] and "failure" in client.calls[0]
    assert "--repo" in client.calls[0] and "owner/repo" in client.calls[0]
    assert "42" in "\n".join(sections[0].lines)
    assert "active" in "\n".join(sections[1].lines)
    assert "done" not in "\n".join(sections[1].lines)
    assert "idle" not in "\n".join(sections[1].lines)
    assert not sections[2].lines


def test_approval_queue_only_claims_current_users_eligible_environments():
    client = FixtureClient(approvals=[
        {"environment": {"name": "production"}, "current_user_can_approve": True},
        {"environment": {"name": "restricted"}, "current_user_can_approve": False},
    ])
    section = dashboard.fetch_snapshot(client, "owner/repo", NOW)[2]

    assert "production" in "\n".join(section.lines)
    assert "restricted" not in "\n".join(section.lines)
    assert "https://example.test/run/91" in "\n".join(section.lines)


def test_workflow_action_required_is_labelled_as_candidate_not_confirmed_approval():
    client = FixtureClient()
    original = client.query

    def query(*arguments):
        if "status=action_required" in arguments:
            return [{"workflow_runs": [
                {"id": 92, "name": "Fork CI", "html_url": "https://example.test/run/92"},
            ]}]
        return original(*arguments)

    client.query = query
    section = dashboard.fetch_snapshot(client, "owner/repo", NOW)[2]

    assert "92" in "\n".join(section.lines)
    assert "candidate" in "\n".join(section.lines)
    assert "eligibility unknown" in "\n".join(section.lines)


def test_errors_are_visible_without_hiding_other_sections():
    sections = dashboard.fetch_snapshot(FixtureClient(fail_runs=True), "owner/repo", NOW)

    assert sections[0].error == "HTTP 403: access denied"
    assert "active" in "\n".join(sections[1].lines)
    assert sections[1].error is None


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_interval_rejects_nonpositive_or_nonfinite_values(value):
    with pytest.raises(ValueError, match="finite positive"):
        dashboard.positive_interval(value)


def test_quitting_during_refresh_stops_worker_and_restores_terminal(monkeypatch):
    stop = Event()
    joined = []

    class Worker:
        def join(self, timeout):
            joined.append(timeout)

    class Screen:
        def timeout(self, milliseconds):
            pass

        def getch(self):
            return ord("q")

        def getmaxyx(self):
            return 24, 80

        def erase(self):
            pass

        def addnstr(self, *arguments):
            pass

        def refresh(self):
            pass

    monkeypatch.setattr(dashboard.curses, "curs_set", lambda visibility: None)
    dashboard.watch(Screen(), "owner/repo", 5, dashboard.Queue(), stop, Worker())

    assert stop.is_set()
    assert joined == [2]


def test_refresh_repeats_and_stops_without_another_query(monkeypatch):
    stop = Event()
    results = dashboard.Queue()
    calls = []

    def snapshot(client, repository):
        calls.append(repository)
        if len(calls) == 2:
            stop.set()
        return [dashboard.Section("Results", ["ready"])]

    monkeypatch.setattr(dashboard, "fetch_snapshot", snapshot)
    dashboard.refresh_loop(FixtureClient(), "owner/repo", 0.001, results, stop)

    assert calls == ["owner/repo", "owner/repo"]
    assert results.get_nowait()[1][0].lines == ["ready"]
    assert results.get_nowait()[1][0].lines == ["ready"]


def test_gh_errors_and_invalid_json_are_explicit(monkeypatch):
    class Process:
        returncode = 1

        def __enter__(self):
            return self

        def __exit__(self, *arguments):
            pass

        def communicate(self, timeout):
            return "", "gh: authentication required"

    process = Process()
    monkeypatch.setattr(dashboard.subprocess, "Popen", lambda *args, **kwargs: process)
    client = dashboard.GitHubClient(Event())

    with pytest.raises(dashboard.QueryError, match="authentication required"):
        client.query("agent-task", "list")

    process.returncode = 0
    with pytest.raises(dashboard.QueryError, match="invalid JSON"):
        client.query("agent-task", "list")


def test_cancelled_query_terminates_its_child_process(monkeypatch):
    stop = Event()
    terminated = []

    class Process:
        returncode = None

        def __enter__(self):
            return self

        def __exit__(self, *arguments):
            pass

        def communicate(self, timeout):
            if not stop.is_set():
                stop.set()
                raise dashboard.subprocess.TimeoutExpired("gh", timeout)
            return "", ""

        def terminate(self):
            terminated.append(True)

    monkeypatch.setattr(dashboard.subprocess, "Popen", lambda *args, **kwargs: Process())
    with pytest.raises(dashboard.QueryError, match="cancelled"):
        dashboard.GitHubClient(stop).query("agent-task", "list")

    assert terminated == [True]


def test_once_prints_snapshot_without_terminal_and_reports_errors(monkeypatch, capsys):
    client = FixtureClient(fail_runs=True)
    monkeypatch.setattr(dashboard, "GitHubClient", lambda stop: client)
    monkeypatch.setattr(sys, "argv", ["gh_dashboard.py", "--repo", "owner/repo", "--once"])

    assert dashboard.main() == 1
    output = capsys.readouterr().out
    assert "ERROR: HTTP 403: access denied" in output
    assert "active" in output
    assert "GitHub dashboard: owner/repo" in output


def test_tui_quits_while_gh_is_blocked_and_restores_terminal(tmp_path):
    fake_gh = tmp_path / "gh"
    fake_gh.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(60)\n")
    fake_gh.chmod(0o755)
    master, slave = os.openpty()
    original_attributes = termios.tcgetattr(slave)
    environment = dict(os.environ, TERM="xterm", PATH=f"{tmp_path}:{os.environ['PATH']}")
    script = Path(__file__).resolve().parents[1] / "scripts" / "gh_dashboard.py"
    process = subprocess.Popen(
        [sys.executable, str(script), "--repo", "owner/repo", "--interval", "1"],
        stdin=slave, stdout=slave, stderr=slave, env=environment,
    )
    output = b""
    try:
        deadline = time.monotonic() + 5
        while b"Fetching GitHub activity" not in output and time.monotonic() < deadline:
            readable, _, _ = select.select([master], [], [], 0.1)
            if readable:
                output += os.read(master, 65536)
        assert b"Fetching GitHub activity" in output
        os.write(master, b"q")
        assert process.wait(timeout=3) == 0
        assert termios.tcgetattr(slave) == original_attributes
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)
        os.close(slave)
