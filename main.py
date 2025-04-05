#!/usr/bin/env python3
import argparse
import logging
import os
import shutil
import subprocess
import time
from argparse import Namespace
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

import logging_config
from chrome_installer import ChromeInstaller
from image_comparator import ImageComparator
from site_comparator import SiteComparator

logger = logging.getLogger(__name__)


class PageDiffProcessor:
    def __init__(self, site_comparator: SiteComparator, image_comparator: ImageComparator,
                 output_dir: str, args: Namespace):
        """
        Initializes the processor with a SiteComparator instance, an ImageComparator instance,
        the base output directory, and the parsed arguments.
        """
        self.site_comparator = site_comparator
        self.image_comparator = image_comparator
        self.output_dir = output_dir
        self.args = args
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
    def get_staging_url(live_url: str, live_base: str, staging_base: str) -> str:
        """
        Return the staging URL corresponding to a live URL.

        :param live_url:
        :param live_base:
        :param staging_base:
        :return:
        """
        if live_url.startswith(live_base):
            return staging_base + live_url[len(live_base):]
        return ''

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
        :return:
        """
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
        return html, screenshot_paths

    def process_page(
            self,
            live_url: str,
            staging_url: str,
            output_dir: str,
            current_level: int,
            max_level: int,
            live_base: str,
            staging_base: str,
            visited: set
    ):
        """
        Processes a single page: captures HTML, screenshots, diffs, logs the results,
        and recursively processes internal links if allowed.

        :param live_url:
        :param staging_url:
        :param output_dir:
        :param current_level:
        :param max_level:
        :param live_base:
        :param staging_base:
        :param visited:
        :return:
        """
        if live_url in visited:
            return

        visited.add(live_url)

        # initialize local image differences for this page
        image_diffs = []

        # create a unique output folder
        safe_dir = self.sanitize_path(live_url)
        sub_output_dir = self.unique_output_dir(output_dir, safe_dir)
        logger.info(f"Processing page: {live_url} (Level {current_level})")

        # capture live page
        live_html, live_screenshots = self.capture_page(
            url=live_url,
            label="live",
            output_dir=sub_output_dir,
            cookie_file=self.args.live_cookies,
            capture_screenshots=not self.args.omit_screenshots
        )

        # capture staging page
        staging_html, staging_screenshots = self.capture_page(
            url=staging_url,
            label="dev",
            output_dir=sub_output_dir,
            cookie_file=self.args.dev_cookies,
            capture_screenshots=not self.args.omit_screenshots
        )

        # write HTML to files
        live_html_file = os.path.join(sub_output_dir, "html_live.html")
        staging_html_file = os.path.join(sub_output_dir, "html_dev.html")
        write_to_file(live_html, live_html_file)
        write_to_file(staging_html, staging_html_file)

        # generate HTML diff
        diff_cmd = ["git", "diff", "--no-index", live_html_file, staging_html_file]
        result = subprocess.run(diff_cmd, stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        html_diff_file = os.path.join(sub_output_dir, "html-difference.diff")
        write_to_file(result.stdout, html_diff_file)
        logger.info(f"HTML diff saved to: {html_diff_file}")

        # compare screenshots
        for res in self.args.resolutions:
            if isinstance(res, str) and res == "fullscreen":
                live_img = live_screenshots.get("fullscreen")
                dev_img = staging_screenshots.get("fullscreen")
                diff_filename = "fullscreen_screenshot_diff.png"
                label_str = "fullscreen"
            else:
                w, h = res
                live_img = live_screenshots.get((w, h))
                dev_img = staging_screenshots.get((w, h))
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

        # log summary for this page
        html_fail = os.path.getsize(html_diff_file) > 0
        if html_fail or image_diffs:
            fail_msg = f"[FAIL] - {live_url} ({staging_url})"
            if html_fail:
                fail_msg += " - HTML differences found"
            if image_diffs:
                fail_msg += " - Image differences: " + ", ".join(image_diffs)
            logger.info(fail_msg)
            print(logging_config.COLOR_FAIL + fail_msg + logging_config.RESET_ALL)
        else:
            pass_msg = f"[PASS] - {live_url} ({staging_url}) - No differences"
            logger.info(pass_msg)
            print(logging_config.COLOR_PASS + pass_msg + logging_config.RESET_ALL)

        # recurse if allowed and if the recursive flag is set
        if self.args.recursive and current_level < max_level:
            sub_links = self.extract_internal_links(live_html, live_url)
            for link in sub_links:
                if link not in visited:
                    sub_staging = self.get_staging_url(link, live_base, staging_base)
                    if sub_staging:
                        self.process_page(link, sub_staging, output_dir, current_level + 1, max_level, live_base,
                                          staging_base, visited)


def write_to_file(content: str, filename: str):
    """
    Writes the given content to a file with UTF-8 encoding.

    :param content:
    :param filename:
    :return:
    """
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(content)


def main():
    parser = argparse.ArgumentParser(
        description="Generate HTML and screenshot diffs between two URLs (and optionally recursively), saving results in a structured folder."
    )
    parser.add_argument("url_live", help="Live URL")
    parser.add_argument("url_staging", help="Staging URL")
    parser.add_argument("--output-dir", default="results", help="Output directory (default: results)")
    parser.add_argument('--remove-previous_results', action='store_true', help="Remove previous results before running")
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
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                        help="Set the logging level (default: INFO)")
    parser.add_argument("--recursive", action="store_true", default=False,
                        help="Enable recursive processing of internal links")
    parser.add_argument("--recursive-max-level", type=int, default=1, help="Maximum recursion level (default: 1)")

    args = parser.parse_args()
    logging_config.setup_logging(args.log_level)

    output_dir = args.output_dir
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

    installer = ChromeInstaller(version=args.version)
    driver_path = installer.install_chromedriver()
    chrome_binary = installer.install_chrome()
    logger.info(f"Using chromedriver at: {driver_path}")
    logger.info(f"Using chrome binary at: {chrome_binary}")

    site_comparator = SiteComparator(driver_path, chrome_binary, user_agent=args.user_agent)
    image_comparator = ImageComparator(threshold=args.threshold)

    processor = PageDiffProcessor(site_comparator, image_comparator, output_dir, args)

    # process the base page.
    logger.info("Capturing base page...")
    processor.process_page(args.url_live, args.url_staging, output_dir, 0, args.recursive_max_level,
                           args.url_live, args.url_staging, set())

    try:
        html_diff_file = os.path.join(output_dir, "html-difference.diff")
        if os.path.getsize(html_diff_file) > 0 or processor.image_diffs:
            print(
                logging_config.COLOR_FAIL + f"[FAIL] - {args.url_live} ({args.url_staging}) - HTML differences or image differences: {', '.join(processor.image_diffs)}" + logging_config.RESET_ALL)
        else:
            print(
                logging_config.COLOR_PASS + f"[PASS] - {args.url_live} ({args.url_staging}) - No differences" + logging_config.RESET_ALL)
    except Exception as e:
        logger.info(f"Error reading diff file: {e}")


if __name__ == '__main__':
    main()
