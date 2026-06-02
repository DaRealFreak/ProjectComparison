# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A CLI tool that compares a **live** and a **dev** version of a website and reports
regressions. For each page it captures the rendered HTML and screenshots (at one or more
resolutions), diffs both, and writes the results into a structured `results/` tree. It can
optionally crawl internal links recursively. Headless Chrome (Chrome for Testing) is
downloaded automatically — nothing browser-related needs to be installed by hand.

## Commands

```bash
# Setup (Windows PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Basic run: compare two URLs at 1920x1080 (default)
python main.py https://live.example.com https://dev.example.com

# Multiple resolutions + a full-page capture
python main.py <live> <dev> --sizes 1024x640,1920x1080,fullscreen

# Recursive crawl of internal links, depth 2, clean output first
python main.py <live> <dev> --recursive --recursive-max-level 2 --remove-previous_results

# Authenticated pages: cookies per environment and/or HTTP Basic Auth
python main.py <live> <dev> --live-cookies cookies_live.json --dev-cookies cookies_dev.json
python main.py <live> <dev> --htuser user --htpass secret
```

There is **no test suite and no linter configured** in this repo (only a PyCharm `.idea/`).
Don't claim to have run either; if you add test infrastructure, surface that as its own change.

## Runtime requirements (non-obvious)

- **`git` must be on PATH at runtime.** HTML diffing shells out to `git diff --no-index`
  (`main.py`), not a Python diff library.
- **Network access on first run.** `ChromeInstaller` downloads Chrome + chromedriver from
  the Chrome for Testing endpoints into `tmp/` (cached across runs). `--version` pins a
  build; default `latest` queries the LATEST_RELEASE_STABLE endpoint.

## Architecture

The flow is a single pass orchestrated by `PageDiffProcessor` in `main.py`:

1. `ChromeInstaller` (`chrome_installer.py`) resolves the platform via
   `utils.detect_platform()` (maps to Chrome-for-Testing strings like `win64`, `linux64`,
   `mac-arm64`) and downloads/extracts the matching chrome + chromedriver into `tmp/`.
2. `SiteComparator` (`site_comparator.py`) wraps a **headless** Selenium Chrome session.
   It owns screenshots and cookie loading. Two screenshot paths exist:
   - `take_screenshot(w, h, ...)` — sets the window size and uses Selenium's screenshot.
   - `take_fullscreen_screenshot(...)` — uses the **Chrome DevTools Protocol**
     (`Page.getLayoutMetrics` + `Page.captureScreenshot` with `captureBeyondViewport`) to
     capture the entire page, not just the viewport.
   It launches with `--ignore-certificate-errors` / `acceptInsecureCerts` so self-signed and
   local/dev TLS works, and uses a dedicated profile under `tmp/chrome-data`.
3. `ImageComparator` (`image_comparator.py`) computes the **mean per-pixel difference** with
   PIL (`ImageChops.difference` → `ImageStat`). A page "fails" on images when this average
   exceeds `--threshold`; a diff PNG is written only when over threshold (or with
   `--always-generate-diff`).
4. `PageDiffProcessor.process_page` ties it together per URL: capture live, capture dev,
   write `html_live.html` / `html_dev.html`, run the git HTML diff, compare each requested
   resolution, then log a per-page `[PASS]`/`[FAIL]` line (colored via `logging_config`).

### Key cross-file concepts

- **Live↔dev URL mapping during recursion.** Crawling only follows *live* internal links
  (`extract_internal_links`, same-domain only). The corresponding dev URL is derived by
  `get_dev_url` via **prefix substitution**: it strips the live base URL prefix and prepends
  the dev base. This assumes live and dev share identical path structures below their roots.
- **Output layout.** Each page gets `results/<sanitized-url-path>/` (path `/` → `_`,
  de-duplicated with numeric suffixes). HTML diffs are saved as `html-difference.diff` per
  page; a page is an HTML "fail" when that file is non-empty.
- **Cookies** are JSON arrays of Selenium cookie dicts (`cookies_live.json`,
  `cookies_dev.json` are committed examples). They're applied *after* an initial navigation
  so the domain is set, then the page is reloaded.
- **HTTP Basic Auth** is injected directly into the URL (`inject_auth`,
  `https://user:pass@host/...`), URL-encoding credentials and stripping any existing auth.

## Conventions

- `--sizes` is a comma-separated list; each entry is either `WxH` or the literal
  `fullscreen`. Internally resolutions are `(w, h)` tuples or the string `"fullscreen"` —
  preserve this dual representation when touching screenshot/diff code.
- The clean-output flag is spelled `--remove-previous_results` (mixed dash/underscore,
  unlike the other flags) and deletes the entire `--output-dir` before running.
- `results/`, `tmp/`, `__pycache__/`, and `*.diff` are gitignored.
