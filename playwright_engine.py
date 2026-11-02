"""Playwright capture engine: runs the Node helper and compares state outcomes."""
import json
import os
import re
import subprocess
import tempfile
from typing import Optional
from urllib.parse import urlparse

from states import State, states_to_json

MIN_NODE_MAJOR = 20
HELPER_TIMEOUT_SECONDS = 900
SAME_SITE_VALUES = ("Strict", "Lax", "None")


class EngineUnavailable(RuntimeError):
    """The Playwright engine cannot run on this machine."""


class PlaywrightEngine:
    def __init__(self, tool_dir: str, timeout_ms: int = 5000):
        self.helper_dir = os.path.join(tool_dir, "playwright_capture")
        self.tool_dir = tool_dir
        self.timeout_ms = timeout_ms

    def ensure_ready(self) -> None:
        self._check_node()
        if os.path.isdir(os.path.join(self.helper_dir, "node_modules", "playwright")):
            return
        install = subprocess.run(
            ["npm", "ci", "--prefix", "playwright_capture"], cwd=self.tool_dir,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if install.returncode != 0:
            lines = [line for line in install.stderr.splitlines() if line.strip()]
            raise EngineUnavailable(f"npm ci failed: {lines[-1] if lines else 'no error output'}")

    @staticmethod
    def _check_node() -> None:
        try:
            done = subprocess.run(["node", "--version"], capture_output=True, text=True)
        except OSError:
            raise EngineUnavailable("Node 20 or newer is required, found none") from None
        found = done.stdout.strip()
        match = re.match(r"^v(\d+)\.", found)
        if done.returncode != 0 or not match or int(match.group(1)) < MIN_NODE_MAJOR:
            raise EngineUnavailable(f"Node 20 or newer is required, found {found or 'none'}")

    def capture(self, url: str, label: str, out_dir: str, sizes: list[str], states: list[State],
                cookies: list[dict], http_credentials: Optional[dict], user_agent: Optional[str],
                omit_screenshots: bool) -> dict:
        request = {
            "url": url, "label": label, "outDir": out_dir, "sizes": sizes,
            "states": states_to_json(states), "cookies": cookies,
            "httpCredentials": http_credentials, "userAgent": user_agent,
            "omitScreenshots": omit_screenshots, "timeoutMs": self.timeout_ms,
        }
        # The request holds cookies and passwords, so the file is private and short-lived.
        request_file = tempfile.NamedTemporaryFile(
            "w", suffix=".json", dir=out_dir, delete=False, encoding="utf-8")
        try:
            with request_file:
                json.dump(request, request_file)
            return self._run_helper(request_file.name)
        finally:
            os.unlink(request_file.name)

    def _run_helper(self, request_path: str) -> dict:
        command = ["node", os.path.join(self.helper_dir, "capture.mjs"), "--request", request_path]
        try:
            done = subprocess.run(
                command, cwd=self.tool_dir, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=HELPER_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return failed_result("capture helper timed out")
        lines = [line for line in done.stdout.splitlines() if line.strip()]
        try:
            return json.loads(lines[-1])
        except (IndexError, json.JSONDecodeError):
            stderr_lines = [line for line in done.stderr.splitlines() if line.strip()]
            detail = stderr_lines[-1] if stderr_lines else f"exit code {done.returncode}"
            return failed_result(f"capture helper gave no result: {detail}")


def failed_result(error: str) -> dict:
    return {"ok": False, "status": None, "error": error, "files": [], "states": []}


def to_playwright_cookies(raw_cookies: list[dict], page_url: str) -> list[dict]:
    """Convert Selenium cookie dicts; a missing domain means the page host."""
    host = urlparse(page_url).hostname or ""
    cookies = []
    for raw in raw_cookies:
        cookie = {
            "name": raw["name"],
            "value": raw["value"],
            "domain": raw.get("domain") or host,
            "path": raw.get("path") or "/",
        }
        if raw.get("expiry") is not None:
            cookie["expires"] = raw["expiry"]
        for key in ("secure", "httpOnly"):
            if key in raw:
                cookie[key] = raw[key]
        if raw.get("sameSite") in SAME_SITE_VALUES:
            cookie["sameSite"] = raw["sameSite"]
        cookies.append(cookie)
    return cookies


def expected_state_entries(states: list[State], sizes: list[str]) -> list[tuple[str, str]]:
    """List every state and width the helper must report; fullscreen never applies."""
    entries = []
    for state in states:
        wanted = state.sizes if state.sizes else sizes
        for size in wanted:
            if size != "fullscreen" and (state.id, size) not in entries:
                entries.append((state.id, size))
    return entries


def states_without_widths(states: list[State], sizes: list[str]) -> list[str]:
    """Return the ids of states that would be captured at no width at all."""
    covered = set()
    for state_id, _ in expected_state_entries(states, sizes):
        covered.add(state_id)
    return [state.id for state in states if state.id not in covered]


def _describe(entry: Optional[dict]) -> str:
    return entry["outcome"] if entry else "missing"


def compare_state_outcomes(expected: list[tuple[str, str]], live: list[dict],
                           dev: list[dict]) -> list[tuple[str, str, str, Optional[str]]]:
    """Return (state id, size, verdict, message) per expected entry.

    The expected list decides what is checked, so a crashed side cannot pass by
    returning nothing.
    """
    live_by_key = {(item["id"], item["size"]): item for item in live}
    dev_by_key = {(item["id"], item["size"]): item for item in dev}
    rows = []
    for state_id, size in expected:
        live_entry = live_by_key.get((state_id, size))
        dev_entry = dev_by_key.get((state_id, size))
        live_outcome = _describe(live_entry)
        dev_outcome = _describe(dev_entry)
        message = f"{live_outcome} on live, {dev_outcome} on dev"
        if live_entry is None or dev_entry is None:
            rows.append((state_id, size, "fail", f"{message} (missing capture)"))
        elif live_outcome == dev_outcome == "captured":
            rows.append((state_id, size, "compare", None))
        elif live_outcome == dev_outcome == "absent":
            rows.append((state_id, size, "not-applicable", None))
        else:
            reasons = []
            for side, item in (("live", live_entry), ("dev", dev_entry)):
                if item["outcome"] == "failed":
                    reasons.append(f"{side}: {item.get('reason') or 'unknown reason'}")
            if reasons:
                message += f" ({'; '.join(reasons)})"
            rows.append((state_id, size, "fail", message))
    return rows
