#!/usr/bin/env python3
import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from typing import Optional
from urllib.parse import urljoin, urlparse, urlunparse, quote

import requests
import urllib3
from bs4 import BeautifulSoup

import baseline
import logging_config
from image_comparator import ImageComparator
from playwright_engine import (
    EngineUnavailable, PlaywrightEngine, compare_state_outcomes, expected_state_entries,
    states_without_widths, to_playwright_cookies,
)
from states import StateFileError, file_sha256, load_states

# Printed by --list-capabilities so callers can detect supported features.
CAPABILITIES = {"engines": ["selenium", "playwright"], "states": 1, "baseline": 1}

logger = logging.getLogger(__name__)

# urllib3 emits warnings when we probe links over self-signed / untrusted TLS with
# certificate verification disabled (to match Chrome's --ignore-certificate-errors).
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Content types we treat as crawlable HTML pages.
HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml")

# File extensions treated as downloadable / non-HTML assets. Used as a fallback
# when a server does not return a usable Content-Type for a link.
DOWNLOADABLE_EXTENSIONS = {
    # documents
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "csv", "rtf", "odt", "ods", "odp",
    # archives
    "zip", "rar", "7z", "tar", "gz", "bz2", "xz",
    # images
    "jpg", "jpeg", "png", "gif", "svg", "webp", "bmp", "ico", "tiff",
    # audio / video
    "mp4", "mp3", "mov", "avi", "wav", "webm", "mkv", "flac", "ogg", "m4a",
    # binaries / packages
    "exe", "dmg", "pkg", "msi", "deb", "rpm", "apk",
}


def write_to_file(content: str, filename: str):
    """
    Writes the given content to a file with UTF-8 encoding.

    :param content:
    :param filename:
    :return:
    """
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(content)


def git_diff(old_file: str, new_file: str) -> tuple:
    """
    Returns (diff text, error). git diff --no-index exits 1 both for a difference and
    for an unreadable file, so any stderr output or an exit code above 1 is an error.

    :param old_file:
    :param new_file:
    :return:
    """
    # Without these settings, Git for Windows (core.autocrlf=true) warns on stderr about
    # every LF file and hides line-ending differences.
    command = ["git", "-c", "core.autocrlf=false", "-c", "core.safecrlf=false",
               "diff", "--no-index", old_file, new_file]
    try:
        done = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                              encoding="utf-8", errors="replace")
    except OSError as error:
        return "", f"git diff failed: {error}"
    if done.returncode > 1 or done.stderr.strip():
        lines = [line for line in done.stderr.splitlines() if line.strip()]
        detail = lines[-1] if lines else f"exit code {done.returncode}"
        return "", f"git diff failed: {detail}"
    return done.stdout, None


def inject_auth(url: str, username: str, password: str) -> str:
    """
    Injects HTTP Basic Auth credentials into a URL.
    For example, inject_auth("https://example.com/path", "user", "pass")
    returns "https://user:pass@example.com/path".

    :param url:
    :param username:
    :param password:
    :return:
    """
    parsed = urlparse(url)
    # remove any existing auth credentials.
    netloc = parsed.netloc.split("@")[-1]
    # URL-encode the username and password in case they contain special characters.
    user_info = f"{quote(username)}:{quote(password)}"
    new_netloc = f"{user_info}@{netloc}"
    new_parsed = parsed._replace(netloc=new_netloc)
    return str(urlunparse(new_parsed))


def is_downloadable_file(url: str) -> bool:
    """
    Return True if the URL's path points to a known downloadable / non-HTML file,
    based on its file extension. Used as a fallback when a link's content type
    cannot be determined.

    :param url:
    :return:
    """
    path = urlparse(url).path
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    return ext in DOWNLOADABLE_EXTENSIONS


def size_names(resolutions: list) -> list:
    """Return resolutions in the helper's text form, e.g. 1920x1080 or fullscreen."""
    names = []
    for res in resolutions:
        names.append("fullscreen" if isinstance(res, str) else f"{res[0]}x{res[1]}")
    return names


class PageDiffProcessor:
    def __init__(self, site_comparator, image_comparator: ImageComparator,
                 output_dir: str, args: argparse.Namespace,
                 engine: Optional[PlaywrightEngine] = None, states: Optional[list] = None):
        """
        Initializes the processor with a SiteComparator instance (Selenium engine) or a
        PlaywrightEngine, an ImageComparator instance, the base output directory, and the
        parsed arguments.
        """
        self.site_comparator = site_comparator
        self.image_comparator = image_comparator
        self.output_dir = output_dir
        self.args = args
        self.engine = engine
        self.states = states or []
        self.image_diffs = []  # Accumulates image differences for summary

    @staticmethod
    def sanitize_path(url: str) -> str:
        """
        Return a sanitized folder name from the URL's path.

        :param url:
        :return:
        """
        parsed = urlparse(url)
        path = parsed.path.strip("/")
        if not path:
            path = "index"
        safe = path.replace("/", "_").replace("?", "_").replace("&", "_")
        return safe

    @staticmethod
    def unique_output_dir(base_output: str, desired: str) -> str:
        """
        Return a unique subfolder under base_output for the desired folder name.

        :param base_output:
        :param desired:
        :return:
        """
        candidate = os.path.join(base_output, desired)
        counter = 1
        while os.path.exists(candidate):
            candidate = os.path.join(base_output, f"{desired}_{counter}")
            counter += 1
        os.makedirs(candidate, exist_ok=True)
        return candidate

    @staticmethod
    def extract_internal_links(html: str, base_url: str) -> set:
        """
        Extract internal links (same domain) from HTML.

        :param html:
        :param base_url:
        :return:
        """
        soup = BeautifulSoup(html, "html.parser")
        links = set()
        base_parsed = urlparse(base_url)
        base_domain = base_parsed.netloc
        for a in soup.find_all("a", href=True):
            href = a["href"]
            full_url = urljoin(base_url, href)
            parsed = urlparse(full_url)
            if parsed.netloc == base_domain and parsed.scheme in ("http", "https"):
                links.add(full_url)
        return links

    @staticmethod
    def get_dev_url(live_url: str, live_base: str, dev_base: str) -> str:
        """
        Return the dev URL corresponding to a live URL.

        :param live_url:
        :param live_base:
        :param dev_base:
        :return:
        """
        if live_url.startswith(live_base):
            return dev_base + live_url[len(live_base):]
        return ''

    def credentials_for(self, label: str) -> Optional[tuple]:
        """Return the (user, password) for the live or dev side, or None."""
        return getattr(self.args, "side_auth", {}).get(label)

    def _probe_content_type(self, url: str) -> Optional[str]:
        """
        Issue a lightweight HEAD request and return the normalized Content-Type
        (lower-cased, without parameters), or None if it cannot be determined.

        :param url:
        :return:
        """
        # Only the live side is crawled.
        auth = self.credentials_for("live")
        try:
            response = requests.head(url, allow_redirects=True, timeout=10, verify=False, auth=auth)
        except requests.RequestException as e:
            logger.debug(f"HEAD request failed for {url}: {e}")
            return None
        content_type = response.headers.get("Content-Type", "")
        if not content_type:
            return None
        return content_type.split(";")[0].strip().lower()

    def should_crawl(self, url: str) -> bool:
        """
        Decide whether a discovered link should be crawled. Non-HTML resources
        (PDFs, images, archives, ...) are skipped unless --include-files is set.
        The content type is determined via a HEAD request, falling back to the
        file extension when the server does not return a usable Content-Type.

        :param url:
        :return:
        """
        if self.args.include_files:
            return True
        content_type = self._probe_content_type(url)
        if content_type is not None:
            return content_type in HTML_CONTENT_TYPES
        # content type unknown — fall back to the extension blocklist.
        return not is_downloadable_file(url)

    def capture_page(self, url: str, label: str, output_dir: str, cookie_file: str = None,
                     capture_screenshots: bool = True):
        """
        Navigates to a URL, optionally loads cookies, waits for content to load,
        then captures the HTML and screenshots at the desired resolutions.

        :param url:
        :param label:
        :param output_dir:
        :param cookie_file:
        :param capture_screenshots:
        :return: html, screenshot paths and a dict with ok, error and the state results
        """
        if self.engine is not None:
            return self.capture_page_playwright(url, label, output_dir, cookie_file, capture_screenshots)

        # inject HTTP Basic Auth credentials if provided.
        credentials = self.credentials_for(label)
        if credentials:
            url = inject_auth(url, *credentials)

        self.site_comparator.driver.get(url)
        if cookie_file:
            self.site_comparator.load_cookies(cookie_file)
            self.site_comparator.driver.get(url)

        time.sleep(2)
        html = self.site_comparator.driver.page_source
        screenshot_paths = {}
        if capture_screenshots:
            for res in self.args.resolutions:
                if isinstance(res, str) and res == "fullscreen":
                    filename = os.path.join(output_dir, f"fullscreen_{label}.png")
                    self.site_comparator.take_fullscreen_screenshot(filename)
                    screenshot_paths["fullscreen"] = filename
                else:
                    w, h = res
                    filename = os.path.join(output_dir, f"{w}x{h}_{label}.png")
                    self.site_comparator.take_screenshot(w, h, filename)
                    screenshot_paths[(w, h)] = filename
        return html, screenshot_paths, {"ok": True, "error": None, "states": []}

    def capture_page_playwright(self, url: str, label: str, output_dir: str, cookie_file: str = None,
                                capture_screenshots: bool = True):
        """
        Captures one page with the Playwright helper, including the interaction states.

        :param url:
        :param label:
        :param output_dir:
        :param cookie_file:
        :param capture_screenshots:
        :return:
        """
        cookies = []
        if cookie_file:
            with open(cookie_file, "r", encoding="utf-8") as f:
                cookies = to_playwright_cookies(json.load(f), url)
        credentials = None
        pair = self.credentials_for(label)
        if pair:
            credentials = {"username": pair[0], "password": pair[1]}

        result = self.engine.capture(
            url=url,
            label=label,
            out_dir=output_dir,
            sizes=self.size_names(),
            states=self.states,
            cookies=cookies,
            http_credentials=credentials,
            user_agent=self.args.user_agent,
            omit_screenshots=not capture_screenshots,
        )
        info = {"ok": result.get("ok", False), "error": result.get("error"),
                "status": result.get("status"), "files": result.get("files", []),
                "states": result.get("states", [])}
        if not info["ok"]:
            return "", {}, info

        with open(os.path.join(output_dir, f"html_{label}.html"), "r", encoding="utf-8") as f:
            html = f.read()
        screenshot_paths = {}
        if capture_screenshots:
            for res in self.args.resolutions:
                if isinstance(res, str) and res == "fullscreen":
                    screenshot_paths["fullscreen"] = os.path.join(output_dir, f"fullscreen_{label}.png")
                else:
                    w, h = res
                    screenshot_paths[(w, h)] = os.path.join(output_dir, f"{w}x{h}_{label}.png")
        return html, screenshot_paths, info

    def size_names(self) -> list:
        """Return the run sizes in the helper's text form."""
        return size_names(self.args.resolutions)

    @staticmethod
    def log_fail(message: str) -> None:
        logger.info(message)
        print(logging_config.COLOR_FAIL + message + logging_config.RESET_ALL)

    def compare_states(self, live_info: dict, dev_info: dict, output_dir: str):
        """
        Compares the captured interaction states of both sides.

        :param live_info:
        :param dev_info:
        :param output_dir:
        :return: descriptions of state differences and the number of state failures
        """
        differences = []
        failures = 0
        expected = expected_state_entries(self.states, self.size_names())
        for state_id, size, verdict, message in compare_state_outcomes(
                expected, live_info["states"], dev_info["states"]):
            prefix = f"state-{state_id}_{size}"
            if verdict == "not-applicable":
                logger.info(f"state {state_id} {size}: not applicable (absent on both sides)")
            elif verdict == "fail":
                failures += 1
                self.log_fail(f"[FAIL] state {state_id} {size}: {message}")
            else:
                # Name each missing file instead of a git error on a missing path.
                needed = [f"{prefix}_html_live.html", f"{prefix}_html_dev.html"]
                if not self.args.omit_screenshots:
                    needed += [f"{prefix}_live.png", f"{prefix}_dev.png"]
                missing = [name for name in needed if not os.path.isfile(os.path.join(output_dir, name))]
                for name in missing:
                    failures += 1
                    self.log_fail(f"[FAIL] state {state_id} {size}: missing file {name}")
                if missing:
                    continue
                html_live = os.path.join(output_dir, f"{prefix}_html_live.html")
                html_dev = os.path.join(output_dir, f"{prefix}_html_dev.html")
                diff_text, diff_error = git_diff(html_live, html_dev)
                if diff_error:
                    failures += 1
                    self.log_fail(f"[FAIL] state {state_id} {size}: {diff_error}")
                elif diff_text:
                    diff_file = os.path.join(output_dir, f"{prefix}_html-difference.diff")
                    write_to_file(diff_text, diff_file)
                    logger.info(f"State HTML diff saved to: {diff_file}")
                    differences.append(f"state {state_id} {size} HTML")
                if not self.args.omit_screenshots:
                    diff_filename = f"{prefix}_screenshot_diff.png"
                    avg_diff = self.image_comparator.compare_screenshots(
                        os.path.join(output_dir, f"{prefix}_live.png"),
                        os.path.join(output_dir, f"{prefix}_dev.png"),
                        os.path.join(output_dir, diff_filename),
                        self.args.always_generate_diff
                    )
                    if avg_diff > self.args.threshold:
                        logger.info(f"Screenshot diff saved to: {diff_filename} (state {state_id} {size}: {avg_diff:.4f})")
                        differences.append(f"state {state_id} {size} ({avg_diff:.4f})")
                    else:
                        logger.info(f"No differences detected for {diff_filename}.")
        return differences, failures

    def process_page(
            self,
            live_url: str,
            dev_url: str,
            output_dir: str,
            current_level: int,
            max_level: int,
            live_base: str,
            dev_base: str,
            visited: set
    ):
        """
        Processes a single page: captures HTML, screenshots, diffs, logs the results,
        and recursively processes internal links if allowed.

        :param live_url:
        :param dev_url:
        :param output_dir:
        :param current_level:
        :param max_level:
        :param live_base:
        :param dev_base:
        :param visited:
        :return:
        """
        if live_url in visited:
            return

        visited.add(live_url)

        # create a unique output folder
        safe_dir = self.sanitize_path(live_url)
        sub_output_dir = self.unique_output_dir(output_dir, safe_dir)
        logger.info(f"Processing page: {live_url} (Level {current_level})")

        # capture live page
        live_html, live_screenshots, live_info = self.capture_page(
            url=live_url,
            label="live",
            output_dir=sub_output_dir,
            cookie_file=self.args.live_cookies,
            capture_screenshots=not self.args.omit_screenshots
        )

        # capture dev page
        dev_html, dev_screenshots, dev_info = self.capture_page(
            url=dev_url,
            label="dev",
            output_dir=sub_output_dir,
            cookie_file=self.args.dev_cookies,
            capture_screenshots=not self.args.omit_screenshots
        )

        self.compare_captures(live_url, dev_url, sub_output_dir, (live_html, live_screenshots, live_info),
                              (dev_html, dev_screenshots, dev_info), baseline.page_key(live_url, live_base))
        self.crawl_links(live_info["ok"], live_html, live_url, current_level, max_level, live_base,
                         dev_base, output_dir, visited)

    @staticmethod
    def compare_status(page_path: str, live_status: Optional[int], dev_status: Optional[int]) -> bool:
        """
        Logs a [FAIL] line when the HTTP status differs and returns True; the Selenium
        engine has no status, so both sides are None there.
        """
        if live_status != dev_status:
            PageDiffProcessor.log_fail(f"[FAIL] {page_path}: status {live_status} on live, {dev_status} on dev")
            return True
        # Error pages are compared on purpose, so the same error status only warns.
        if live_status is not None and live_status >= 400:
            warn_msg = f"[WARN] {page_path}: status {live_status} on both sides"
            logger.warning(warn_msg)
            print(logging_config.COLOR_WARN + warn_msg + logging_config.RESET_ALL)
        return False

    def compare_captures(self, live_url: str, dev_url: str, sub_output_dir: str, live_capture: tuple,
                         dev_capture: tuple, page_path: str):
        """
        Compares two captured sides of one page and logs the [PASS] / [FAIL] line.
        Each capture is the (html, screenshot paths, info) tuple of capture_page;
        page_path is the path below the base URL, used in the status lines.
        """
        live_html, live_screenshots, live_info = live_capture
        dev_html, dev_screenshots, dev_info = dev_capture
        image_diffs = []

        # a page that did not load cannot be compared
        load_errors = []
        for side, info in (("live", live_info), ("dev", dev_info)):
            if not info["ok"]:
                load_errors.append(f"{side} page failed to load: {info['error']}")
        if load_errors:
            fail_msg = f"[FAIL] - {live_url} ({dev_url}) - " + "; ".join(load_errors)
            logger.info(fail_msg)
            print(logging_config.COLOR_FAIL + fail_msg + logging_config.RESET_ALL)
            return

        status_fail = self.compare_status(page_path, live_info.get("status"), dev_info.get("status"))

        # write HTML to files
        live_html_file = os.path.join(sub_output_dir, "html_live.html")
        dev_html_file = os.path.join(sub_output_dir, "html_dev.html")
        write_to_file(live_html, live_html_file)
        write_to_file(dev_html, dev_html_file)

        # generate HTML diff; both files were written just above
        html_diff_text, html_diff_error = git_diff(live_html_file, dev_html_file)
        html_diff_file = os.path.join(sub_output_dir, "html-difference.diff")
        write_to_file(html_diff_text, html_diff_file)
        logger.info(f"HTML diff saved to: {html_diff_file}")

        # compare screenshots
        for res in self.args.resolutions:
            if isinstance(res, str) and res == "fullscreen":
                live_img = live_screenshots.get("fullscreen")
                dev_img = dev_screenshots.get("fullscreen")
                diff_filename = "fullscreen_screenshot_diff.png"
                label_str = "fullscreen"
            else:
                w, h = res
                live_img = live_screenshots.get((w, h))
                dev_img = dev_screenshots.get((w, h))
                diff_filename = f"{w}x{h}_screenshot_diff.png"
                label_str = f"{w}x{h}p"

            if live_img and dev_img:
                diff_output_filename = os.path.join(sub_output_dir, diff_filename)
                avg_diff = self.image_comparator.compare_screenshots(
                    live_img,
                    dev_img,
                    diff_output_filename,
                    self.args.always_generate_diff
                )
                if avg_diff > self.args.threshold:
                    logger.info(f"Screenshot diff saved to: {diff_output_filename} ({label_str}: {avg_diff:.4f})")
                    image_diffs.append(f"{label_str} ({avg_diff:.4f})")
                else:
                    logger.info(f"No differences detected for {diff_filename}.")

        state_diffs, state_failures = [], 0
        if self.engine is not None and self.states:
            state_diffs, state_failures = self.compare_states(live_info, dev_info, sub_output_dir)

        # log summary for this page
        html_fail = os.path.getsize(html_diff_file) > 0
        if status_fail or html_diff_error or html_fail or image_diffs or state_diffs or state_failures:
            fail_msg = f"[FAIL] - {live_url} ({dev_url})"
            if status_fail:
                fail_msg += " - HTTP status differs"
            if html_diff_error:
                fail_msg += f" - HTML {html_diff_error}"
            if html_fail:
                fail_msg += " - HTML differences found"
            if image_diffs:
                fail_msg += " - Image differences: " + ", ".join(image_diffs)
            if state_diffs:
                fail_msg += " - State differences: " + ", ".join(state_diffs)
            if state_failures:
                fail_msg += f" - State failures: {state_failures}"
            logger.info(fail_msg)
            print(logging_config.COLOR_FAIL + fail_msg + logging_config.RESET_ALL)
        else:
            pass_msg = f"[PASS] - {live_url} ({dev_url}) - No differences"
            logger.info(pass_msg)
            print(logging_config.COLOR_PASS + pass_msg + logging_config.RESET_ALL)

    def crawl_links(self, live_loaded: bool, live_html: str, live_url: str, current_level: int,
                    max_level: int, live_base: str, dev_base: str, output_dir: str, visited: set):
        """
        Recurses into the internal links of the live page if the recursive flag is set.
        """
        if live_loaded and self.args.recursive and current_level < max_level:
            sub_links = self.extract_internal_links(live_html, live_url)
            for link in sub_links:
                if link in visited:
                    continue
                sub_dev = self.get_dev_url(link, live_base, dev_base)
                if not sub_dev:
                    continue
                if not self.should_crawl(link):
                    # remember skipped links so they are not probed again on other pages.
                    visited.add(link)
                    logger.debug(f"Skipping non-HTML link: {link}")
                    continue
                self.process_page(link, sub_dev, output_dir, current_level + 1, max_level, live_base,
                                  dev_base, visited)


def run_baseline_mode(parser, args, processor, manifest, urls):
    try:
        if args.capture_baseline:
            failures = baseline.capture_baseline(processor, args.capture_baseline, args.url_live, urls,
                                                 args.states, args.dev_cookies, args.remove_previous_results)
            if failures:
                # The baseline is still written, so a later compare reports the same failures.
                sys.exit(1)
        else:
            baseline.compare_baseline(processor, args.compare_baseline, manifest, args.url_live,
                                      urls, args.dev_cookies)
    except baseline.BaselineError as e:
        parser.error(str(e))


def main():
    parser = argparse.ArgumentParser(
        description="Generate HTML and screenshot diffs between two URLs (and optionally recursively), saving results in a structured folder."
    )
    parser.add_argument("positional_urls", nargs="*", metavar="url",
                        help="Live and dev URL; with --capture-baseline or --compare-baseline one URL")
    parser.add_argument("--output-dir", default="results", help="Output directory (default: results)")
    parser.add_argument('--remove-previous_results', action='store_true',
                        help="Remove previous results before running; with --capture-baseline, replace "
                             "an existing baseline")
    parser.add_argument("--version", default="latest", help="Chrome for Testing version (default: latest)")
    parser.add_argument("--sizes",
                        help="Comma-separated list of resolutions (e.g., 1024x640,1920x1080,fullscreen)")
    parser.add_argument('--threshold', type=float, default=0, help="Threshold for pixel difference (default: 0)")
    parser.add_argument("--always-generate-diff", action="store_true",
                        help="Always generate a diff file even if no differences are found")
    parser.add_argument("--omit-screenshots", action="store_true", help="Omit screenshot capture and diffing")
    parser.add_argument("--user-agent", help="Set a custom user agent")
    parser.add_argument("--live-cookies", help="Path to JSON file containing cookies for the live URL")
    parser.add_argument("--dev-cookies", help="Path to JSON file containing cookies for the dev URL")
    parser.add_argument("--htuser", help="HTTP auth username")
    parser.add_argument("--htpass", help="HTTP auth password")
    parser.add_argument("--engine", default="selenium", choices=["selenium", "playwright"],
                        help="Capture engine (default: selenium)")
    parser.add_argument("--states", help="JSON file with interaction states to capture (needs --engine playwright)")
    parser.add_argument("--capture-baseline", metavar="DIR",
                        help="Capture one URL (or every URL of --urls) into DIR as a baseline "
                             "(needs --engine playwright)")
    parser.add_argument("--compare-baseline", metavar="DIR",
                        help="Compare a URL (or every URL of --urls) against the baseline in DIR "
                             "(needs --engine playwright)")
    parser.add_argument("--urls", dest="urls_file", metavar="FILE",
                        help="File with one URL per line for the baseline modes; # starts a comment")
    parser.add_argument("--list-capabilities", action="store_true",
                        help="Print the supported engines and features as JSON and exit")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                        help="Set the logging level (default: INFO)")
    parser.add_argument("--recursive", action="store_true", default=False,
                        help="Enable recursive processing of internal links")
    # No default here, so main() can tell whether the option was given.
    parser.add_argument("--recursive-max-level", type=int, default=None,
                        help="Maximum recursion level (default: 1)")
    parser.add_argument("--include-files", action="store_true",
                        help="Also crawl links to downloadable/non-HTML files (PDFs, images, archives, ...). "
                             "By default these are skipped during recursive crawling.")

    args = parser.parse_intermixed_args()
    if args.list_capabilities:
        print(json.dumps(CAPABILITIES))
        return
    baseline_dir = args.capture_baseline or args.compare_baseline
    if args.capture_baseline and args.compare_baseline:
        parser.error("use either --capture-baseline or --compare-baseline")
    if baseline_dir:
        if len(args.positional_urls) != 1:
            parser.error("the baseline modes need exactly one URL")
        if args.engine != "playwright":
            parser.error("the baseline modes need --engine playwright")
        if args.live_cookies:
            parser.error("the baseline modes use --dev-cookies only")
        if args.recursive or args.recursive_max_level is not None:
            parser.error("the baseline modes do not crawl: drop --recursive and --recursive-max-level "
                         "and list the pages with --urls")
    else:
        if len(args.positional_urls) != 2:
            parser.error("url_live and url_dev are required")
        if args.urls_file:
            parser.error("--urls needs --capture-baseline or --compare-baseline")
    if args.recursive_max_level is None:
        args.recursive_max_level = 1
    # From here on, no URL carries a password: it would reach logs and baseline files.
    # A URL's credentials apply to the side that carried them; --htuser/--htpass apply to both.
    clean_urls = []
    url_credentials = []
    for url in args.positional_urls:
        clean_url, found = baseline.split_credentials(url)
        clean_urls.append(clean_url)
        url_credentials.append(found)
    args.positional_urls = clean_urls
    try:
        if baseline_dir:
            # One site and one set of credentials, shared by the stored and the compared side.
            found_all = {found for found in url_credentials if found}
            if args.urls_file:
                for url in baseline.read_urls_file(args.urls_file):
                    found = baseline.split_credentials(url)[1]
                    if found:
                        found_all.add(found)
            if len(found_all) > 1:
                parser.error("the URLs give different credentials; use one set")
            shared = baseline.resolve_credentials(next(iter(found_all), None), args.htuser, args.htpass)
            args.side_auth = {"live": shared, "dev": shared}
        else:
            args.side_auth = {
                "live": baseline.resolve_credentials(url_credentials[0], args.htuser, args.htpass),
                "dev": baseline.resolve_credentials(url_credentials[1], args.htuser, args.htpass),
            }
    except baseline.BaselineError as e:
        parser.error(str(e))
    args.url_live = args.positional_urls[0] if args.positional_urls else None
    args.url_dev = args.positional_urls[1] if len(args.positional_urls) > 1 else None
    if args.states and args.engine != "playwright":
        parser.error("--states needs --engine playwright")
    states = []
    if args.states:
        try:
            states = load_states(args.states)
        except StateFileError as e:
            parser.error(str(e))
    if baseline_dir:
        # Before any cleanup: removing the output directory must not delete the baseline.
        try:
            baseline.check_dirs_apart(baseline_dir, args.output_dir)
        except baseline.BaselineError as e:
            parser.error(str(e))
    logging_config.setup_logging(args.log_level)

    output_dir = args.output_dir
    if not args.capture_baseline:
        if args.remove_previous_results and os.path.exists(output_dir):
            logger.info(f"Removing previous results in {output_dir}...")
            shutil.rmtree(output_dir)
        os.makedirs(output_dir, exist_ok=True)

    # parse resolutions. if --sizes is not supplied, default to 1920x1080.
    resolutions = []
    if args.sizes:
        for res in args.sizes.split(','):
            r = res.strip().lower()
            if r == "fullscreen":
                resolutions.append("fullscreen")
            else:
                try:
                    w, h = map(int, r.split('x'))
                    resolutions.append((w, h))
                except Exception:
                    logger.error(f"Invalid resolution format: {res}")
    else:
        resolutions = [(1920, 1080)]
    args.resolutions = resolutions

    # A state without a width would be skipped without a [FAIL].
    empty_states = states_without_widths(states, size_names(resolutions))
    if empty_states:
        parser.error(f"state {', '.join(empty_states)} has no width: --sizes has no WxH entry "
                     f"and the state sets no sizes")

    manifest = None
    baseline_urls = []
    if baseline_dir:
        # Refuse bad input before the engine starts and before a stored baseline is touched.
        try:
            baseline_urls = baseline.run_urls(args.url_live, args.urls_file)
            if args.capture_baseline:
                baseline.check_capture_target(args.capture_baseline, args.remove_previous_results)
            else:
                manifest = baseline.read_baseline_manifest(args.compare_baseline)
        except baseline.BaselineError as e:
            parser.error(str(e))
    if args.compare_baseline:
        mismatches = baseline.input_mismatches(
            manifest, args.engine, size_names(resolutions),
            file_sha256(args.states) if args.states else None, args.omit_screenshots, args.user_agent)
        if mismatches:
            parser.error("the run differs from the baseline: " + "; ".join(mismatches))

    site_comparator = None
    engine = None
    if args.engine == "playwright":
        engine = PlaywrightEngine(os.path.dirname(os.path.abspath(__file__)))
        try:
            engine.ensure_ready()
        except EngineUnavailable as e:
            # Exit code 2, like the other refusals: nothing was compared.
            logger.error(str(e))
            sys.exit(2)
    else:
        # Selenium and Chrome are only needed (and imported) for this engine.
        from chrome_installer import ChromeInstaller
        from site_comparator import SiteComparator

        installer = ChromeInstaller(version=args.version)
        driver_path = installer.install_chromedriver()
        chrome_binary = installer.install_chrome()
        logger.info(f"Using chromedriver at: {driver_path}")
        logger.info(f"Using chrome binary at: {chrome_binary}")
        site_comparator = SiteComparator(driver_path, chrome_binary, user_agent=args.user_agent)
    image_comparator = ImageComparator(threshold=args.threshold)

    processor = PageDiffProcessor(site_comparator, image_comparator, output_dir, args, engine, states)

    if baseline_dir:
        run_baseline_mode(parser, args, processor, manifest, baseline_urls)
        return

    logger.info("Processing base page...")
    processor.process_page(args.url_live, args.url_dev, output_dir, 0, args.recursive_max_level,
                           args.url_live, args.url_dev, set())

    try:
        html_diff_file = os.path.join(output_dir, "html-difference.diff")
        if os.path.getsize(html_diff_file) > 0 or processor.image_diffs:
            print(
                logging_config.COLOR_FAIL + f"[FAIL] - {args.url_live} ({args.url_dev}) - HTML differences or image differences: {', '.join(processor.image_diffs)}" + logging_config.RESET_ALL)
        else:
            print(
                logging_config.COLOR_PASS + f"[PASS] - {args.url_live} ({args.url_dev}) - No differences" + logging_config.RESET_ALL)
    except Exception as e:
        logger.info(f"Error reading diff file: {e}")


if __name__ == '__main__':
    main()
