#!/usr/bin/env python3
"""Tests for the baseline manifest, input checks and the baseline command line."""
import contextlib
import io
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
from image_comparator import ImageComparator  # noqa: E402
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

    def test_user_agent_mismatch_refused(self):
        problems = input_mismatches(MANIFEST, "playwright", ["800x600"], "abc", False, "Bot/1")
        self.assertEqual(problems, ["user agent: baseline default, run Bot/1"])
        self.assertEqual(input_mismatches(MANIFEST, "playwright", ["800x600"], "abc", False, None), [])

    def test_user_agent_mismatch_refused_by_cli(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            write_baseline_manifest(temp_dir, dict(MANIFEST, statesSha256=None))
            run = subprocess.run(
                [sys.executable, "main.py", "--compare-baseline", temp_dir, "http://127.0.0.1:1/",
                 "--engine", "playwright", "--sizes", "800x600", "--user-agent", "Bot/1",
                 "--output-dir", os.path.join(temp_dir, "results")],
                cwd=REPO_ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("user agent: baseline default, run Bot/1", run.stderr)

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
            baseline.capture_baseline(processor, target, "http://a.example/", ["http://a.example/"], None,
                                      cookie_file)
            with open(os.path.join(target, "baseline.json"), encoding="utf-8") as handle:
                text = handle.read()
        self.assertNotIn("s3cret", text)
        self.assertIsNotNone(json.loads(text)["cookiesSha256"])

    def test_failed_state_at_capture_is_reported_and_counted(self):
        states = [
            {"id": "menu", "size": "800x600", "outcome": "captured", "reason": None, "files": []},
            {"id": "menu", "size": "400x600", "outcome": "failed", "reason": "step 1: boom", "files": []},
            {"id": "gone", "size": "800x600", "outcome": "absent", "reason": None, "files": []},
        ]

        def capture_page(**kwargs):
            return "", {}, {"ok": True, "error": None, "status": 200, "files": [], "states": states}

        processor = SimpleNamespace(
            args=SimpleNamespace(engine="playwright", omit_screenshots=True, user_agent=None),
            size_names=lambda: ["800x600"], capture_page=capture_page,
            unique_output_dir=PageDiffProcessor.unique_output_dir,
            sanitize_path=PageDiffProcessor.sanitize_path)
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as temp_dir, contextlib.redirect_stdout(output):
            failures = baseline.capture_baseline(processor, os.path.join(temp_dir, "base"), "http://a.example/",
                                                 ["http://a.example/"], None, None)
        self.assertIn("[FAIL] /: state menu 400x600 failed: step 1: boom", output.getvalue())
        self.assertNotIn("state menu 800x600", output.getvalue())
        self.assertNotIn("state gone", output.getvalue())
        self.assertEqual(failures, 1)


class CompareBaselineTest(unittest.TestCase):
    def compare(self, stored_status, dev_status, manifest_fields=None, cookie_text=None):
        """Compare one stored page with a fake dev capture; return what was printed."""
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as temp_dir:
            baseline_dir = os.path.join(temp_dir, "base")
            stored_dir = os.path.join(baseline_dir, "pages", "index")
            os.makedirs(stored_dir)
            with open(os.path.join(stored_dir, "html_live.html"), "w", encoding="utf-8") as handle:
                handle.write("<p>same</p>")
            page = {"url": "http://a.example/", "dir": "index", "ok": True, "error": None,
                    "status": stored_status, "files": ["html_live.html"], "states": []}
            manifest = dict(MANIFEST, pages={"/": page}, **(manifest_fields or {}))
            cookie_file = None
            if cookie_text is not None:
                cookie_file = os.path.join(temp_dir, "cookies.json")
                with open(cookie_file, "w", encoding="utf-8") as handle:
                    handle.write(cookie_text)
            results = os.path.join(temp_dir, "results")
            os.makedirs(results)
            args = SimpleNamespace(resolutions=[], omit_screenshots=True, threshold=0, always_generate_diff=False)
            processor = PageDiffProcessor(None, ImageComparator(threshold=0), results, args)
            processor.capture_page = lambda **kwargs: (
                "<p>same</p>", {}, {"ok": True, "error": None, "status": dev_status, "files": [], "states": []})
            with contextlib.redirect_stdout(output):
                baseline.compare_baseline(processor, baseline_dir, manifest, "http://b.example/",
                                          ["http://b.example/"], cookie_file)
        return output.getvalue()

    def test_status_is_compared_with_the_stored_page(self):
        output = self.compare(200, 404)
        self.assertIn("[FAIL] /: status 200 on live, 404 on dev", output)
        self.assertNotIn("[PASS]", output)

    def test_different_cookies_warn_once_and_continue(self):
        output = self.compare(200, 200, cookie_text="[]")
        self.assertEqual(output.count("[WARN] cookies differ from the baseline"), 1)
        self.assertIn("[PASS]", output)

    def test_same_status_passes(self):
        output = self.compare(200, 200)
        self.assertIn("[PASS]", output)
        self.assertNotIn("[FAIL]", output)


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

    def make_baseline(self, temp_dir):
        target = os.path.join(temp_dir, "base")
        write_baseline_manifest(target, dict(MANIFEST, statesSha256=None))
        stored = os.path.join(target, "pages", "index", "html_live.html")
        os.makedirs(os.path.dirname(stored))
        with open(stored, "w", encoding="utf-8") as handle:
            handle.write("<p>stored</p>")
        return target, stored

    def write_urls(self, temp_dir, text):
        urls_file = os.path.join(temp_dir, "urls.txt")
        with open(urls_file, "w", encoding="utf-8") as handle:
            handle.write(text)
        return urls_file

    def test_existing_baseline_is_refused_without_remove_previous_results(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target, stored = self.make_baseline(temp_dir)
            run = self.run_main("--capture-baseline", target, "http://127.0.0.1:1/", "--engine", "playwright")
            self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
            self.assertIn("already holds a baseline", run.stderr)
            self.assertIn("--remove-previous_results", run.stderr)
            self.assertTrue(os.path.isfile(stored))

    def test_url_outside_base_is_refused_before_anything_is_deleted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target, stored = self.make_baseline(temp_dir)
            urls_file = self.write_urls(temp_dir, "http://127.0.0.1:1/\nhttp://other.example/x\n")
            run = self.run_main("--capture-baseline", target, "http://127.0.0.1:1/", "--engine", "playwright",
                                "--urls", urls_file, "--remove-previous_results")
            self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
            self.assertIn("http://other.example/x is not below http://127.0.0.1:1/", run.stderr)
            self.assertTrue(os.path.isfile(stored))

    def test_empty_url_file_is_refused_in_both_modes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            urls_file = self.write_urls(temp_dir, "# nothing to check\n\n")
            new_target = os.path.join(temp_dir, "new")
            run = self.run_main("--capture-baseline", new_target, "http://127.0.0.1:1/", "--engine", "playwright",
                                "--urls", urls_file)
            self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
            self.assertIn("lists no URL", run.stderr)
            self.assertFalse(os.path.exists(new_target))

            target, _ = self.make_baseline(temp_dir)
            run = self.run_main("--compare-baseline", target, "http://127.0.0.1:1/", "--engine", "playwright",
                                "--sizes", "800x600", "--urls", urls_file,
                                "--output-dir", os.path.join(temp_dir, "results"))
            self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
            self.assertIn("lists no URL", run.stderr)

    def test_page_key_follows_prefix_rule(self):
        self.assertEqual(baseline.page_key("http://h/", "http://h/"), "/")
        self.assertEqual(baseline.page_key("http://h/a/b", "http://h/"), "/a/b")
        self.assertEqual(baseline.url_for_key("/a/b", "http://h/"), "http://h/a/b")
        self.assertEqual(baseline.url_for_key("/", "http://h/x.html"), "http://h/x.html")


if __name__ == "__main__":
    unittest.main()
