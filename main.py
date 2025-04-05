#!/usr/bin/env python3
import argparse
import os
import subprocess
import tempfile
import time

from chrome_installer import ChromeInstaller
from site_comparator import SiteComparator

def write_to_file(content, filename):
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(content)

def compare_screenshots(img1_path, img2_path, diff_output_path, threshold=5):
    from PIL import Image, ImageChops, ImageStat
    # Open images and convert to RGB for consistency.
    img1 = Image.open(img1_path).convert('RGB')
    img2 = Image.open(img2_path).convert('RGB')
    diff = ImageChops.difference(img1, img2)
    stat = ImageStat.Stat(diff)
    avg_diff = sum(stat.mean) / len(stat.mean)
    print(f"Average difference for {img1_path} vs {img2_path}: {avg_diff}")
    if avg_diff > threshold:
        diff.save(diff_output_path)
        return True
    return False

def capture_page(comparator, url, label, resolutions, output_dir):
    """
    Navigates to the URL once, waits for the page to load, then:
      - Captures the HTML.
      - For each resolution in 'resolutions':
            * If resolution is a tuple (w, h), sets the window size and saves a screenshot.
            * If resolution equals "fullscreen", uses Chrome's CDP command to capture a full-page screenshot.
    Returns the HTML content and a dictionary mapping each resolution (tuple or "fullscreen")
    to its screenshot path.
    """
    comparator.driver.get(url)
    time.sleep(3)
    html = comparator.driver.page_source
    screenshot_paths = {}
    for res in resolutions:
        if isinstance(res, str) and res == "fullscreen":
            filename = os.path.join(output_dir, f"fullscreen_{label}.png")
            comparator.take_fullscreen_screenshot(filename)
            screenshot_paths["fullscreen"] = filename
        else:
            w, h = res
            filename = os.path.join(output_dir, f"{w}x{h}_{label}.png")
            comparator.driver.set_window_size(w, h)
            time.sleep(1)  # short delay after resizing
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
    parser.add_argument("--omit-screenshots", action="store_true", help="Omit screenshot comparison")
    parser.add_argument("--sizes", help="Comma-separated list of resolutions (e.g., 1024x640,1366x768,1920x1080,fullscreen)")
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

    comparator = SiteComparator(driver_path, chrome_binary)

    # Capture live page.
    live_html, live_screenshots = capture_page(comparator, args.url_live, "live", resolutions, output_dir)
    live_html_file = os.path.join(output_dir, "live.html")
    write_to_file(live_html, live_html_file)

    # Capture staging (dev) page.
    staging_html, staging_screenshots = capture_page(comparator, args.url_staging, "dev", resolutions, output_dir)
    staging_html_file = os.path.join(output_dir, "dev.html")
    write_to_file(staging_html, staging_html_file)

    # Generate HTML diff.
    diff_cmd = ["git", "diff", "--no-index", live_html_file, staging_html_file]
    result = subprocess.run(diff_cmd, stdout=subprocess.PIPE, text=True)
    html_diff_file = os.path.join(output_dir, "html-difference.diff")
    write_to_file(result.stdout, html_diff_file)
    print(f"HTML diff saved to: {html_diff_file}")

    # Compare screenshots for each resolution.
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
                if compare_screenshots(live_img, dev_img, diff_output_filename):
                    print(f"Screenshot diff saved to: {diff_output_filename}")
                else:
                    print(f"No differences detected for {diff_filename}.")
    comparator.quit_driver()

if __name__ == '__main__':
    main()
