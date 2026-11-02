#!/usr/bin/env python3
"""Tests for the Playwright engine wrapper and the state comparison table."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import playwright_engine  # noqa: E402
from playwright_engine import (  # noqa: E402
    EngineUnavailable, PlaywrightEngine, compare_state_outcomes, expected_state_entries,
    states_without_widths, to_playwright_cookies,
)
from image_comparator import ImageComparator  # noqa: E402
from main import PageDiffProcessor, git_diff  # noqa: E402
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
    def run_main(self, *args, env=None):
        return subprocess.run(
            [sys.executable, "main.py", *args], cwd=REPO_ROOT,
            capture_output=True, text=True, timeout=60, env=env,
        )

    def test_missing_node_exits_2(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            # An empty PATH directory hides node on every platform.
            env = dict(os.environ, PATH=temp_dir)
            run = self.run_main("http://127.0.0.1:1/", "http://127.0.0.1:1/", "--engine", "playwright",
                                "--output-dir", os.path.join(temp_dir, "results"), env=env)
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("Node 20 or newer is required", run.stderr)

    def test_states_requires_playwright_engine(self):
        run = self.run_main("http://a.example", "http://b.example", "--states", "s.json")
        self.assertEqual(run.returncode, 2)
        self.assertIn("--states needs --engine playwright", run.stderr)

    def test_list_capabilities(self):
        run = self.run_main("--list-capabilities")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout), {"engines": ["selenium", "playwright"], "states": 1, "baseline": 1})

    def test_urls_are_required_without_list_capabilities(self):
        run = self.run_main()
        self.assertEqual(run.returncode, 2)

    def test_state_without_width_is_refused(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            states_file = os.path.join(temp_dir, "states.json")
            with open(states_file, "w", encoding="utf-8") as handle:
                json.dump({"version": 1, "states": [
                    {"id": "menu", "steps": [{"action": "click", "selector": "#m"}]},
                    {"id": "own", "sizes": ["400x600"], "steps": [{"action": "click", "selector": "#m"}]},
                ]}, handle)
            run = self.run_main("http://127.0.0.1:1/", "http://127.0.0.1:1/", "--engine", "playwright",
                                "--states", states_file, "--sizes", "fullscreen",
                                "--output-dir", os.path.join(temp_dir, "results"))
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("state menu has no width", run.stderr)
        self.assertNotIn("menu, own", run.stderr)

    def test_recursive_is_refused_in_baseline_modes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = os.path.join(temp_dir, "base")
            for flags in (["--recursive"], ["--recursive-max-level", "2"]):
                run = self.run_main("--capture-baseline", target, "http://127.0.0.1:1/", "--engine", "playwright",
                                    *flags)
                self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
                self.assertIn("--recursive", run.stderr)
                self.assertFalse(os.path.exists(target))


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


def make_processor(output_dir, states=None, omit_screenshots=True, resolutions=()):
    args = SimpleNamespace(resolutions=list(resolutions), omit_screenshots=omit_screenshots, threshold=0,
                           always_generate_diff=False)
    return PageDiffProcessor(None, ImageComparator(threshold=0), output_dir, args, states=states)


class StatusCompareTest(unittest.TestCase):
    def compare(self, live_info, dev_info):
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as temp_dir, contextlib.redirect_stdout(output):
            make_processor(temp_dir).compare_captures(
                "http://live/x", "http://dev/x", temp_dir, ("<p>same</p>", {}, live_info),
                ("<p>same</p>", {}, dev_info), "/x")
        return output.getvalue()

    @staticmethod
    def info(status):
        return {"ok": True, "error": None, "status": status, "states": []}

    def test_different_status_fails(self):
        output = self.compare(self.info(200), self.info(404))
        self.assertIn("[FAIL] /x: status 200 on live, 404 on dev", output)
        self.assertNotIn("[PASS]", output)

    def test_same_error_status_warns_and_compares(self):
        output = self.compare(self.info(404), self.info(404))
        self.assertIn("[WARN] /x: status 404 on both sides", output)
        self.assertIn("[PASS]", output)

    def test_same_success_status_passes_without_warning(self):
        output = self.compare(self.info(200), self.info(200))
        self.assertNotIn("[WARN]", output)
        self.assertIn("[PASS]", output)

    def test_capture_without_status_passes(self):
        # The Selenium engine reports no status.
        selenium_info = {"ok": True, "error": None, "states": []}
        output = self.compare(selenium_info, selenium_info)
        self.assertNotIn("status", output)
        self.assertIn("[PASS]", output)


class BaseHtmlDiffTest(unittest.TestCase):
    def test_git_error_fails_the_page(self):
        info = {"ok": True, "error": None, "status": 200, "states": []}
        broken_git = subprocess.CompletedProcess(["git"], 128, stdout="", stderr="fatal: broken\n")
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as temp_dir, contextlib.redirect_stdout(output), \
                mock.patch("main.subprocess.run", return_value=broken_git):
            make_processor(temp_dir).compare_captures(
                "http://live/x", "http://dev/x", temp_dir, ("<p>a</p>", {}, info), ("<p>a</p>", {}, info), "/x")
        self.assertIn("HTML git diff failed: fatal: broken", output.getvalue())
        self.assertNotIn("[PASS]", output.getvalue())


class StateFileCheckTest(unittest.TestCase):
    STATE = State(id="a", when=None, steps=(Step(action="click", selector="#x"),), sizes=None)
    CAPTURED = {"states": [entry("a", "800x600", "captured")]}

    def compare_states(self, temp_dir, present, omit_screenshots):
        for name in present:
            with open(os.path.join(temp_dir, name), "w", encoding="utf-8") as handle:
                handle.write("<p>same</p>")
        processor = make_processor(temp_dir, [self.STATE], omit_screenshots, [(800, 600)])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            _, failures = processor.compare_states(self.CAPTURED, self.CAPTURED, temp_dir)
        return output.getvalue(), failures

    def test_missing_state_html_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output, failures = self.compare_states(temp_dir, ["state-a_800x600_html_live.html"], True)
        self.assertIn("[FAIL] state a 800x600: missing file state-a_800x600_html_dev.html", output)
        self.assertEqual(failures, 1)

    def test_missing_state_screenshot_fails(self):
        present = ["state-a_800x600_html_live.html", "state-a_800x600_html_dev.html", "state-a_800x600_live.png"]
        with tempfile.TemporaryDirectory() as temp_dir:
            output, failures = self.compare_states(temp_dir, present, False)
        self.assertIn("[FAIL] state a 800x600: missing file state-a_800x600_dev.png", output)
        self.assertEqual(failures, 1)


class GitDiffTest(unittest.TestCase):
    def test_unreadable_file_is_an_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            present = os.path.join(temp_dir, "a.html")
            with open(present, "w", encoding="utf-8") as handle:
                handle.write("<p>a</p>")
            text, error = git_diff(present, os.path.join(temp_dir, "missing.html"))
        self.assertEqual(text, "")
        self.assertIn("git diff failed", error)

    def test_line_ending_settings_do_not_cause_an_error(self):
        # Git for Windows sets core.autocrlf=true, which makes git warn about LF files.
        windows_git = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.autocrlf", "GIT_CONFIG_VALUE_0": "true"}
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(os.environ, windows_git):
            names = []
            for name in ("a.html", "b.html"):
                names.append(os.path.join(temp_dir, name))
                with open(names[-1], "w", encoding="utf-8", newline="\n") as handle:
                    handle.write("<p>same</p>\n")
            self.assertEqual(git_diff(*names), ("", None))

    def test_difference_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            names = []
            for name, body in (("a.html", "<p>a</p>\n"), ("b.html", "<p>b</p>\n")):
                names.append(os.path.join(temp_dir, name))
                with open(names[-1], "w", encoding="utf-8") as handle:
                    handle.write(body)
            text, error = git_diff(*names)
        self.assertIsNone(error)
        self.assertIn("+<p>b</p>", text)


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

    def test_states_without_widths(self):
        states = [
            State(id="own", when=None, steps=self.STEP, sizes=("400x600",)),
            State(id="run", when=None, steps=self.STEP, sizes=None),
        ]
        self.assertEqual(states_without_widths(states, ["fullscreen"]), ["run"])
        self.assertEqual(states_without_widths(states, ["fullscreen", "800x600"]), [])

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
