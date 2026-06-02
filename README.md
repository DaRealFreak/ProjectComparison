# Project Comparison

A command-line tool for **visual and HTML regression testing** between two versions of a
website — typically a **live** (production) site and a **dev** (staging) site. For every
page it visits, it captures the rendered HTML and screenshots, diffs both against the other
environment, and writes the results into a structured folder so you can see exactly what
changed.

It drives a real headless **Chrome** (via Selenium) and downloads a matching
[Chrome for Testing](https://googlechromelabs.github.io/chrome-for-testing/) build
automatically — you don't need Chrome or chromedriver installed beforehand.

## Features

- **HTML diffing** — captures the fully rendered DOM of each page on both environments and
  produces a unified diff.
- **Screenshot diffing** — captures screenshots at one or more resolutions and computes the
  average per-pixel difference, saving a visual diff image when pages differ.
- **Full-page screenshots** — optional `fullscreen` capture of the entire scrollable page
  (via the Chrome DevTools Protocol), not just the visible viewport.
- **Recursive crawling** — optionally follows internal links and compares each discovered
  page, mapping live URLs to their dev equivalents automatically.
- **Authentication support** — per-environment cookies (JSON) and/or HTTP Basic Auth.
- **Self-signed / local TLS** — accepts untrusted certificates so local and dev
  environments work without certificate setup.
- **Auto-managed browser** — downloads and caches the correct Chrome + chromedriver for
  your platform.
- **Pass/Fail reporting** — color-coded `[PASS]`/`[FAIL]` summary per page and overall.

## Requirements

- **Python 3.9+**
- **`git`** available on your `PATH` — HTML diffing uses `git diff --no-index`.
- **Internet access on first run** — to download Chrome for Testing into `tmp/` (cached for
  subsequent runs).

> **Platform support:** Works out of the box on **Windows**, **Linux**, and **macOS**
> (Intel and Apple Silicon). On Linux, headless Chrome may additionally require system
> libraries (e.g. `libnss3`, `libgbm`, `libasound2`, fonts) on minimal/server distros.

## Installation

```bash
# clone, then from the project root:
python -m venv .venv

# Windows (PowerShell)
.venv\Scripts\Activate.ps1
# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt
```

## Usage

```
python main.py <url_live> <url_dev> [options]
```

### Positional arguments

| Argument    | Description           |
|-------------|-----------------------|
| `url_live`  | The live / production URL. |
| `url_dev`   | The dev / staging URL.     |

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `--output-dir DIR` | `results` | Directory where results are written. |
| `--remove-previous_results` | off | Delete the output directory before running. |
| `--version VER` | `latest` | Chrome for Testing version to download (e.g. `131.0.6778.85`). |
| `--sizes LIST` | `1920x1080` | Comma-separated resolutions, e.g. `1024x640,1920x1080,fullscreen`. |
| `--threshold N` | `0` | Average per-pixel difference above which a screenshot is considered a failure. |
| `--always-generate-diff` | off | Always write a screenshot diff image, even when below threshold. |
| `--omit-screenshots` | off | Skip screenshots entirely; only diff HTML. |
| `--user-agent UA` | — | Use a custom browser user agent. |
| `--live-cookies FILE` | — | JSON cookie file applied to the live URL. |
| `--dev-cookies FILE` | — | JSON cookie file applied to the dev URL. |
| `--htuser USER` | — | HTTP Basic Auth username (applied to both URLs). |
| `--htpass PASS` | — | HTTP Basic Auth password. |
| `--recursive` | off | Follow internal links and compare discovered pages. |
| `--recursive-max-level N` | `1` | Maximum crawl depth when `--recursive` is set. |
| `--log-level LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`. |

## Example run configurations

**1. Quick single-page comparison** (default 1920x1080 screenshot + HTML):

```bash
python main.py https://www.example.com https://dev.example.com
```

**2. Multiple resolutions plus a full-page capture** — useful for responsive checks:

```bash
python main.py https://www.example.com https://dev.example.com \
  --sizes 375x667,768x1024,1920x1080,fullscreen
```

**3. Recursive crawl of the whole site** to depth 2, starting from a clean output folder:

```bash
python main.py https://www.example.com https://dev.example.com \
  --recursive --recursive-max-level 2 --remove-previous_results
```

**4. Authenticated comparison via cookies** (e.g. logged-in pages with different sessions
per environment):

```bash
python main.py https://www.example.com https://dev.example.com \
  --live-cookies cookies_live.json --dev-cookies cookies_dev.json
```

**5. Dev site behind HTTP Basic Auth** (e.g. an `.htaccess`-protected staging domain):

```bash
python main.py https://www.example.com https://dev.example.com \
  --htuser staging --htpass s3cret
```

**6. Tolerate minor rendering noise** (anti-aliasing, font hinting) by raising the pixel
threshold, and force diff images so you can eyeball them:

```bash
python main.py https://www.example.com https://dev.example.com \
  --threshold 2.5 --always-generate-diff
```

**7. HTML-only comparison** (fast, no browser screenshots):

```bash
python main.py https://www.example.com https://dev.example.com --omit-screenshots
```

**8. Pin a Chrome version with verbose logging and a custom output directory:**

```bash
python main.py https://www.example.com https://dev.example.com \
  --version 131.0.6778.85 --output-dir results/run-2026-06-02 --log-level DEBUG
```

## Output

Each compared page gets its own subfolder under the output directory, named after the URL
path (with `/` replaced by `_`; duplicates get a numeric suffix). A typical page folder
contains:

```
results/
└── products_shoes/
    ├── html_live.html              # rendered DOM, live
    ├── html_dev.html               # rendered DOM, dev
    ├── html-difference.diff        # unified HTML diff (empty = identical)
    ├── 1920x1080_live.png          # screenshot, live
    ├── 1920x1080_dev.png           # screenshot, dev
    ├── 1920x1080_screenshot_diff.png   # visual diff (only if over threshold)
    ├── fullscreen_live.png
    ├── fullscreen_dev.png
    └── fullscreen_screenshot_diff.png
```

A page is reported as **`[FAIL]`** when the HTML diff is non-empty **or** any screenshot's
average pixel difference exceeds `--threshold`; otherwise it's **`[PASS]`**.

## Cookie file format

Cookie files are JSON arrays of [Selenium cookie objects](https://www.selenium.dev/documentation/webdriver/interactions/cookies/).
Cookies are applied after an initial navigation (so the domain is set) and the page is then
reloaded. Example:

```json
[
  {
    "name": "session",
    "value": "abc123",
    "domain": ".example.com",
    "path": "/",
    "secure": true,
    "httpOnly": true
  }
]
```

The repository includes empty `cookies_live.json` and `cookies_dev.json` as starting points.

## How it works

1. **`chrome_installer.py`** detects your platform and downloads the matching Chrome +
   chromedriver from Chrome for Testing into `tmp/` (cached).
2. **`site_comparator.py`** runs a headless Selenium Chrome session and handles screenshots
   (windowed and full-page via CDP) and cookie loading.
3. **`image_comparator.py`** computes the mean per-pixel difference between two screenshots
   using Pillow and writes a diff image when they differ.
4. **`main.py`** (`PageDiffProcessor`) orchestrates everything per page — capture, HTML
   diff, screenshot diff, pass/fail logging — and drives the optional recursive crawl.
```
