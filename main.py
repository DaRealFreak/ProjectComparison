#!/usr/bin/env python3
import argparse
import logging
import os
import subprocess
import time

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


def capture_page(comparator: SiteComparator, url: str, label: str, resolutions: list, output_dir: str,
                 cookie_file: str = None, capture_screenshots: bool = True):
    """
        Navigates to the URL and loads cookies if provided, then:
      - Captures the HTML.
      - If capture_screenshots is True:
            For each resolution in 'resolutions':
              * If resolution is "fullscreen", capture a full-page screenshot.
              * Otherwise, set the window size and capture a screenshot.
    Returns the HTML content and a dictionary mapping each resolution (tuple or "fullscreen")
    to its screenshot path (or an empty dict if screenshots are omitted).

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


def main():
    parser = argparse.ArgumentParser(
        description="Generate HTML and screenshot diffs between two URLs, saving results in a structured folder."
    )
    parser.add_argument("url_live", help="Live URL")
    parser.add_argument("url_staging", help="Staging URL")
    parser.add_argument("--output-dir", default="results", help="Output directory (default: results)")
    parser.add_argument("--version", default="latest", help="Chrome for Testing version (default: latest)")
    parser.add_argument("--sizes", help="Comma-separated list of resolutions (e.g., 1024x640,1920x1080,fullscreen)")
    parser.add_argument('--threshold', type=float, default=0, help="Threshold for pixel difference (default: 0)")
    parser.add_argument("--always-generate-diff", action="store_true",
                        help="Always generate a diff file even if no differences are found")
    parser.add_argument("--omit-screenshots", action="store_true", help="Omit screenshot capture and diffing")
    parser.add_argument("--user-agent", help="Set a custom user agent")
    parser.add_argument("--live-cookies", help="Path to JSON file containing cookies for the live URL")
    parser.add_argument("--staging-cookies", help="Path to JSON file containing cookies for the staging URL")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                        help="Set the logging level (default: INFO)")

    args = parser.parse_args()
    logging_config.setup_logging(args.log_level)

    output_dir = args.output_dir
    os.makedirs(output_dir, exist_ok=True)

    # parse resolutions. if --sizes is not supplied, default to 1920x1080
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

    installer = ChromeInstaller(version=args.version)
    driver_path = installer.install_chromedriver()
    chrome_binary = installer.install_chrome()
    logger.info(f"Using chromedriver at: {driver_path}")
    logger.info(f"Using chrome binary at: {chrome_binary}")

    site_comparator = SiteComparator(driver_path, chrome_binary, user_agent=args.user_agent)
    image_comparator = ImageComparator(threshold=args.threshold)

    # capture live page
    logger.info("Capturing live page...")
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

    # capture dev page
    logger.info("Capturing staging page...")
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

    # generate HTML diff (using git diff here for windows compatibility since the diff tool is not commonly available)
    diff_cmd = ["git", "diff", "--no-index", live_html_file, staging_html_file]
    result = subprocess.run(diff_cmd, stdout=subprocess.PIPE, text=True)
    html_diff_file = os.path.join(output_dir, "html-difference.diff")
    write_to_file(result.stdout, html_diff_file)
    logger.info(f"HTML diff saved to: {html_diff_file}")

    # compare screenshots if they were captured
    if not args.omit_screenshots:
        for res in resolutions:
            if isinstance(res, str) and res == "fullscreen":
                live_img = live_screenshots.get("fullscreen")
                dev_img = staging_screenshots.get("fullscreen")
                diff_filename = "fullscreen_screenshot_diff.png"
            else:
                w, h = res
                live_img = live_screenshots.get((w, h))
                dev_img = staging_screenshots.get((w, h))
                diff_filename = f"{w}x{h}_screenshot_diff.png"
            if live_img and dev_img:
                diff_filename = os.path.join(output_dir, diff_filename)
                if image_comparator.compare_screenshots(live_img, dev_img, diff_filename, args.always_generate_diff):
                    logger.info(f"Screenshot diff saved to: {diff_filename}")
                else:
                    logger.info(f"No differences detected for {diff_filename}.")

    site_comparator.quit_driver()

    try:
        if os.path.getsize(html_diff_file) > 0:
            print(logging_config.COLOR_FAIL + f"[FAIL] - {args.url_live} ({args.url_staging}) - HTML differences found")
        else:
            print(logging_config.COLOR_PASS + f"[PASS] - {args.url_live} ({args.url_staging}) - No HTML differences")
    except Exception as e:
        logger.info(f"Error reading diff file: {e}")


if __name__ == '__main__':
    main()
