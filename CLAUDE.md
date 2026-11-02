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

```bash
# Playwright engine, interaction states and baselines (see "Playwright engine" below)
python main.py <live> <dev> --engine playwright --states states.json
python main.py --capture-baseline baselines/site <url> --engine playwright
python main.py --compare-baseline baselines/site <url> --engine playwright

# Tests
python -m unittest discover -s tests -p "test_*.py"                       # unit tests, no Node needed
python -m unittest tests.test_states tests.test_playwright_engine tests.test_baseline
(cd playwright_capture && node --test)                                    # helper tests, need Node + Chromium
python tests/smoke_test.py                                                # Selenium end to end
python tests/playwright_smoke_test.py                                     # Playwright end to end
python tests/baseline_smoke_test.py                                       # baseline end to end
```

There is **no linter configured** (only a PyCharm `.idea/`). Tests are stdlib `unittest`
(`tests/test_*.py`) plus `node --test` for the helper; the smoke tests run the real tool.
CI (`.github/workflows/ci.yml`) runs the unit tests and the Selenium smoke test on Linux,
Windows and macOS, and the `playwright` job (Linux only) runs the Node tests, the Playwright
unit tests and both Playwright smoke tests. The unit tests mock `node`/`npm`, so they need
neither. Don't claim to have run a suite you did not run.

## Runtime requirements (non-obvious)

- **Playwright engine only: Node >= 20.** `--engine playwright` checks `node --version` and
  runs `npm ci --prefix playwright_capture` once if `playwright_capture/node_modules/playwright`
  is missing. Browsers are not downloaded by the tool: they must already exist (in DDEV they
  are baked into `PLAYWRIGHT_BROWSERS_PATH`; elsewhere run `npx playwright install chromium`
  in `playwright_capture/`). A missing Node or a failed `npm ci` exits with code 1.

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
- **Non-HTML link filtering.** Before recursing into a discovered link, `should_crawl`
  issues a `HEAD` request and only crawls `text/html` / `application/xhtml+xml` responses;
  anything else (PDFs, images, archives, …) is skipped. When the `Content-Type` can't be
  determined (HEAD blocked, timeout, missing header) it falls back to the
  `DOWNLOADABLE_EXTENSIONS` blocklist via `is_downloadable_file`. `--include-files` bypasses
  the check. The HEAD probe uses `verify=False` (to match Chrome's cert handling) and
  reuses `--htuser`/`--htpass`. Skipped links are added to `visited` so they aren't
  re-probed on other pages.
- **Output layout.** Each page gets `results/<sanitized-url-path>/` (path `/` → `_`,
  de-duplicated with numeric suffixes). HTML diffs are saved as `html-difference.diff` per
  page; a page is an HTML "fail" when that file is non-empty.
- **Cookies** are JSON arrays of Selenium cookie dicts (`cookies_live.json`,
  `cookies_dev.json` are committed examples). They're applied *after* an initial navigation
  so the domain is set, then the page is reloaded.
- **HTTP Basic Auth** is injected directly into the URL (`inject_auth`,
  `https://user:pass@host/...`), URL-encoding credentials and stripping any existing auth.

## Playwright engine, states and baselines

`--engine playwright` (default `selenium`) replaces step 2 above: `playwright_engine.py`
writes a request file and runs the Node helper `playwright_capture/capture.mjs` once per
side and page. The helper writes the same file names as the Selenium path
(`html_<label>.html`, `<WxH>_<label>.png`, `fullscreen_<label>.png`) plus the state files
below, and prints one JSON result. Python does all diffing. Cookies are converted from the
Selenium format; credentials and cookies are never printed or stored in a baseline.
Every screenshot size is a fresh page load, so the result does not depend on the order of
`--sizes`; the HTML and the HTTP status come from the first `WxH`, and `fullscreen` is
taken at a 1920x1080 viewport, as in the Selenium path. Each load (base size or state and
width) runs in a new browser context with the same cookies, credentials and user agent,
so cookies and storage that one load sets do not reach the next.

**Pin.** `playwright_capture/package.json` pins `playwright` to
`1.64.0-alpha-1790635538000`. That is the version the typo3-upgrade skill's DDEV
agent-tooling layer bakes into the container, with its browsers in
`PLAYWRIGHT_BROWSERS_PATH`. Keep both in step, or the baked browsers will not match the
package. The helper must not override that variable. Do not bump the pin alone.

**State file** (`--states FILE`, validated by `states.py`, needs `--engine playwright`):

```json
{"version": 1, "states": [
  {"id": "menu-open", "when": ".nav-toggle", "sizes": ["375x667"],
   "steps": [{"action": "click", "selector": ".nav-toggle", "index": 0},
             {"action": "waitFor", "selector": ".nav.open"}]}
]}
```

- `id`: `^[a-z0-9][a-z0-9-]*$`, unique. `steps`: non-empty. `when` (optional CSS selector):
  the state applies only if it matches at least one element. `sizes` (optional list of
  `WxH`): widths for this state; default is the run's `--sizes`. `fullscreen` never applies.
- Actions: `click`, `hover`, `focus`, `waitFor` need `selector` (optional `index`, default 0);
  `press` needs `key`; `wait` needs `ms` (1 to 10000).
- Each state and width is captured on a fresh page load. Files:
  `state-<id>_<WxH>_html_<label>.html` and `state-<id>_<WxH>_<label>.png`; diffs are
  `state-<id>_<WxH>_html-difference.diff` and `state-<id>_<WxH>_screenshot_diff.png`.

**Outcomes.** The helper reports one entry per state and width: `absent` (`when` matched
nothing), `captured`, or `failed` (a step errored or timed out, with a reason). The
expected list comes from the state file, not from what the helper returned, so a crashed
side cannot pass. Rules (`compare_state_outcomes`): `captured` on both sides is diffed
(HTML and screenshot); `absent` on both sides is "not applicable" and only logged; every
other combination, including `failed` and a missing entry, is a `[FAIL]` line
(`[FAIL] state <id> <size>: <outcomes> ...`). A state failure fails the page.

**Baselines.** `--capture-baseline DIR <url>` captures the pages once and stores them as
the "live" side; `--compare-baseline DIR <url>` captures the current site as "dev" and
compares. Both need `--engine playwright`, take exactly one URL (the base URL) and accept
`--urls FILE` (one URL per line, `#` comments; each must be below the base URL) and
`--dev-cookies` only. Layout: `DIR/baseline.json` plus `DIR/pages/<page-dir>/` with the
stored files. `baseline.json` (version 1) holds `baseUrl`, `engine`, `sizes`,
`omitScreenshots`, `statesSha256`, `cookiesSha256`, `userAgent`, `createdAt` and per-page
`url`, `dir`, `ok`, `error`, `status`, `files`, `states`. Pages are keyed by their path
below the base URL, so the base URL may differ between capture and compare. A compare
**refuses and exits 2** when engine, sizes, the sha256 of the state file or the screenshot
mode (`--omit-screenshots`) differ ("the run differs from the baseline: ..."). A page in
the baseline but not in the run, or the reverse, is a `[FAIL]`, as is a missing stored file.

`--list-capabilities` prints `{"engines": ["selenium", "playwright"], "states": 1, "baseline": 1}`
and exits; other tools use it to detect support.

## Conventions

- `--sizes` is a comma-separated list; each entry is either `WxH` or the literal
  `fullscreen`. Internally resolutions are `(w, h)` tuples or the string `"fullscreen"` —
  preserve this dual representation when touching screenshot/diff code.
- The clean-output flag is spelled `--remove-previous_results` (mixed dash/underscore,
  unlike the other flags) and deletes the entire `--output-dir` before running.
- `results/`, `tmp/`, `__pycache__/`, and `*.diff` are gitignored.
