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
  page, mapping live URLs to their dev equivalents automatically. Links to downloadable /
  non-HTML files (PDFs, images, archives, …) are skipped by default — detected via the
  link's `Content-Type`, falling back to its file extension — and can be re-included with
  `--include-files`.
- **Authentication support** — per-environment cookies (JSON) and/or HTTP Basic Auth.
- **Self-signed / local TLS** — accepts untrusted certificates so local and dev
  environments work without certificate setup.
- **Auto-managed browser** — downloads and caches the correct Chrome + chromedriver for
  your platform.
- **Playwright engine, interaction states and baselines** — optionally capture with
  Playwright, also capture states such as an open menu, and compare a site against a stored
  baseline when the original installation is gone (see below).
- **Pass/Fail reporting** — color-coded `[PASS]`/`[FAIL]` summary per page and overall.

## Requirements

- **Python 3.9+**
- **`git`** available on your `PATH` — HTML diffing uses `git diff --no-index`.
- **Internet access on first run** — to download Chrome for Testing into `tmp/` (cached for
  subsequent runs).

- **Only for `--engine playwright`:** **Node.js 20+** and `npm`. On first use the tool runs
  `npm ci` in `playwright_capture/` itself; a missing or too old Node or a failed `npm ci`
  exits with code 2. Browsers are not downloaded by the tool: run
  `npx playwright install chromium` in `playwright_capture/` (inside the typo3-upgrade DDEV
  layer they are already installed).

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
| `--remove-previous_results` | off | Delete the output directory before running. With `--capture-baseline`: replace an existing baseline. |
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
| `--recursive` | off | Follow internal links and compare discovered pages. Not with the baseline modes (exit code 2). |
| `--recursive-max-level N` | `1` | Maximum crawl depth when `--recursive` is set. |
| `--include-files` | off | Also crawl links to downloadable/non-HTML files (PDFs, images, archives, …). By default these are skipped. |
| `--engine ENGINE` | `selenium` | `selenium` or `playwright`. |
| `--states FILE` | — | JSON file with interaction states (needs `--engine playwright`). |
| `--capture-baseline DIR` | — | Capture one URL (or every URL of `--urls`) into `DIR` as a baseline (needs `--engine playwright`). |
| `--compare-baseline DIR` | — | Compare a URL (or every URL of `--urls`) against the baseline in `DIR` (needs `--engine playwright`). |
| `--urls FILE` | — | One URL per line for the baseline modes; `#` starts a comment. |
| `--list-capabilities` | off | Print the supported engines and features as JSON and exit. |
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

## Playwright engine, interaction states and baselines

`--engine playwright` captures with [Playwright](https://playwright.dev) instead of
Selenium. It writes the same files and uses the same pass/fail rules. The Node helper in
`playwright_capture/` pins `playwright` to `1.64.0-alpha-1790635538000`, the version the
typo3-upgrade skill bakes into its DDEV container, so the browsers installed there match
without a download. Keep the two in step.

```bash
python main.py https://www.example.com https://dev.example.com --engine playwright
```

The Playwright engine also compares the HTTP status of each page. A different status is
`[FAIL] <path>: status <live> on live, <dev> on dev`. The same status of 400 or more on both
sides prints `[WARN] <path>: status <n> on both sides`, and the page is still compared (error
pages are compared on purpose). `<path>` is the path below the base URL. In a baseline
comparison the stored status is the live side.

### Interaction states

`--states states.json` also captures pages after interactions. Format (version 1):

```json
{
  "version": 1,
  "states": [
    {
      "id": "menu-open",
      "when": ".nav-toggle",
      "sizes": ["375x667"],
      "steps": [
        {"action": "click", "selector": ".nav-toggle"},
        {"action": "waitFor", "selector": ".nav.open"}
      ]
    }
  ]
}
```

- `id` — lowercase letters, digits and `-`; unique. `steps` — at least one.
- `when` (optional) — CSS selector; the state is captured only if it matches an element.
- `sizes` (optional) — list of `WxH`; default is the run's `--sizes`. `fullscreen` is
  never used for states, so a state without `sizes` in a run whose `--sizes` has no `WxH`
  entry is refused at startup (exit code 2).
- Actions: `click`, `hover`, `focus`, `waitFor` (need `selector`, optional `index`, default
  0), `press` (needs `key`), `wait` (needs `ms`, 1 to 10000).

Every state is captured at every width on a fresh page load in a new browser context, on both
sides, so cookies or storage set by the steps of one state do not change the next. Each one has one
outcome per side: `absent` (`when` matched nothing), `captured`, or `failed` (a step failed
or timed out). Rules:

| Live | Dev | Result |
|------|-----|--------|
| `captured` | `captured` | HTML and screenshot are diffed |
| `absent` | `absent` | not applicable (only logged) |
| anything else, including a missing capture | | `[FAIL] state <id> <size>: ...` |

A state captured on both sides whose HTML or screenshot file is missing is
`[FAIL] state <id> <size>: missing file <name>`. An error from `git diff` fails the page or
the state; it never counts as "no difference".

### Baselines

A baseline stores one capture so you can compare against it after the original
installation is gone (for example before and after an upgrade):

```bash
# Before the change: capture the current site
python main.py --capture-baseline baselines/site https://www.example.com \
  --engine playwright --sizes 375x667,1920x1080 --states states.json --urls urls.txt

# After the change: compare the new site against it
python main.py --compare-baseline baselines/site https://dev.example.com \
  --engine playwright --sizes 375x667,1920x1080 --states states.json --urls urls.txt
```

Both modes need `--engine playwright` and exactly one URL (the base URL). `--urls FILE`
lists more pages, one per line, each below the base URL; blank lines and `#` comments are
skipped. Pages are matched by their path below the base URL. The baseline directory holds
`baseline.json` (engine, sizes, SHA-256 of the state file and cookie file, screenshot mode,
and per page the files and state outcomes; no cookie values) and `pages/<page>/` with the
stored files. The comparison **refuses to run (exit code 2)** when the engine, the sizes,
the state file, the screenshot mode (`--omit-screenshots`) or the user agent (`--user-agent`)
differ from the baseline. A different cookie file only prints `[WARN] cookies differ from the
baseline` and the comparison continues. A page
missing on either side, a baseline page that failed to load, and a missing stored file are
reported as `[FAIL]`. Use `--dev-cookies` for cookies in the baseline modes.

Both modes also **refuse to run (exit code 2)** when the `--urls` file lists no URL or lists
a URL that is not below the base URL. A capture refuses when `DIR` already holds a
baseline; add `--remove-previous_results` to replace it. These checks run before anything
in `DIR` is deleted.

A capture **exits with code 1** when a page failed to load or a state failed
(`[FAIL] <page>: state <id> <size> failed: <reason>`). The baseline is still written, so a
later comparison reports the same failure.

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

With `--states`, each captured state adds `state-<id>_<WxH>_html_live.html`,
`state-<id>_<WxH>_html_dev.html`, `state-<id>_<WxH>_live.png`, `state-<id>_<WxH>_dev.png`
and, when they differ, `state-<id>_<WxH>_html-difference.diff` and
`state-<id>_<WxH>_screenshot_diff.png`.

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
