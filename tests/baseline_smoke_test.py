#!/usr/bin/env python3
"""
End-to-end test for --capture-baseline and --compare-baseline.

One local fixture server plays the site. Its page is replaced between the
capture and the comparison, like an upgrade would change it. Run with no arguments:

    python tests/baseline_smoke_test.py
"""
import functools
import http.server
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "fixtures", "states")
RESULTS = os.path.join(REPO_ROOT, "results", "baseline")
STATES_FILE = os.path.join(FIXTURES, "states.json")
RUN_TIMEOUT = 300


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class BaselineSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(REPO_ROOT, "playwright_capture", "node_modules", "playwright")):
            raise RuntimeError('run "npm ci" in playwright_capture first')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.site = os.path.join(self.temp.name, "site")
        os.makedirs(self.site)
        self.serve_fixture("live")
        handler = functools.partial(QuietHandler, directory=self.site)
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}/"
        self.baseline_dir = os.path.join(self.temp.name, "baseline")

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.temp.cleanup()

    def serve_fixture(self, fixture):
        shutil.copy(os.path.join(FIXTURES, fixture, "index.html"), os.path.join(self.site, "index.html"))

    def run_main(self, name, *args):
        command = [sys.executable, "main.py", "--engine", "playwright", "--states", STATES_FILE,
                   "--sizes", "800x600", *args]
        if "--compare-baseline" in args:
            command += ["--output-dir", os.path.join(RESULTS, name), "--remove-previous_results"]
        run = subprocess.run(command, cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace", timeout=RUN_TIMEOUT)
        print(f"\n$ {' '.join(command)}\n{run.stdout}", flush=True)
        return run

    def capture(self, *extra):
        run = self.run_main("capture", "--capture-baseline", self.baseline_dir, self.base, *extra)
        self.assertEqual(run.returncode, 0, run.stdout)

    def test_unchanged_site_passes(self):
        self.capture()
        self.assertTrue(os.path.isfile(os.path.join(self.baseline_dir, "baseline.json")))
        run = self.run_main("same", "--compare-baseline", self.baseline_dir, self.base)
        self.assertEqual(run.returncode, 0)
        self.assertIn("[PASS]", run.stdout)
        self.assertNotIn("[FAIL]", run.stdout)

    def test_recapture_needs_remove_previous_results(self):
        self.capture()
        run = self.run_main("recapture", "--capture-baseline", self.baseline_dir, self.base)
        self.assertEqual(run.returncode, 2)
        self.assertIn("already holds a baseline", run.stdout)
        self.capture("--remove-previous_results")
        self.assertTrue(os.path.isfile(os.path.join(self.baseline_dir, "baseline.json")))

    def write_urls(self, *pages):
        urls_file = os.path.join(self.temp.name, "urls.txt")
        with open(urls_file, "w", encoding="utf-8") as handle:
            for page in pages:
                handle.write(f"{self.base}{page}\n")
        return urls_file

    def test_status_change_fails(self):
        urls_file = self.write_urls("index.html")
        self.capture("--urls", urls_file)
        os.remove(os.path.join(self.site, "index.html"))
        run = self.run_main("status", "--compare-baseline", self.baseline_dir, self.base, "--urls", urls_file)
        self.assertIn("[FAIL] /index.html: status 200 on live, 404 on dev", run.stdout)
        self.assertNotIn("[PASS]", run.stdout)

    def test_same_error_status_warns(self):
        urls_file = self.write_urls("missing.html")
        self.capture("--urls", urls_file)
        run = self.run_main("missing", "--compare-baseline", self.baseline_dir, self.base, "--urls", urls_file)
        self.assertIn("[WARN] /missing.html: status 404 on both sides", run.stdout)
        self.assertIn("[PASS]", run.stdout)

    def test_changed_state_fails(self):
        self.capture()
        self.serve_fixture("dev-broken")
        run = self.run_main("broken", "--compare-baseline", self.baseline_dir, self.base)
        self.assertIn("[FAIL] state box-open 800x600: captured on live, failed on dev", run.stdout)

    def test_extra_url_is_missing_in_baseline(self):
        self.capture()
        urls_file = os.path.join(self.temp.name, "urls.txt")
        with open(urls_file, "w", encoding="utf-8") as handle:
            handle.write(f"# run list\n{self.base}\n{self.base}index.html\n")
        run = self.run_main("extra", "--compare-baseline", self.baseline_dir, self.base, "--urls", urls_file)
        self.assertIn("[FAIL] /index.html: missing in baseline", run.stdout)
        self.assertIn("[PASS]", run.stdout)

    def test_baseline_url_not_in_run_is_missing_in_run(self):
        urls_file = os.path.join(self.temp.name, "urls.txt")
        with open(urls_file, "w", encoding="utf-8") as handle:
            handle.write(f"{self.base}\n{self.base}index.html\n")
        self.capture("--urls", urls_file)
        run = self.run_main("short", "--compare-baseline", self.baseline_dir, self.base)
        self.assertIn("[FAIL] /index.html: missing in run", run.stdout)

    def test_failed_state_fails_the_capture(self):
        self.serve_fixture("dev-broken")
        run = self.run_main("capture-broken", "--capture-baseline", self.baseline_dir, self.base)
        self.assertEqual(run.returncode, 1)
        self.assertIn("[FAIL] /: state box-open 800x600 failed: step 2:", run.stdout)
        self.assertTrue(os.path.isfile(os.path.join(self.baseline_dir, "baseline.json")))

    def test_baseline_page_that_failed_to_load_is_a_failure(self):
        # Port 1 refuses connections, so the stored page records a failed load.
        run = self.run_main("unreachable", "--capture-baseline", self.baseline_dir, "http://127.0.0.1:1/")
        self.assertEqual(run.returncode, 1)
        self.assertIn("[FAIL] /: page failed to load", run.stdout)
        run = self.run_main("failed", "--compare-baseline", self.baseline_dir, "http://127.0.0.1:1/")
        self.assertIn("[FAIL] /: baseline page failed to load", run.stdout)
        self.assertNotIn("[PASS]", run.stdout)


if __name__ == "__main__":
    unittest.main()
