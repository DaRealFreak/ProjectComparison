#!/usr/bin/env python3
"""
Cross-platform end-to-end smoke test for the page-comparison tool.

It serves two local fixture sites ("live" and "dev") from the bundled
``tests/fixtures`` directory and runs ``main.py`` against them exactly as a user
would. The point is to exercise the *real* run setup on whatever OS this runs on:
downloading Chrome for Testing, marking the binary executable, and launching
headless Chrome. The comparison targets are local and deterministic, so the only
moving parts under test are the tool itself and the browser setup.

Two scenarios are checked:
  * identical pages  -> the tool must report ``[PASS]``
  * differing pages  -> the tool must report ``[FAIL]``

The process exits non-zero if either scenario does not produce the expected
result, which fails the CI job. It can also be run locally with no arguments:

    python tests/smoke_test.py

Results are written under ``results/`` (git-ignored) and left in place so a failed
CI run can upload them as an artifact for inspection.
"""
import functools
import http.server
import os
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "fixtures")
RESULTS = os.path.join(REPO_ROOT, "results")

# First invocation downloads Chrome for Testing (~150 MB) and chromedriver, so the
# timeout has to be generous; later invocations reuse the cached download.
RUN_TIMEOUT = 600  # seconds


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler without the per-request stderr logging."""

    def log_message(self, *args):
        pass


def serve(directory: str):
    """Start a background HTTP server for ``directory`` on a free port.

    Returns ``(httpd, port)``. Binding to port 0 lets the OS pick a free port so
    the test never collides with something already listening.
    """
    handler = functools.partial(_QuietHandler, directory=directory)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, httpd.server_address[1]


def run_comparison(live_url: str, dev_url: str, output_dir: str) -> subprocess.CompletedProcess:
    """Run ``main.py`` for a single comparison and capture combined output.

    stderr is merged into stdout because the per-page ``[PASS]``/``[FAIL]`` summary
    is emitted both via ``logging`` (stderr) and ``print`` (stdout); merging means
    any Chrome/Selenium traceback also lands in the captured text.
    """
    cmd = [
        sys.executable, "main.py", live_url, dev_url,
        "--output-dir", output_dir,
        "--remove-previous_results",
        "--sizes", "1024x640,fullscreen",
    ]
    print(f"\n$ {' '.join(cmd)}", flush=True)
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=RUN_TIMEOUT,
    )
    print(proc.stdout, flush=True)
    return proc


def main() -> int:
    live_httpd, live_port = serve(os.path.join(FIXTURES, "live"))
    dev_httpd, dev_port = serve(os.path.join(FIXTURES, "dev"))
    live = f"http://127.0.0.1:{live_port}"
    dev = f"http://127.0.0.1:{dev_port}"

    failures = []
    try:
        # Scenario 1: identical pages -> expect PASS.
        proc = run_comparison(
            f"{live}/index.html", f"{dev}/index.html",
            os.path.join(RESULTS, "identical"),
        )
        if proc.returncode != 0:
            failures.append(f"identical run exited with code {proc.returncode}")
        elif "[FAIL]" in proc.stdout or "[PASS]" not in proc.stdout:
            failures.append("identical pages did not report [PASS]")

        # Scenario 2: differing pages -> expect FAIL.
        proc = run_comparison(
            f"{live}/changed.html", f"{dev}/changed.html",
            os.path.join(RESULTS, "changed"),
        )
        if proc.returncode != 0:
            failures.append(f"changed run exited with code {proc.returncode}")
        elif "[FAIL]" not in proc.stdout:
            failures.append("differing pages did not report [FAIL]")
    finally:
        live_httpd.shutdown()
        dev_httpd.shutdown()

    if failures:
        print("\nSMOKE TEST FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("\nSMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
