#!/usr/bin/env python3
import argparse
import os
import subprocess
import time

from chrome_installer import ChromeInstaller
from site_comparator import SiteComparator


def write_to_file(content, filename):
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(content)


def compare_screenshots(img1_path, img2_path, diff_output_path):
    from PIL import Image, ImageChops
    img1 = Image.open(img1_path)
    img2 = Image.open(img2_path)
    diff = ImageChops.difference(img1, img2)
    if diff.getbbox() is not None:
        diff.save(diff_output_path)
        return True
    return False


def capture_page(comparator, url, label, resolutions, output_dir):
    """
    Navigates to the URL, waits for the page to load, then:
      - Captures the HTML.
      - Captures a default screenshot (using the first resolution or a default).
      - Captures screenshots at each specified resolution (by resizing the window).
    Saves files in output_dir using the label (e.g. "live" or "dev").
    Returns the HTML content and a dictionary mapping each resolution to its screenshot path.
    """
    comparator.driver.get(url)
    time.sleep(3)
    html = comparator.driver.page_source
    screenshot_paths = {}
    if resolutions:
        default_res = resolutions[0]
    else:
        default_res = (1280, 720)
    comparator.driver.set_window_size(*default_res)
    default_path = os.path.join(output_dir, f"{label}.png")
    comparator.driver.save_screenshot(default_path)
    screenshot_paths['default'] = default_path
    for (w, h) in resolutions:
        comparator.driver.set_window_size(w, h)
        filename = os.path.join(output_dir, f"{label}_{w}x{h}.png")
        # Give a short delay after resizing before taking screenshot.
        time.sleep(1)
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
    parser.add_argument("--sizes", help="Comma-separated list of resolutions (e.g., 1024x640,1366x768,1920x1080)")
    args = parser.parse_args()

    output_dir = args.output_dir
    os.makedirs(output_dir, exist_ok=True)

    # Parse resolutions if provided.
    resolutions = []
    if args.sizes:
        for res in args.sizes.split(','):
            try:
                w, h = map(int, res.lower().split('x'))
                resolutions.append((w, h))
            except Exception:
                print(f"Invalid resolution format: {res}")
    else:
        # If no sizes are provided, use a default resolution.
        resolutions = [(1280, 720)]

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

    # Compare screenshots at each resolution and save diff images.
    if not args.omit_screenshots:
        for (w, h) in resolutions:
            live_img = live_screenshots.get((w, h))
            dev_img = staging_screenshots.get((w, h))
            if live_img and dev_img:
                diff_output_filename = os.path.join(output_dir, f"screenshot_diff_{w}x{h}.png")
                if compare_screenshots(live_img, dev_img, diff_output_filename):
                    print(f"Screenshot diff for resolution {w}x{h} saved to: {diff_output_filename}")
                else:
                    print(f"No differences detected at resolution {w}x{h}.")
    comparator.quit_driver()


if __name__ == '__main__':
    main()
