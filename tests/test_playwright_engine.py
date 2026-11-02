#!/usr/bin/env python3
"""Tests for the Playwright engine wrapper and the state comparison table."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import playwright_engine  # noqa: E402
from playwright_engine import (  # noqa: E402
    EngineUnavailable, PlaywrightEngine, compare_state_outcomes, expected_state_entries,
    to_playwright_cookies,
)
from states import State, Step  # noqa: E402


def entry(state_id, size, outcome, reason=None):
    return {"id": state_id, "size": size, "outcome": outcome, "reason": reason, "files": []}


class EngineReadinessTest(unittest.TestCase):
    def test_engine_reports_old_node(self):
        done = subprocess.CompletedProcess(["node", "--version"], 0, stdout="v18.19.0\n", stderr="")
        with mock.patch.object(playwright_engine.subprocess, "run", return_value=done):
            with self.assertRaises(EngineUnavailable) as caught:
                PlaywrightEngine(REPO_ROOT).ensure_ready()
        self.assertIn("Node 20 or newer", str(caught.exception))
        self.assertIn("v18.19.0", str(caught.exception))

    def test_engine_reports_missing_node(self):
        with mock.patch.object(playwright_engine.subprocess, "run", side_effect=FileNotFoundError):
            with self.assertRaises(EngineUnavailable) as caught:
                PlaywrightEngine(REPO_ROOT).ensure_ready()
        self.assertIn("Node 20 or newer", str(caught.exception))

    def test_failed_npm_ci_reports_last_stderr_line(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            if command[0] == "node":
                return subprocess.CompletedProcess(command, 0, stdout="v22.1.0\n", stderr="")
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="first\nnpm ERR! offline\n")

        with tempfile.TemporaryDirectory() as tool_dir:
            with mock.patch.object(playwright_engine.subprocess, "run", side_effect=fake_run):
                with self.assertRaises(EngineUnavailable) as caught:
                    PlaywrightEngine(tool_dir).ensure_ready()
        self.assertIn("npm ERR! offline", str(caught.exception))
        self.assertEqual(calls[1][:2], ["npm", "ci"])


class CommandLineTest(unittest.TestCase):
    def run_main(self, *args):
        return subprocess.run(
            [sys.executable, "main.py", *args], cwd=REPO_ROOT,
            capture_output=True, text=True, timeout=60,
        )

    def test_states_requires_playwright_engine(self):
        run = self.run_main("http://a.example", "http://b.example", "--states", "s.json")
        self.assertEqual(run.returncode, 2)
        self.assertIn("--states needs --engine playwright", run.stderr)

    def test_list_capabilities(self):
        run = self.run_main("--list-capabilities")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout), {"engines": ["selenium", "playwright"], "states": 1})

    def test_urls_are_required_without_list_capabilities(self):
        run = self.run_main()
        self.assertEqual(run.returncode, 2)


class CompareOutcomesTest(unittest.TestCase):
    def verdicts(self, expected, live, dev):
        return {(i, s): (v, m) for i, s, v, m in compare_state_outcomes(expected, live, dev)}

    def test_compare_outcomes_table(self):
        expected = [("a", "800x600"), ("b", "800x600"), ("c", "800x600"), ("d", "800x600"), ("e", "800x600")]
        live = [entry("a", "800x600", "captured"), entry("b", "800x600", "absent"),
                entry("c", "800x600", "failed", "step 1: boom"), entry("d", "800x600", "absent"),
                entry("e", "800x600", "captured")]
        dev = [entry("a", "800x600", "captured"), entry("b", "800x600", "absent"),
               entry("c", "800x600", "failed", "step 1: bang"), entry("d", "800x600", "captured"),
               entry("e", "800x600", "failed", "step 2: gone")]
        result = self.verdicts(expected, live, dev)
        self.assertEqual(result[("a", "800x600")], ("compare", None))
        self.assertEqual(result[("b", "800x600")], ("not-applicable", None))
        self.assertEqual(result[("c", "800x600")][0], "fail")
        self.assertIn("failed on live, failed on dev", result[("c", "800x600")][1])
        self.assertIn("step 1: boom", result[("c", "800x600")][1])
        self.assertEqual(result[("d", "800x600")][0], "fail")
        self.assertIn("absent on live, captured on dev", result[("d", "800x600")][1])
        self.assertEqual(result[("e", "800x600")][0], "fail")
        self.assertIn("captured on live, failed on dev", result[("e", "800x600")][1])

    def test_size_missing_on_dev_fails(self):
        expected = [("a", "400x600"), ("a", "1200x800")]
        live = [entry("a", "400x600", "captured"), entry("a", "1200x800", "captured")]
        dev = [entry("a", "1200x800", "captured")]
        result = self.verdicts(expected, live, dev)
        verdict, message = result[("a", "400x600")]
        self.assertEqual(verdict, "fail")
        self.assertIn("missing capture", message)
        self.assertEqual(result[("a", "1200x800")][0], "compare")

    def test_two_crashed_sides_do_not_pass(self):
        result = self.verdicts([("a", "800x600")], [], [])
        self.assertEqual(result[("a", "800x600")][0], "fail")
        self.assertIn("missing capture", result[("a", "800x600")][1])

    def test_order_follows_expected_entries(self):
        expected = [("b", "800x600"), ("a", "800x600")]
        rows = compare_state_outcomes(expected, [], [])
        self.assertEqual([(r[0], r[1]) for r in rows], expected)


class ExpectedEntriesTest(unittest.TestCase):
    STEP = (Step(action="click", selector="#x"),)

    def test_state_sizes_or_run_sizes_without_fullscreen(self):
        states = [
            State(id="own", when=None, steps=self.STEP, sizes=("400x600",)),
            State(id="null", when=None, steps=self.STEP, sizes=None),
            State(id="empty", when=None, steps=self.STEP, sizes=()),
        ]
        entries = expected_state_entries(states, ["800x600", "fullscreen", "1200x800"])
        self.assertEqual(entries, [
            ("own", "400x600"),
            ("null", "800x600"), ("null", "1200x800"),
            ("empty", "800x600"), ("empty", "1200x800"),
        ])

    def test_duplicate_sizes_collapse(self):
        states = [State(id="a", when=None, steps=self.STEP, sizes=None)]
        self.assertEqual(expected_state_entries(states, ["800x600", "800x600"]), [("a", "800x600")])


class CookieConversionTest(unittest.TestCase):
    def test_domain_defaults_to_page_host_without_port(self):
        cookies = to_playwright_cookies([{"name": "a", "value": "1"}], "https://dev.example.com:8443/x")
        self.assertEqual(cookies, [{"name": "a", "value": "1", "domain": "dev.example.com", "path": "/"}])

    def test_selenium_fields_are_mapped(self):
        raw = [{"name": "a", "value": "1", "domain": ".example.com", "path": "/p", "expiry": 1900000000,
                "secure": True, "httpOnly": True, "sameSite": "Lax"}]
        self.assertEqual(to_playwright_cookies(raw, "https://example.com/"), [{
            "name": "a", "value": "1", "domain": ".example.com", "path": "/p", "expires": 1900000000,
            "secure": True, "httpOnly": True, "sameSite": "Lax",
        }])

    def test_unknown_same_site_is_dropped(self):
        raw = [{"name": "a", "value": "1", "sameSite": "bogus"}]
        self.assertNotIn("sameSite", to_playwright_cookies(raw, "https://example.com/")[0])


if __name__ == "__main__":
    unittest.main()
