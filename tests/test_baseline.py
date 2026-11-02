#!/usr/bin/env python3
"""Tests for the baseline manifest, input checks and the baseline command line."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import baseline  # noqa: E402
from baseline import input_mismatches, read_baseline_manifest, write_baseline_manifest  # noqa: E402
from main import PageDiffProcessor  # noqa: E402

MANIFEST = {
    "version": 1, "baseUrl": "http://a.example", "engine": "playwright", "sizes": ["800x600"],
    "statesSha256": "abc", "omitScreenshots": False, "cookiesSha256": None, "userAgent": None,
    "createdAt": "2026-11-02T00:00:00+00:00", "pages": {},
}


class ManifestTest(unittest.TestCase):
    def test_manifest_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = os.path.join(temp_dir, "base")
            write_baseline_manifest(target, MANIFEST)
            self.assertEqual(read_baseline_manifest(target), MANIFEST)

    def test_unreadable_manifest_is_reported(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(baseline.BaselineError):
                read_baseline_manifest(temp_dir)

    def test_baseline_input_mismatch_refused(self):
        problems = input_mismatches(MANIFEST, "playwright", ["400x600"], "other")
        self.assertEqual(len(problems), 2)
        self.assertTrue(problems[0].startswith("sizes"))
        self.assertTrue(problems[1].startswith("states"))
        self.assertEqual(input_mismatches(MANIFEST, "playwright", ["800x600"], "abc"), [])

    def test_screenshot_mode_mismatch_refused(self):
        problems = input_mismatches(MANIFEST, "playwright", ["800x600"], "abc", True)
        self.assertEqual(len(problems), 1)
        self.assertTrue(problems[0].startswith("screenshots"))
        self.assertEqual(input_mismatches(MANIFEST, "playwright", ["800x600"], "abc", False), [])

    def test_screenshot_mode_mismatch_refused_by_cli(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            write_baseline_manifest(temp_dir, dict(MANIFEST, statesSha256=None))
            run = subprocess.run(
                [sys.executable, "main.py", "--compare-baseline", temp_dir, "http://a.example",
                 "--engine", "playwright", "--sizes", "800x600", "--omit-screenshots"],
                cwd=REPO_ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 2)
        self.assertIn("screenshots", run.stderr)

    def test_baseline_input_mismatch_refused_by_cli(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            write_baseline_manifest(temp_dir, MANIFEST)
            states_file = os.path.join(temp_dir, "states.json")
            with open(states_file, "w", encoding="utf-8") as handle:
                json.dump({"version": 1, "states": []}, handle)
            run = subprocess.run(
                [sys.executable, "main.py", "--compare-baseline", temp_dir, "http://a.example",
                 "--engine", "playwright", "--sizes", "400x600", "--states", states_file],
                cwd=REPO_ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 2)
        self.assertIn("sizes", run.stderr)
        self.assertIn("states", run.stderr)

    def test_manifest_has_no_secret_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cookie_file = os.path.join(temp_dir, "cookies.json")
            with open(cookie_file, "w", encoding="utf-8") as handle:
                json.dump([{"name": "session", "value": "s3cret"}], handle)
            args = SimpleNamespace(engine="playwright", omit_screenshots=True, user_agent=None)

            def capture_page(**kwargs):
                return "", {}, {"ok": True, "error": None, "status": 200, "files": [], "states": []}

            processor = SimpleNamespace(
                args=args, size_names=lambda: ["800x600"], capture_page=capture_page,
                unique_output_dir=PageDiffProcessor.unique_output_dir,
                sanitize_path=PageDiffProcessor.sanitize_path)
            target = os.path.join(temp_dir, "base")
            baseline.capture_baseline(processor, target, "http://a.example/", None, None, cookie_file)
            with open(os.path.join(target, "baseline.json"), encoding="utf-8") as handle:
                text = handle.read()
        self.assertNotIn("s3cret", text)
        self.assertIsNotNone(json.loads(text)["cookiesSha256"])


class CommandLineTest(unittest.TestCase):
    def run_main(self, *args):
        return subprocess.run([sys.executable, "main.py", *args], cwd=REPO_ROOT,
                              capture_output=True, text=True, timeout=60)

    def test_baseline_needs_playwright_engine(self):
        run = self.run_main("--capture-baseline", "x", "http://a.example")
        self.assertEqual(run.returncode, 2)
        self.assertIn("--engine playwright", run.stderr)

    def test_baseline_needs_exactly_one_url(self):
        run = self.run_main("--capture-baseline", "x", "--engine", "playwright")
        self.assertEqual(run.returncode, 2)
        run = self.run_main("--capture-baseline", "x", "--engine", "playwright", "http://a", "http://b")
        self.assertEqual(run.returncode, 2)

    def test_page_key_follows_prefix_rule(self):
        self.assertEqual(baseline.page_key("http://h/", "http://h/"), "/")
        self.assertEqual(baseline.page_key("http://h/a/b", "http://h/"), "/a/b")
        self.assertEqual(baseline.url_for_key("/a/b", "http://h/"), "http://h/a/b")
        self.assertEqual(baseline.url_for_key("/", "http://h/x.html"), "http://h/x.html")


if __name__ == "__main__":
    unittest.main()
