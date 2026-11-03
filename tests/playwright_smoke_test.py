#!/usr/bin/env python3
"""
End-to-end test for ``--engine playwright`` with interaction states.

Two local fixture servers act as live and dev, and main.py runs against them
like a user would. Run it with no arguments:

    python tests/playwright_smoke_test.py
"""
import functools
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "fixtures", "states")
STATES_FILE = os.path.join(FIXTURES, "states.json")
WIDTH_FIXTURES = os.path.join(HERE, "fixtures", "widths")
RESULTS = os.path.join(REPO_ROOT, "results", "playwright")
RUN_TIMEOUT = 300


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def serve(directory):
    handler = functools.partial(QuietHandler, directory=directory)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}/index.html"


class PlaywrightSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(REPO_ROOT, "playwright_capture", "node_modules", "playwright")):
            raise RuntimeError('run "npm ci" in playwright_capture first')

    def setUp(self):
        self.servers = []

    def tearDown(self):
        for httpd in self.servers:
            httpd.shutdown()
            httpd.server_close()

    def url(self, fixture, fixtures=FIXTURES):
        httpd, url = serve(os.path.join(fixtures, fixture))
        self.servers.append(httpd)
        return url

    def run_main(self, name, live_fixture, dev_fixture, sizes="800x600", states_file=STATES_FILE,
                 fixtures=FIXTURES):
        output_dir = os.path.join(RESULTS, name)
        command = [
            sys.executable, "main.py", self.url(live_fixture, fixtures), self.url(dev_fixture, fixtures),
            "--engine", "playwright", "--output-dir", output_dir, "--remove-previous_results",
            "--sizes", sizes,
        ]
        if states_file:
            command += ["--states", states_file]
        run = subprocess.run(command, cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace", timeout=RUN_TIMEOUT)
        print(f"\n$ {' '.join(command)}\n{run.stdout}", flush=True)
        page_dir = os.path.join(output_dir, "index.html")
        return run, page_dir

    def test_identical_fixtures_pass(self):
        run, page_dir = self.run_main("identical", "live", "dev")
        self.assertEqual(run.returncode, 0)
        self.assertIn("[PASS]", run.stdout)
        self.assertNotIn("[FAIL]", run.stdout)
        for name in ("state-box-open_800x600_live.png", "state-box-open_800x600_dev.png",
                     "state-box-open_800x600_html_live.html", "state-box-open_800x600_html_dev.html"):
            self.assertTrue(os.path.exists(os.path.join(page_dir, name)), name)
        leftovers = [n for n in os.listdir(page_dir) if n.startswith("state-") and "diff" in n]
        self.assertEqual(leftovers, [])
        self.assertEqual([n for n in os.listdir(page_dir) if n.endswith(".json")], [])

    def test_state_applied_on_one_side_fails(self):
        run, _ = self.run_main("notrigger", "live", "dev-notrigger")
        self.assertIn("[FAIL] state box-open 800x600: captured on live, absent on dev", run.stdout)

    def test_state_failed_on_both_sides_fails(self):
        run, _ = self.run_main("broken", "dev-broken", "dev-broken")
        self.assertIn("[FAIL] state box-open 800x600: failed on live, failed on dev", run.stdout)

    def test_state_partial_widths_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            states_file = os.path.join(temp_dir, "states.json")
            with open(states_file, "w", encoding="utf-8") as handle:
                json.dump({"version": 1, "states": [
                    {"id": "wide-only", "when": "#wide-only", "steps": [{"action": "click", "selector": "#wide-only"}]},
                ]}, handle)
            run, page_dir = self.run_main("partial", "live", "dev-narrow", "400x600,1200x800", states_file)
        self.assertIn("[FAIL] state wide-only 400x600: captured on live, absent on dev", run.stdout)
        self.assertNotIn("[FAIL] state wide-only 1200x800", run.stdout)
        self.assertIn("No differences detected for state-wide-only_1200x800_screenshot_diff.png", run.stdout)
        for name in ("state-wide-only_400x600_live.png", "state-wide-only_1200x800_live.png",
                     "state-wide-only_1200x800_dev.png"):
            self.assertTrue(os.path.exists(os.path.join(page_dir, name)), name)

    def test_width_set_at_load_fails_in_any_size_order(self):
        # The fixtures differ only in a style that a load-time script enables below 1000 px.
        for sizes in ("1200x800,400x600", "400x600,1200x800"):
            with self.subTest(sizes=sizes):
                run, _ = self.run_main("widths-" + sizes.replace(",", "-"), "live", "dev", sizes,
                                       states_file=None, fixtures=WIDTH_FIXTURES)
                self.assertIn("[FAIL]", run.stdout)
                self.assertIn("Image differences: 400x600p", run.stdout)
                self.assertNotIn("[PASS]", run.stdout)


if __name__ == "__main__":
    unittest.main()
