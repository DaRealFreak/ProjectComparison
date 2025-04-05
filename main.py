#!/usr/bin/env python3
import argparse
import logging
import os
import subprocess
import time
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

import logging_config
from chrome_installer import ChromeInstaller
from image_comparator import ImageComparator
from site_comparator import SiteComparator

logger = logging.getLogger(__name__)


def write_to_file(content: str, filename: str):
    """
    Writes the given content to a file with the specified filename.

    :param content:
    :param filename:
    :return:
    """
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(content)


def extract_internal_links(html: str, base_url: str) -> set:
    """
    Parses the HTML and returns a set of full URLs that are internal to the base URL's domain.

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


def get_staging_url(live_url: str, live_base: str, staging_base: str) -> str:
    """
    Given a live URL, returns the corresponding staging URL by replacing the live_base with staging_base.

    :param live_url:
    :param live_base:
    :param staging_base:
    :return:
    """
    if live_url.startswith(live_base):
        return staging_base + live_url[len(live_base):]

    return ''


def sanitize_path(url: str) -> str:
    """
    Returns a sanitized string from the URL's path for use as a folder name.

    :param url:
    :return:
    """
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    if not path:
        path = "index"
    safe = path.replace("/", "_").replace("?", "_").replace("&", "_")
    return safe


def unique_output_dir(base_output: str, desired: str) -> str:
    """
    Returns a unique subfolder name under base_output for the desired folder name.
    If the folder already exists, appends a numeric suffix.

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


def capture_page(comparator: SiteComparator, url: str, label: str, resolutions: list, output_dir: str,
                 cookie_file: str = None, capture_screenshots: bool = True):
    """
    Navigates to the URL and loads cookies if provided, then:
      - Captures the HTML.
      - If capture_screenshots is True, captures a screenshot for each resolution:
            * "fullscreen": capture a full-page screenshot.
            * Otherwise, set the window size and capture a screenshot.
    Returns the HTML content and a dict mapping each resolution (tuple or "fullscreen")
    to its screenshot path.

    :param comparator:
    :param url:
    :param label:
    :param resolutions:
    :param output_dir:
    :param cookie_file:
    :param capture_screenshots:
    :return:
    """
    comparator.driver.get(url)
    if cookie_file:
        comparator.load_cookies(cookie_file)
        # refresh after cookies are added (selenium can only add cookies while we're on the domain)
        comparator.driver.get(url)

    # sleep 2 seconds to let the JavaScript load/restructure elements (f.e. galleries/sliders)
    time.sleep(2)
    html = comparator.driver.page_source
    screenshot_paths = {}
    if capture_screenshots:
        for res in resolutions:
            # special case for "fullscreen"
            if isinstance(res, str) and res == "fullscreen":
                filename = os.path.join(output_dir, f"fullscreen_{label}.png")
                comparator.take_fullscreen_screenshot(filename)
                screenshot_paths["fullscreen"] = filename
            else:
                w, h = res
                filename = os.path.join(output_dir, f"{w}x{h}_{label}.png")
                comparator.take_screenshot(w, h, filename)
                screenshot_paths[(w, h)] = filename

    return html, screenshot_paths


def process_page(live_url: str, staging_url: str, output_dir: str, current_level: int, max_level: int,
                 site_comparator: SiteComparator, image_comparator: ImageComparator, args,
                 live_base: str, staging_base: str, visited: set):
    """
    Processes a single page: captures HTML, screenshots, diffs, logs the results,
    and recursively processes internal links if allowed.
    """
    if live_url in visited:
        return

    visited.add(live_url)
    safe_dir = sanitize_path(live_url)
    sub_output_dir = unique_output_dir(output_dir, safe_dir)
    logger.info(f"Processing page: {live_url} (Level {current_level})")

    live_html, live_screenshots = capture_page(site_comparator, live_url, "live", args.resolutions,
                                               sub_output_dir, cookie_file=args.live_cookies,
                                               capture_screenshots=not args.omit_screenshots)
    staging_html, staging_screenshots = capture_page(site_comparator, staging_url, "dev", args.resolutions,
                                                     sub_output_dir, cookie_file=args.staging_cookies,
                                                     capture_screenshots=not args.omit_screenshots)

    live_html_file = os.path.join(sub_output_dir, "live.html")
    staging_html_file = os.path.join(sub_output_dir, "dev.html")

    write_to_file(live_html, live_html_file)
    write_to_file(staging_html, staging_html_file)

    diff_cmd = ["git", "diff", "--no-index", live_html_file, staging_html_file]
    # Specify encoding and error handling for diff output.
    result = subprocess.run(diff_cmd, stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    html_diff_file = os.path.join(sub_output_dir, "html-difference.diff")
    write_to_file(result.stdout, html_diff_file)
    logger.info(f"HTML diff saved to: {html_diff_file}")
    image_diffs = []
    if not args.omit_screenshots:
        for res in args.resolutions:
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
                avg_diff = image_comparator.compare_screenshots(live_img, dev_img, diff_output_filename,
                                                                args.always_generate_diff)
                if avg_diff > args.threshold:
                    logger.info(f"Screenshot diff saved to: {diff_output_filename} ({label_str}: {avg_diff:.4f})")
                    image_diffs.append(f"{label_str} ({avg_diff:.4f})")
                else:
                    logger.info(f"No differences detected for {diff_filename}.")
    # Print summary for this page.
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
    # Recurse if allowed.
    if current_level < max_level:
        sub_links = extract_internal_links(live_html, live_url)
        for link in sub_links:
            if link not in visited:
                sub_staging = get_staging_url(link, live_base, staging_base)
                if sub_staging:
                    process_page(link, sub_staging, output_dir, current_level + 1, max_level,
                                 site_comparator, image_comparator, args, live_base, staging_base, visited)


def main():
    parser = argparse.ArgumentParser(
        description="Generate HTML and screenshot diffs between two URLs (and optionally recursively), saving results in a structured folder."
    )
    parser.add_argument("url_live", help="Live URL")
    parser.add_argument("url_staging", help="Staging URL")
    parser.add_argument("--output-dir", default="results", help="Output directory (default: results)")
    parser.add_argument("--version", default="latest", help="Chrome for Testing version (default: latest)")
    parser.add_argument("--sizes",
                        help="Comma-separated list of resolutions (e.g., 1024x640,1920x1080,fullscreen)")
    parser.add_argument('--threshold', type=float, default=0, help="Threshold for pixel difference (default: 0)")
    parser.add_argument("--always-generate-diff", action="store_true",
                        help="Always generate a diff file even if no differences are found")
    parser.add_argument("--omit-screenshots", action="store_true", help="Omit screenshot capture and diffing")
    parser.add_argument("--user-agent", help="Set a custom user agent")
    parser.add_argument("--live-cookies", help="Path to JSON file containing cookies for the live URL")
    parser.add_argument("--staging-cookies", help="Path to JSON file containing cookies for the staging URL")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                        help="Set the logging level (default: INFO)")
    parser.add_argument("--recursive", action="store_true", help="Enable recursive processing of internal links")
    parser.add_argument("--recursive-max-level", type=int, default=1, help="Maximum recursion level (default: 1)")

    args = parser.parse_args()
    logging_config.setup_logging(args.log_level)

    output_dir = args.output_dir
    os.makedirs(output_dir, exist_ok=True)

    # Parse resolutions. If --sizes is not supplied, default to 1920x1080.
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

    # Process the base page.
    logger.info("Capturing base page...")
    live_html, live_screenshots = capture_page(
        site_comparator,
        args.url_live,
        "live",
        resolutions,
        output_dir,
        cookie_file=args.live_cookies,
        capture_screenshots=not args.omit_screenshots
    )
    live_html_file = os.path.join(output_dir, "live.html")
    write_to_file(live_html, live_html_file)

    staging_html, staging_screenshots = capture_page(
        site_comparator,
        args.url_staging,
        "dev",
        resolutions,
        output_dir,
        cookie_file=args.staging_cookies,
        capture_screenshots=not args.omit_screenshots
    )
    staging_html_file = os.path.join(output_dir, "dev.html")
    write_to_file(staging_html, staging_html_file)

    diff_cmd = ["git", "diff", "--no-index", live_html_file, staging_html_file]
    result = subprocess.run(diff_cmd, stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    html_diff_file = os.path.join(output_dir, "html-difference.diff")
    write_to_file(result.stdout, html_diff_file)
    logger.info(f"HTML diff saved to: {html_diff_file}")

    image_diffs = []
    if not args.omit_screenshots:
        for res in resolutions:
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
                diff_output_filename = os.path.join(output_dir, diff_filename)
                avg_diff = image_comparator.compare_screenshots(live_img, dev_img, diff_output_filename,
                                                                args.always_generate_diff)
                if avg_diff > args.threshold:
                    logger.info(f"Screenshot diff saved to: {diff_output_filename} ({label_str}: {avg_diff:.4f})")
                    image_diffs.append(f"{label_str} ({avg_diff:.4f})")
                else:
                    logger.info(f"No differences detected for {diff_filename}.")
    # Print overall summary for base page.
    base_html_fail = os.path.getsize(html_diff_file) > 0
    if base_html_fail or image_diffs:
        base_fail_msg = f"[FAIL] - {args.url_live} ({args.url_staging})"
        if base_html_fail:
            base_fail_msg += " - HTML differences found"
        if image_diffs:
            base_fail_msg += " - Image differences: " + ", ".join(image_diffs)
        logger.info(base_fail_msg)
        print(logging_config.COLOR_FAIL + base_fail_msg + logging_config.RESET_ALL)
    else:
        pass_msg = f"[PASS] - {args.url_live} ({args.url_staging}) - No differences"
        logger.info(pass_msg)
        print(logging_config.COLOR_PASS + pass_msg + logging_config.RESET_ALL)

    # Process recursive pages if enabled.
    if args.recursive:
        visited = set()
        live_base = args.url_live
        staging_base = args.url_staging
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(live_html, "html.parser")
        base_links = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            full_url = urljoin(args.url_live, href)
            parsed = urlparse(full_url)
            if parsed.netloc == urlparse(live_base).netloc and parsed.scheme in ("http", "https"):
                base_links.add(full_url)
        logger.info(f"Found {len(base_links)} internal links on base page for recursion.")
        for link in base_links:
            if link not in visited:
                sub_staging = get_staging_url(link, live_base, staging_base)
                if sub_staging:
                    process_page(link, sub_staging, output_dir, 1, args.recursive_max_level,
                                 site_comparator, image_comparator, args, live_base, staging_base, visited)

    site_comparator.quit_driver()

    try:
        if os.path.getsize(html_diff_file) > 0 or image_diffs:
            print(
                logging_config.COLOR_FAIL + f"[FAIL] - {args.url_live} ({args.url_staging}) - HTML differences or image differences: {', '.join(image_diffs)}" + logging_config.RESET_ALL)
        else:
            print(
                logging_config.COLOR_PASS + f"[PASS] - {args.url_live} ({args.url_staging}) - No differences" + logging_config.RESET_ALL)
    except Exception as e:
        logger.info(f"Error reading diff file: {e}")


if __name__ == '__main__':
    main()
