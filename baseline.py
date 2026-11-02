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


def input_mismatches(manifest: dict, engine: str, sizes: list[str],
                     states_sha256: Optional[str], omit_screenshots: bool = False) -> list[str]:
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


def _run_urls(base_url: str, urls_file: Optional[str]) -> list[str]:
    return read_urls_file(urls_file) if urls_file else [base_url]


def _fail(message: str) -> None:
    logger.info(message)
    print(logging_config.COLOR_FAIL + message + logging_config.RESET_ALL)


def capture_baseline(processor, baseline_dir: str, base_url: str, urls_file: Optional[str],
                     states_file: Optional[str], cookie_file: Optional[str]) -> dict:
    """Capture every page once, labelled live, and write baseline.json."""
    if os.path.exists(baseline_dir):
        if not os.path.isfile(os.path.join(baseline_dir, MANIFEST_NAME)):
            raise BaselineError(f"{baseline_dir} exists and is not a baseline directory")
        # An old capture would otherwise mix with the new one.
        shutil.rmtree(os.path.join(baseline_dir, PAGES_DIR), ignore_errors=True)
    pages_dir = os.path.join(baseline_dir, PAGES_DIR)
    os.makedirs(pages_dir, exist_ok=True)

    pages = {}
    for url in _run_urls(base_url, urls_file):
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
            _fail(f"[FAIL] {key}: page failed to load: {info['error']}")

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
    return manifest


def _stored_file_names(page: dict) -> list[str]:
    names = list(page["files"])
    for state in page["states"]:
        names.extend(state.get("files", []))
    return names


def compare_baseline(processor, baseline_dir: str, manifest: dict, dev_base_url: str,
                     urls_file: Optional[str], cookie_file: Optional[str]) -> None:
    """Capture the dev side and compare it with the stored live files."""
    run_keys = {}
    for url in _run_urls(dev_base_url, urls_file):
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
        live_info = {"ok": True, "error": None, "states": page["states"]}

        logger.info(f"Comparing page: {url}")
        dev_capture = processor.capture_page(
            url=url, label="dev", output_dir=page_dir, cookie_file=cookie_file,
            capture_screenshots=not processor.args.omit_screenshots)
        processor.compare_captures(page["url"], url, page_dir,
                                   (stored_html, stored_screenshots, live_info), dev_capture)

    for key in manifest["pages"]:
        if key not in run_keys:
            _fail(f"[FAIL] {key}: missing in run")
