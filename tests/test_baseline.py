#!/usr/bin/env python3
"""Tests for the baseline manifest, input checks and the baseline command line."""
import contextlib
import io
import json
import logging
import os
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import baseline  # noqa: E402
import logging_config  # noqa: E402
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
        with tempfile.TemporaryDirectory() as temp_dir, tempfile.TemporaryDirectory() as output_dir:
            write_baseline_manifest(temp_dir, dict(MANIFEST, statesSha256=None))
            run = subprocess.run(
                [sys.executable, "main.py", "--compare-baseline", temp_dir, "http://127.0.0.1:1/",
                 "--engine", "playwright", "--sizes", "800x600", "--user-agent", "Bot/1",
                 "--output-dir", output_dir],
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


SECRET = "Zq9-synthetic-pw"
CREDENTIAL_BASE = f"http://us%40er:{SECRET}@127.0.0.1:1/"


def files_containing(root, text):
    """Return every file below root whose bytes contain text."""
    found = []
    for folder, _, names in os.walk(root):
        for name in names:
            path = os.path.join(folder, name)
            with open(path, "rb") as handle:
                if text.encode() in handle.read():
                    found.append(path)
    return found


class UrlCredentialsTest(unittest.TestCase):
    def test_split_credentials(self):
        self.assertEqual(baseline.split_credentials(f"https://us%40er:p%3Ass@host:8080/p?q=1#f"),
                         ("https://host:8080/p?q=1#f", ("us@er", "p:ss")))
        self.assertEqual(baseline.split_credentials("http://user@host/"), ("http://host/", ("user", None)))
        self.assertEqual(baseline.split_credentials("http://user:@host/"), ("http://host/", ("user", "")))
        self.assertEqual(baseline.split_credentials("http://host/a@b"), ("http://host/a@b", None))
        self.assertEqual(baseline.split_credentials("http://[::1]:80/"), ("http://[::1]:80/", None))

    def test_run_urls_returns_urls_without_credentials(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            urls_file = os.path.join(temp_dir, "urls.txt")
            with open(urls_file, "w", encoding="utf-8") as handle:
                handle.write(f"http://u:{SECRET}@h.example/a\nhttp://h.example/b\n")
            self.assertEqual(baseline.run_urls("http://h.example/", urls_file),
                             ["http://h.example/a", "http://h.example/b"])


class UrlCredentialsCommandLineTest(unittest.TestCase):
    def run_main(self, *args):
        return subprocess.run([sys.executable, "main.py", "--log-level", "DEBUG", *args], cwd=REPO_ROOT,
                              capture_output=True, text=True, timeout=120)

    def test_failed_capture_leaves_no_password_behind(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = os.path.join(temp_dir, "base")
            results = os.path.join(temp_dir, "results")
            run = self.run_main("--capture-baseline", target, CREDENTIAL_BASE, "--engine", "playwright",
                                "--sizes", "800x600", "--output-dir", results)
            self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
            self.assertIn("[FAIL] /: page failed to load", run.stdout)
            self.assertNotIn(SECRET, run.stdout + run.stderr)
            self.assertEqual(files_containing(temp_dir, SECRET), [])
            with open(os.path.join(target, "baseline.json"), encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["pages"]["/"]["url"], "http://127.0.0.1:1/")

    def test_compare_leaves_no_password_behind(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = os.path.join(temp_dir, "base")
            stored_dir = os.path.join(target, "pages", "index")
            os.makedirs(stored_dir)
            with open(os.path.join(stored_dir, "html_live.html"), "w", encoding="utf-8") as handle:
                handle.write("<p>stored</p>")
            page = {"url": "http://127.0.0.1:1/", "dir": "index", "ok": True, "error": None,
                    "status": 200, "files": ["html_live.html"], "states": []}
            write_baseline_manifest(target, dict(MANIFEST, statesSha256=None, omitScreenshots=True,
                                                 pages={"/": page}))
            results = os.path.join(temp_dir, "results")
            run = self.run_main("--compare-baseline", target, CREDENTIAL_BASE, "--engine", "playwright",
                                "--sizes", "800x600", "--omit-screenshots", "--output-dir", results)
            self.assertNotIn(SECRET, run.stdout + run.stderr)
            self.assertNotIn("Traceback", run.stderr)
            self.assertEqual(files_containing(temp_dir, SECRET), [])

    def test_credentials_in_both_urls_do_not_reach_the_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            results = os.path.join(temp_dir, "results")
            run = self.run_main(CREDENTIAL_BASE, CREDENTIAL_BASE, "--engine", "playwright",
                                "--sizes", "800x600", "--omit-screenshots", "--output-dir", results)
            self.assertNotIn(SECRET, run.stdout + run.stderr)
            self.assertEqual(files_containing(temp_dir, SECRET), [])

    def test_conflicting_credentials_are_refused(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            results = os.path.join(temp_dir, "results")
            cases = (
                [CREDENTIAL_BASE, "http://127.0.0.1:1/", "--htuser", "someone", "--htpass", "else"],
                [CREDENTIAL_BASE, "http://127.0.0.1:1/", "--htuser", "us@er", "--htpass", "different"],
            )
            for case in cases:
                run = self.run_main(*case, "--engine", "playwright", "--output-dir", results)
                self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
                self.assertIn("different credentials", run.stderr)
                self.assertNotIn(SECRET, run.stdout + run.stderr)

    def test_conflicting_credentials_in_the_url_file_are_refused(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            urls_file = os.path.join(temp_dir, "urls.txt")
            with open(urls_file, "w", encoding="utf-8") as handle:
                handle.write("http://other:x@127.0.0.1:1/a\n")
            run = self.run_main("--capture-baseline", os.path.join(temp_dir, "base"), CREDENTIAL_BASE,
                                "--urls", urls_file, "--engine", "playwright")
            self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
            self.assertIn("different credentials", run.stderr)
            self.assertNotIn(SECRET, run.stdout + run.stderr)


class SideCredentialsTest(unittest.TestCase):
    def test_user_only_url_combines_with_htpass(self):
        self.assertEqual(baseline.resolve_credentials(("u", None), None, "x"), ("u", "x"))
        self.assertEqual(baseline.resolve_credentials(("u", None), "u", "x"), ("u", "x"))
        self.assertIsNone(baseline.resolve_credentials(("u", None), None, None))

    def test_different_value_is_a_conflict(self):
        with self.assertRaises(baseline.BaselineError):
            baseline.resolve_credentials(("u", None), "other", "x")
        with self.assertRaises(baseline.BaselineError):
            baseline.resolve_credentials(("u", "p"), "u", "x")

    def test_htuser_pair_without_url_credentials(self):
        self.assertEqual(baseline.resolve_credentials(None, "h", "w"), ("h", "w"))
        self.assertIsNone(baseline.resolve_credentials(None, "h", None))

    def test_user_only_url_with_htpass_is_accepted_by_cli(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run = subprocess.run(
                [sys.executable, "main.py", "http://u@127.0.0.1:1/", "http://127.0.0.1:1/", "--htpass", "x",
                 "--engine", "playwright", "--states", "missing.json", "--output-dir", temp_dir],
                cwd=REPO_ROOT, capture_output=True, text=True, timeout=60)
        self.assertNotIn("different credentials", run.stderr)

    def make_processor(self, side_auth, htuser=None, htpass=None):
        calls = []

        class FakeEngine:
            def capture(self, **kwargs):
                calls.append(kwargs)
                return {"ok": False, "error": "stop", "files": [], "states": []}

        class FakeDriver:
            def get(self, url):
                calls.append({"get": url})
                raise RuntimeError("stop")

        args = SimpleNamespace(resolutions=[(800, 600)], omit_screenshots=True, user_agent=None,
                               htuser=htuser, htpass=htpass, side_auth=side_auth)
        site = SimpleNamespace(driver=FakeDriver())
        engine_processor = PageDiffProcessor(None, ImageComparator(threshold=0), "out", args, FakeEngine(), [])
        selenium_processor = PageDiffProcessor(site, ImageComparator(threshold=0), "out", args)
        return engine_processor, selenium_processor, calls

    def test_credentials_apply_only_to_their_own_side(self):
        side_auth = {"live": ("lu", "lp"), "dev": None}
        engine_processor, selenium_processor, calls = self.make_processor(side_auth)
        with tempfile.TemporaryDirectory() as temp_dir:
            engine_processor.capture_page("http://l/", "live", temp_dir)
            engine_processor.capture_page("http://d/", "dev", temp_dir)
            self.assertEqual(calls[0]["http_credentials"], {"username": "lu", "password": "lp"})
            self.assertIsNone(calls[1]["http_credentials"])
            calls.clear()
            for url, label in (("http://l/", "live"), ("http://d/", "dev")):
                with self.assertRaises(RuntimeError):
                    selenium_processor.capture_page(url, label, temp_dir)
            self.assertEqual(calls[0]["get"], "http://lu:lp@l/")
            self.assertEqual(calls[1]["get"], "http://d/")


class LogRedactionTest(unittest.TestCase):
    def emit(self, message, *args):
        buffer = io.StringIO()
        handler = logging.StreamHandler(buffer)
        handler.addFilter(logging_config.RedactCredentials())
        logger = logging.getLogger("redaction-test")
        logger.propagate = False
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        try:
            logger.debug(message, *args)
        finally:
            logger.removeHandler(handler)
        return buffer.getvalue()

    def test_message_and_args_are_redacted(self):
        out = self.emit("go to http://us:%s@h.example/p", SECRET)
        self.assertNotIn(SECRET, out)
        self.assertNotIn("us:", out)
        self.assertIn("http://***@h.example/p", out)
        out = self.emit("%s %s %s", "POST", "https://u:p%40ss@h/x", {"url": f"https://user@h:8080/{SECRET[:3]}"})
        self.assertNotIn("p%40ss", out)
        self.assertIn("https://***@h/x", out)
        self.assertIn("https://***@h:8080/", out)
        self.assertEqual(self.emit("plain http://h/a@b"), "plain http://h/a@b\n")

    def test_unencoded_at_sign_in_password_is_redacted(self):
        out = self.emit("go to http://us:pa@%s@h.example/p", SECRET)
        self.assertNotIn(SECRET, out)
        self.assertIn("http://***@h.example/p", out)

    def test_exception_text_is_redacted(self):
        buffer = io.StringIO()
        handler = logging.StreamHandler(buffer)
        handler.addFilter(logging_config.RedactCredentials())
        logger = logging.getLogger("redaction-exc-test")
        logger.propagate = False
        logger.addHandler(handler)
        try:
            try:
                raise RuntimeError(f"navigation to http://us:{SECRET}@h.example/ failed")
            except RuntimeError:
                logger.exception("capture failed")
            logger.error("stack", stack_info=True, extra={})
        finally:
            logger.removeHandler(handler)
        self.assertNotIn(SECRET, buffer.getvalue())
        self.assertIn("http://***@h.example/", buffer.getvalue())

    def test_setup_logging_filters_every_root_handler(self):
        root = logging.getLogger()
        saved, saved_level = root.handlers[:], root.level
        buffer = io.StringIO()
        root.handlers = [logging.StreamHandler(buffer), logging.StreamHandler(buffer)]
        try:
            logging_config.setup_logging("DEBUG")
            root.setLevel(logging.DEBUG)
            logging.getLogger("selenium.test").debug("%s %s", "POST", f"{{'url': 'http://u:{SECRET}@h/'}}")
        finally:
            root.handlers = saved
            root.setLevel(saved_level)
        self.assertNotIn(SECRET, buffer.getvalue())
        self.assertIn("http://***@h/", buffer.getvalue())


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

    def test_output_dir_overlapping_the_baseline_is_refused_without_deleting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target, stored = self.make_baseline(temp_dir)
            inside = os.path.join(target, "pages")
            outside = temp_dir
            for output_dir in (target, inside, outside, os.path.join(target, "..", "base")):
                run = self.run_main("--compare-baseline", target, "http://127.0.0.1:1/", "--engine", "playwright",
                                    "--sizes", "800x600", "--output-dir", output_dir, "--remove-previous_results")
                self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
                self.assertIn("overlaps the baseline directory", run.stderr)
                self.assertTrue(os.path.isfile(stored), output_dir)

    def test_capture_target_overlapping_the_output_dir_is_refused(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target, stored = self.make_baseline(temp_dir)
            run = self.run_main("--capture-baseline", target, "http://127.0.0.1:1/", "--engine", "playwright",
                                "--output-dir", temp_dir, "--remove-previous_results")
            self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
            self.assertIn("overlaps the baseline directory", run.stderr)
            self.assertTrue(os.path.isfile(stored))

    def test_page_key_follows_prefix_rule(self):
        self.assertEqual(baseline.page_key("http://h/", "http://h/"), "/")
        self.assertEqual(baseline.page_key("http://h/a/b", "http://h/"), "/a/b")
        self.assertEqual(baseline.url_for_key("/a/b", "http://h/"), "http://h/a/b")
        self.assertEqual(baseline.url_for_key("/", "http://h/x.html"), "http://h/x.html")


if __name__ == "__main__":
    unittest.main()
