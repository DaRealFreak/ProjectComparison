"""Store one capture as a baseline and compare a later capture against it."""
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from typing import Optional

import logging_config
from states import file_sha256

logger = logging.getLogger(__name__)

MANIFEST_NAME = "baseline.json"
MANIFEST_VERSION = 1
PAGES_DIR = "pages"
# The stored side always uses the label "live", so the existing file names apply.
STORED_LABEL = "live"


class BaselineError(ValueError):
    """The baseline directory or its inputs are unusable."""


def write_baseline_manifest(dir: str, manifest: dict) -> None:
    os.makedirs(dir, exist_ok=True)
    with open(os.path.join(dir, MANIFEST_NAME), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)


def read_baseline_manifest(dir: str) -> dict:
    path = os.path.join(dir, MANIFEST_NAME)
    try:
        with open(path, encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        raise BaselineError(f"cannot read baseline {path}: {error}") from error
    if not isinstance(manifest, dict) or manifest.get("version") != MANIFEST_VERSION:
        raise BaselineError(f"{path}: version must be {MANIFEST_VERSION}")
    return manifest


def input_mismatches(manifest: dict, engine: str, sizes: list[str], states_sha256: Optional[str],
                     omit_screenshots: bool = False, user_agent: Optional[str] = None) -> list[str]:
    """Name each run input that differs from the stored baseline."""
    problems = []
    if manifest.get("engine") != engine:
        problems.append(f"engine: baseline {manifest.get('engine')}, run {engine}")
    if manifest.get("sizes") != sizes:
        problems.append(f"sizes: baseline {','.join(manifest.get('sizes') or [])}, run {','.join(sizes)}")
    if manifest.get("statesSha256") != states_sha256:
        problems.append("states: the state file differs from the baseline")
    # A run without screenshots against a baseline with them would pass partially.
    if bool(manifest.get("omitScreenshots")) != omit_screenshots:
        stored = "without" if manifest.get("omitScreenshots") else "with"
        problems.append(f"screenshots: baseline {stored} screenshots, run "
                        f"{'without' if omit_screenshots else 'with'}")
    # Sites can serve other markup to another user agent.
    if manifest.get("userAgent") != user_agent:
        problems.append(f"user agent: baseline {manifest.get('userAgent') or 'default'}, "
                        f"run {user_agent or 'default'}")
    return problems


def read_urls_file(path: str) -> list[str]:
    """Read one URL per line; blank lines and # comments are skipped."""
    urls = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if text and not text.startswith("#") and text not in urls:
                    urls.append(text)
    except OSError as error:
        raise BaselineError(f"cannot read URL file {path}: {error}") from error
    return urls


def page_key(url: str, base_url: str) -> str:
    """Map a URL to its path below the base URL, the same prefix rule as get_dev_url."""
    if not url.startswith(base_url):
        raise BaselineError(f"{url} is not below {base_url}")
    return "/" + url[len(base_url):].lstrip("/")


def url_for_key(key: str, base_url: str) -> str:
    if key == "/":
        return base_url
    return base_url.rstrip("/") + key


def run_urls(base_url: str, urls_file: Optional[str]) -> list[str]:
    """Return the pages of a baseline run; refuse an empty list and URLs outside the base URL."""
    if not urls_file:
        return [base_url]
    urls = read_urls_file(urls_file)
    if not urls:
        raise BaselineError(f"{urls_file} lists no URL")
    for url in urls:
        page_key(url, base_url)
    return urls


def check_capture_target(baseline_dir: str, replace: bool) -> None:
    """Refuse a directory that is not a baseline, and an existing baseline unless replace is set."""
    if not os.path.exists(baseline_dir):
        return
    if not os.path.isfile(os.path.join(baseline_dir, MANIFEST_NAME)):
        raise BaselineError(f"{baseline_dir} exists and is not a baseline directory")
    if not replace:
        raise BaselineError(f"{baseline_dir} already holds a baseline; "
                            f"add --remove-previous_results to replace it")


def check_dirs_apart(baseline_dir: str, output_dir: str) -> None:
    """Refuse equal, nested or enclosing directories, so cleaning one cannot delete the other."""
    baseline_real = os.path.realpath(baseline_dir)
    output_real = os.path.realpath(output_dir)
    try:
        overlap = os.path.commonpath([baseline_real, output_real]) in (baseline_real, output_real)
    except ValueError:
        # Different drives on Windows.
        overlap = False
    if overlap:
        raise BaselineError(f"output directory {output_dir} overlaps the baseline directory {baseline_dir}: "
                            f"use two separate directories")


def _fail(message: str) -> None:
    logger.info(message)
    print(logging_config.COLOR_FAIL + message + logging_config.RESET_ALL)


def _warn(message: str) -> None:
    logger.warning(message)
    print(logging_config.COLOR_WARN + message + logging_config.RESET_ALL)


def capture_baseline(processor, baseline_dir: str, base_url: str, urls: list[str],
                     states_file: Optional[str], cookie_file: Optional[str], replace: bool = False) -> int:
    """Capture every page of urls (from run_urls) once, labelled live, and write baseline.json.

    Return the number of pages and states that failed.
    """
    check_capture_target(baseline_dir, replace)
    pages_dir = os.path.join(baseline_dir, PAGES_DIR)
    # An old capture would otherwise mix with the new one.
    shutil.rmtree(pages_dir, ignore_errors=True)
    os.makedirs(pages_dir, exist_ok=True)

    pages = {}
    failures = 0
    for url in urls:
        key = page_key(url, base_url)
        page_dir = processor.unique_output_dir(pages_dir, processor.sanitize_path(url))
        logger.info(f"Capturing baseline page: {url}")
        _, _, info = processor.capture_page(
            url=url, label=STORED_LABEL, output_dir=page_dir, cookie_file=cookie_file,
            capture_screenshots=not processor.args.omit_screenshots)
        pages[key] = {
            "url": url, "dir": os.path.basename(page_dir), "ok": info["ok"], "error": info["error"],
            "status": info["status"], "files": info["files"], "states": info["states"],
        }
        if not info["ok"]:
            failures += 1
            _fail(f"[FAIL] {key}: page failed to load: {info['error']}")
        for state in info["states"]:
            if state.get("outcome") == "failed":
                failures += 1
                _fail(f"[FAIL] {key}: state {state['id']} {state['size']} failed: "
                      f"{state.get('reason') or 'unknown reason'}")

    manifest = {
        "version": MANIFEST_VERSION,
        "baseUrl": base_url,
        "engine": processor.args.engine,
        "sizes": processor.size_names(),
        "omitScreenshots": bool(processor.args.omit_screenshots),
        "statesSha256": file_sha256(states_file) if states_file else None,
        "cookiesSha256": file_sha256(cookie_file) if cookie_file else None,
        "userAgent": processor.args.user_agent,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "pages": pages,
    }
    write_baseline_manifest(baseline_dir, manifest)
    logger.info(f"Baseline of {len(pages)} page(s) written to {baseline_dir}")
    return failures


def _stored_file_names(page: dict) -> list[str]:
    names = list(page["files"])
    for state in page["states"]:
        names.extend(state.get("files", []))
    return names


def compare_baseline(processor, baseline_dir: str, manifest: dict, dev_base_url: str,
                     urls: list[str], cookie_file: Optional[str]) -> None:
    """Capture the dev side of urls (from run_urls) and compare it with the stored live files."""
    # Other cookies can be on purpose (a new session), so this only warns.
    run_cookies = file_sha256(cookie_file) if cookie_file else None
    if manifest.get("cookiesSha256") != run_cookies:
        _warn("[WARN] cookies differ from the baseline")

    run_keys = {}
    for url in urls:
        run_keys[page_key(url, dev_base_url)] = url

    for key, url in run_keys.items():
        page = manifest["pages"].get(key)
        if page is None:
            _fail(f"[FAIL] {key}: missing in baseline")
            continue
        if not page.get("ok"):
            _fail(f"[FAIL] {key}: baseline page failed to load")
            continue
        page_dir = processor.unique_output_dir(processor.output_dir, processor.sanitize_path(url))
        stored_dir = os.path.join(baseline_dir, PAGES_DIR, page["dir"])
        try:
            for name in _stored_file_names(page):
                shutil.copy2(os.path.join(stored_dir, name), os.path.join(page_dir, name))
        except OSError as error:
            _fail(f"[FAIL] {key}: baseline file missing: {error.filename}")
            continue
        with open(os.path.join(page_dir, f"html_{STORED_LABEL}.html"), encoding="utf-8") as handle:
            stored_html = handle.read()
        stored_screenshots = {}
        if not processor.args.omit_screenshots:
            for res in processor.args.resolutions:
                name = "fullscreen" if isinstance(res, str) else f"{res[0]}x{res[1]}"
                stored_screenshots["fullscreen" if isinstance(res, str) else res] = os.path.join(
                    page_dir, f"{name}_{STORED_LABEL}.png")
        live_info = {"ok": True, "error": None, "status": page.get("status"), "states": page["states"]}

        logger.info(f"Comparing page: {url}")
        dev_capture = processor.capture_page(
            url=url, label="dev", output_dir=page_dir, cookie_file=cookie_file,
            capture_screenshots=not processor.args.omit_screenshots)
        processor.compare_captures(page["url"], url, page_dir,
                                   (stored_html, stored_screenshots, live_info), dev_capture, key)

    for key in manifest["pages"]:
        if key not in run_keys:
            _fail(f"[FAIL] {key}: missing in run")
