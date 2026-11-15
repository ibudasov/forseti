#!/usr/bin/env python3
"""Read-only GitHub activity dashboard; requires Python 3.12+ and authenticated gh."""

from __future__ import annotations

import argparse
import curses
import json
import math
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from queue import Empty, Queue
from threading import Event, Thread
from typing import Any, Callable


class QueryError(RuntimeError):
    pass


class GitHubClient:
    def __init__(self, stop: Event):
        self.stop = stop

    def query(self, *arguments: str) -> Any:
        if self.stop.is_set():
            raise QueryError("Refresh cancelled")
        environment = dict(os.environ, GH_PROMPT_DISABLED="1", GH_PAGER="cat")
        try:
            with subprocess.Popen(
                ["gh", *arguments], stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=environment,
            ) as process:
                output, error = self._communicate(process)
                if process.returncode:
                    raise QueryError(error.strip() or f"gh exited with status {process.returncode}")
        except OSError as error:
            raise QueryError(f"Cannot run gh: {error}") from error
        try:
            return json.loads(output)
        except json.JSONDecodeError as error:
            raise QueryError(f"gh returned invalid JSON: {error}") from error

    def _communicate(self, process: subprocess.Popen[str]) -> tuple[str, str]:
        deadline = time.monotonic() + 20
        while True:
            try:
                return process.communicate(timeout=0.1)
            except subprocess.TimeoutExpired:
                if not self.stop.is_set() and time.monotonic() < deadline:
                    continue
                process.terminate()
                try:
                    process.communicate(timeout=0.5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
                reason = "Refresh cancelled" if self.stop.is_set() else "gh request timed out after 20 seconds"
                raise QueryError(reason)


@dataclass
class Section:
    title: str
    lines: list[str]
    error: str | None = None


def failed_runs(client: GitHubClient, repository: str, now: datetime) -> list[str]:
    cutoff = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    runs = client.query(
        "run", "list", "--repo", repository, "--status", "failure",
        "--created", f">={cutoff}", "--limit", "1000",
        "--json", "databaseId,workflowName,url",
    )
    lines = []
    for run in runs:
        lines.extend([f"{run['databaseId']}  {run['workflowName']}", f"  {run['url']}"])
    if len(runs) == 1000:
        lines.append("WARNING: 1000-run limit reached; results may be incomplete.")
    return lines


def active_sessions(client: GitHubClient) -> list[str]:
    sessions = client.query(
        "agent-task", "list", "--limit", "1000",
        "--json", "id,name,state,repository,pullRequestUrl",
    )
    lines = []
    for session in sessions:
        if session["state"] != "in_progress":
            continue
        repository = session.get("repository") or "no repository"
        lines.append(f"{session['id']}  {session['name']}  ({repository})")
        if session.get("pullRequestUrl"):
            lines.append(f"  {session['pullRequestUrl']}")
    if len(sessions) == 1000:
        lines.append("WARNING: 1000-session limit reached; older active sessions may be omitted.")
    return lines


def approval_queue(client: GitHubClient, repository: str) -> list[str]:
    lines = []
    for status in ("waiting", "action_required"):
        pages = client.query(
            "api", f"repos/{repository}/actions/runs", "--method", "GET",
            "-f", f"status={status}", "-F", "per_page=100", "--paginate", "--slurp",
        )
        for page in pages:
            for run in page["workflow_runs"]:
                lines.extend(approval_lines(client, repository, run, status))
        if sum(len(page["workflow_runs"]) for page in pages) >= 1000:
            lines.append(f"WARNING: GitHub's 1000-run search cap may truncate {status} results.")
    return lines


def approval_lines(client: GitHubClient, repository: str, run: dict[str, Any], status: str) -> list[str]:
    if status == "action_required":
        return [
            f"{run['id']}  {run['name']}  (workflow approval candidate; eligibility unknown)",
            f"  {run['html_url']}",
        ]
    deployments = client.query("api", f"repos/{repository}/actions/runs/{run['id']}/pending_deployments")
    environments = [
        deployment["environment"]["name"]
        for deployment in deployments if deployment["current_user_can_approve"]
    ]
    if not environments:
        return []
    return [f"{run['id']}  {run['name']}  (approve: {', '.join(environments)})", f"  {run['html_url']}"]


def fetch_snapshot(
    client: GitHubClient, repository: str, now: datetime | None = None,
) -> list[Section]:
    now = now or datetime.now(timezone.utc)
    queries: list[tuple[str, Callable[[], list[str]]]] = [
        ("Failed Actions (created in last 2h)", lambda: failed_runs(client, repository, now)),
        ("Active agent sessions (signed-in user, all repositories)", lambda: active_sessions(client)),
        ("Actions awaiting your approval / workflow candidates", lambda: approval_queue(client, repository)),
    ]
    sections = []
    for title, query in queries:
        try:
            sections.append(Section(title, query()))
        except (QueryError, KeyError, TypeError) as error:
            sections.append(Section(title, [], str(error)))
    return sections


def snapshot_lines(repository: str, updated: str, sections: list[Section]) -> list[str]:
    lines = [f"GitHub dashboard: {repository}", f"Last updated: {updated}", ""]
    for section in sections:
        lines.append(section.title)
        if section.error:
            lines.append(f"ERROR: {section.error}")
        else:
            lines.extend(section.lines or ["None."])
        lines.append("")
    return ["".join(character if character.isprintable() else " " for character in line) for line in lines]


def refresh_loop(
    client: GitHubClient, repository: str, interval: float,
    results: Queue[tuple[str, list[Section]]], stop: Event,
) -> None:
    while not stop.is_set():
        sections = fetch_snapshot(client, repository)
        results.put((datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"), sections))
        if stop.wait(interval):
            return


def draw(screen: curses.window, lines: list[str], offset: int, interval: float) -> None:
    height, width = screen.getmaxyx()
    screen.erase()
    visible = lines[offset:offset + max(0, height - 1)]
    footer = f"q: quit | arrows/PgUp/PgDn: scroll | refresh: {interval:g}s after each fetch"
    try:
        for row, line in enumerate(visible):
            screen.addnstr(row, 0, line, max(0, width - 1))
        if height and width > 1:
            screen.addnstr(height - 1, 0, footer, width - 1)
        screen.refresh()
    except curses.error:
        # A concurrent terminal resize can invalidate dimensions; redraw next tick.
        pass


def watch(
    screen: curses.window, repository: str, interval: float, results: Queue[tuple[str, list[Section]]],
    stop: Event, worker: Thread,
) -> None:
    try:
        curses.curs_set(0)
    except curses.error:
        pass  # Some terminals cannot hide the cursor.
    screen.timeout(100)
    lines = [f"GitHub dashboard: {repository}", "Fetching GitHub activity..."]
    offset = 0
    try:
        while True:
            try:
                updated, sections = results.get_nowait()
                lines = snapshot_lines(repository, updated, sections)
            except Empty:
                pass
            height, _ = screen.getmaxyx()
            offset = max(0, min(offset, len(lines) - max(1, height - 1)))
            draw(screen, lines, offset, interval)
            key = screen.getch()
            if key in (ord("q"), ord("Q")):
                return
            offset += scroll_distance(key, height)
    finally:
        stop.set()
        worker.join(timeout=2)


def scroll_distance(key: int, height: int) -> int:
    distances = {
        curses.KEY_DOWN: 1, curses.KEY_UP: -1,
        curses.KEY_NPAGE: max(1, height - 2), curses.KEY_PPAGE: -max(1, height - 2),
    }
    return distances.get(key, 0)


def positive_interval(value: str) -> float:
    interval = float(value)
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError("interval must be a finite positive number")
    return interval


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-R", "--repo", help="OWNER/REPO (default: current repository)")
    parser.add_argument("--interval", type=positive_interval, default=5, help="seconds between fetches (default: 5)")
    parser.add_argument("--once", action="store_true", help="print one snapshot without taking over the terminal")
    arguments = parser.parse_args()
    stop = Event()
    client = GitHubClient(stop)
    try:
        repository = arguments.repo or client.query("repo", "view", "--json", "nameWithOwner")["nameWithOwner"]
        if arguments.once:
            sections = fetch_snapshot(client, repository)
            updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            print("\n".join(snapshot_lines(repository, updated, sections)))
            return int(any(section.error for section in sections))
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            parser.error("interactive mode requires a terminal; use --once for redirected output")
        results: Queue[tuple[str, list[Section]]] = Queue()
        worker = Thread(target=refresh_loop, args=(client, repository, arguments.interval, results, stop), daemon=True)
        worker.start()
        try:
            curses.wrapper(watch, repository, arguments.interval, results, stop, worker)
        finally:
            stop.set()
            worker.join(timeout=2)
    except (QueryError, curses.error) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
