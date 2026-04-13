#!/usr/bin/env python3
from __future__ import annotations

import argparse
import platform
import time
import urllib.parse
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Callable, Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.platform_utils import get_config_path
from plugins.os_bridge.actions.apps import open_app, open_url
from plugins.os_bridge.actions.keyboard import hotkey, press, save_file, type_text
from plugins.os_bridge.actions.screen import read_screen


ASCII_TEXT = "E-Pet OS bridge test: ASCII typing works."
UNICODE_TEXT = "E-Pet OS bridge test: नमस्ते 😺"
SAVE_FILENAME = "epet-osbridge-smoke-test.txt"


def build_test_page() -> str:
    html = """
    <html>
      <head>
        <meta charset="utf-8" />
        <title>E-Pet OS Bridge Smoke Test</title>
        <style>
          body { font-family: sans-serif; margin: 24px; background: #f6f3ea; color: #222; }
          h1 { margin-top: 0; }
          textarea {
            width: 96vw;
            height: 70vh;
            font-size: 22px;
            padding: 16px;
            box-sizing: border-box;
          }
        </style>
      </head>
      <body>
        <h1>E-Pet OS Bridge Smoke Test</h1>
        <p>Click the text area if focus is lost.</p>
        <textarea autofocus spellcheck="false"></textarea>
      </body>
    </html>
    """
    return "data:text/html;charset=utf-8," + urllib.parse.quote(html)


def default_browser_label() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "default browser (macOS)"
    if system == "windows":
        return "default browser (Windows)"
    return "default browser (Linux)"


def default_editor_name() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "TextEdit"
    if system == "windows":
        return "Notepad"
    return "gedit"


def prompt(message: str) -> None:
    input(f"\n{message}\nPress Enter to continue...")


def show_screen_read() -> None:
    try:
        result = read_screen()
    except Exception as exc:
        print(f"  OCR/read_screen unavailable: {exc}")
        return

    text = str(result.get("text", "")).strip()
    if not text:
        print("  OCR text: <empty>")
        return

    print("  OCR text:")
    for line in text.splitlines():
        print(f"    {line}")


@dataclass
class StepResult:
    name: str
    ok: bool
    detail: str


def run_step(name: str, action: Callable[[], Any], *, confirm: bool = True, pause: float = 1.5) -> StepResult:
    print(f"\n== {name} ==")
    try:
        result = action()
        if isinstance(result, dict) and "text" in result:
            text = str(result.get("text", "")).strip().splitlines()
            preview = " | ".join(text[:3]) if text else "<empty>"
            detail = f"ok: read_screen -> {preview}"
        else:
            detail = "ok" if result is None else f"ok: {result!r}"
        print(f"  {detail}")
        if pause > 0:
            time.sleep(pause)
        if confirm:
            show_screen_read()
            prompt("Visually confirm the step above worked as expected")
        return StepResult(name=name, ok=True, detail=detail)
    except Exception as exc:
        print(f"  failed: {exc}")
        return StepResult(name=name, ok=False, detail=str(exc))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Interactive smoke test for the E-Pet OS bridge actions."
    )
    parser.add_argument(
        "--url",
        default=build_test_page(),
        help="URL to open for the typing tests. Defaults to an in-memory data URL.",
    )
    parser.add_argument(
        "--filename",
        default=SAVE_FILENAME,
        help="Filename to type into the save dialog.",
    )
    parser.add_argument(
        "--ascii-text",
        default=ASCII_TEXT,
        help="ASCII text to type into the test page.",
    )
    parser.add_argument(
        "--unicode-text",
        default=UNICODE_TEXT,
        help="Unicode text to type into the test page.",
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=1.5,
        help="Seconds to wait after each action before confirmation.",
    )
    args = parser.parse_args()

    print("E-Pet OS bridge smoke test")
    print(f"Config file: {get_config_path()}")
    print("This script will open a browser tab and drive typing, hotkeys, save, and OCR.")
    print("If any step fails, the script will report the exception and continue to the end.")

    results: list[StepResult] = []

    results.append(
        run_step(
            f"Open {default_editor_name()}",
            lambda: open_app(default_editor_name()),
            pause=args.pause,
        )
    )

    results.append(
        run_step(
            "Open test page",
            lambda: open_url(args.url),
            pause=args.pause,
        )
    )

    print("\nFocus should now be inside the text area on the test page.")
    prompt("If the cursor is not inside the text area, click it once before continuing")

    results.append(
        run_step(
            "Type ASCII text",
            lambda: type_text(args.ascii_text),
            pause=args.pause,
        )
    )

    system = platform.system().lower()
    select_all_combo = ("command", "a") if system == "darwin" else ("ctrl", "a")
    results.append(
        run_step(
            "Hotkey select-all",
            lambda: hotkey(*select_all_combo),
            pause=args.pause,
        )
    )

    results.append(
        run_step(
            "Replace selection with Unicode text",
            lambda: type_text(args.unicode_text),
            pause=args.pause,
        )
    )

    results.append(
        run_step(
            "Press Enter",
            lambda: press("enter"),
            pause=args.pause,
        )
    )

    results.append(
        run_step(
            "Save file",
            lambda: save_file(args.filename),
            pause=max(args.pause, 2.0),
        )
    )

    results.append(
        run_step(
            "Final screen read",
            lambda: read_screen(),
            confirm=False,
            pause=0.5,
        )
    )

    print("\nSummary")
    for item in results:
        status = "PASS" if item.ok else "FAIL"
        print(f"  {status}: {item.name} ({item.detail})")

    failed = [item for item in results if not item.ok]
    if failed:
        print("\nOne or more steps failed. The OS bridge may still be partially usable, but this run was not clean.")
        return 1

    print("\nAll OS bridge smoke-test steps completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
