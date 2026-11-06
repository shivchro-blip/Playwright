"""The smart-capture recording browser: `python -m webapp.recorder_capture --url URL --out events.jsonl`.

Started by the app's recorder (one at a time, on the machine running the app). It opens Chromium on the URL with
the capture script in every page and writes each action, with the full description of its element, as a line of
JSON to --out. It ends when the last window is closed. Values typed into password fields are never written.

For automated tests only: AUTOPLAY_RECORDER_HEADLESS=true runs it headless and AUTOPLAY_RECORDER_DRIVE=<file.py>
calls that file's drive(page) instead of waiting for a person, then closes the browser.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path


def _drive(path: str, page) -> None:
    spec = importlib.util.spec_from_file_location("autoplay_recording_drive", path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    module.drive(page)
    page.wait_for_timeout(300)  # let the last events reach the recorder


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m webapp.recorder_capture")
    p.add_argument("--url", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--storage", default="")
    p.add_argument("--id-attribute", default="")
    args = p.parse_args(argv)

    from playwright.sync_api import Error, sync_playwright

    from webapp.services.capture import capture_script

    out = Path(args.out).open("a", encoding="utf-8")

    def record(_source, event) -> None:
        if isinstance(event, dict):
            out.write(json.dumps(event, ensure_ascii=False) + "\n")
            out.flush()

    headless = os.environ.get("AUTOPLAY_RECORDER_HEADLESS", "").lower() in ("1", "true", "yes")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(storage_state=args.storage or None, no_viewport=not headless)
        context.add_init_script(capture_script(args.id_attribute))
        context.expose_binding("__autoplayRecord", record)
        page = context.new_page()
        page.goto(args.url)
        drive = os.environ.get("AUTOPLAY_RECORDER_DRIVE", "")
        try:
            if drive:
                _drive(drive, page)
            else:
                while True:  # waiting on a page keeps the recorder's events flowing; popups are pages too
                    open_pages = [pg for pg in context.pages if not pg.is_closed()]
                    if not open_pages:
                        break
                    try:
                        open_pages[0].wait_for_event("close", timeout=0)
                    except Error:
                        pass
        finally:
            try:
                browser.close()
            except Error:
                pass
            out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
