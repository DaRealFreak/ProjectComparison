#!/usr/bin/env python3
import argparse
import os
import subprocess
import time

from chrome_installer import ChromeInstaller
from image_comparator import compare_screenshots
from site_comparator import SiteComparator


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
        # Refresh after cookies are added.
        comparator.driver.get(url)

    # Wait a little to let the page settle.
    time.sleep(2)
    html = comparator.driver.page_source
    screenshot_paths = {}
    if capture_screenshots:
        for res in resolutions:
            if isinstance(res, str) and res == "fullscreen":
                filename = os.path.join(output_dir, f"fullscreen_{label}.png")
                comparator.driver.refresh()
                time.sleep(0.5)
                comparator.take_fullscreen_screenshot(filename)
                screenshot_paths["fullscreen"] = filename
            else:
                w, h = res
                filename = os.path.join(output_dir, f"{w}x{h}_{label}.png")
                comparator.driver.set_window_size(w, h)
                comparator.driver.refresh()
                # A short delay after resizing.
                time.sleep(0.5)
                comparator.driver.save_screenshot(filename)
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
    parser.add_argument("--omit-screenshots", action="store_true", help="Omit screenshot capture and diffing")
    parser.add_argument("--sizes",
                        help="Comma-separated list of resolutions (e.g., 1024x640,1366x768,1920x1080,fullscreen)")
    parser.add_argument("--user-agent", help="Set a custom user agent")
    parser.add_argument("--live-cookies", help="Path to JSON file containing cookies for the live URL")
    parser.add_argument("--staging-cookies", help="Path to JSON file containing cookies for the staging URL")
    args = parser.parse_args()

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
                    print(f"Invalid resolution format: {res}")
    else:
        resolutions = [(1920, 1080)]

    installer = ChromeInstaller(version=args.version)
    driver_path = installer.install_chromedriver()
    chrome_binary = installer.install_chrome()
    print(f"Using chromedriver at: {driver_path}")
    print(f"Using chrome binary at: {chrome_binary}")

    comparator = SiteComparator(driver_path, chrome_binary, user_agent=args.user_agent)

    # Capture live page.
    live_html, live_screenshots = capture_page(
        comparator,
        args.url_live,
        "live",
        resolutions,
        output_dir,
        cookie_file=args.live_cookies,
        capture_screenshots=not args.omit_screenshots
    )
    live_html_file = os.path.join(output_dir, "live.html")
    write_to_file(live_html, live_html_file)

    # Capture staging (dev) page.
    staging_html, staging_screenshots = capture_page(
        comparator,
        args.url_staging,
        "dev",
        resolutions,
        output_dir,
        cookie_file=args.staging_cookies,
        capture_screenshots=not args.omit_screenshots
    )
    staging_html_file = os.path.join(output_dir, "dev.html")
    write_to_file(staging_html, staging_html_file)

    # Generate HTML diff.
    diff_cmd = ["git", "diff", "--no-index", live_html_file, staging_html_file]
    result = subprocess.run(diff_cmd, stdout=subprocess.PIPE, text=True)
    html_diff_file = os.path.join(output_dir, "html-difference.diff")
    write_to_file(result.stdout, html_diff_file)
    print(f"HTML diff saved to: {html_diff_file}")

    # Compare screenshots if they were captured.
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
                diff_output_filename = os.path.join(output_dir, diff_filename)
                if compare_screenshots(live_img, dev_img, diff_output_filename, threshold=0):
                    print(f"Screenshot diff saved to: {diff_output_filename}")
                else:
                    print(f"No differences detected for {diff_filename}.")
    comparator.quit_driver()


if __name__ == '__main__':
    main()
