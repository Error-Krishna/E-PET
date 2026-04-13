from __future__ import annotations

import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _is_likely_gui_shell() -> bool:
    # macOS Qt GUI apps are much more reliable from a real Terminal/iTerm
    # session than from an integrated shell with TERM=dumb or an XPC service.
    if sys.platform != "darwin":
        return True
    if os.environ.get("TERM") == "dumb":
        return False
    xpc_service = os.environ.get("XPC_SERVICE_NAME", "")
    if xpc_service.startswith("application.com.microsoft.VSCode"):
        return False
    return True


def _preflight_environment() -> None:
    if os.environ.get("EPET_GUI_FORCE_LAUNCH"):
        return
    if not _is_likely_gui_shell():
        print(
            "E-Pet GUI cannot start from this shell session on macOS.\n"
            "Open a regular Terminal/iTerm window from the Dock, then run:\n"
            f"  cd {ROOT}\n"
            "  source .venv/bin/activate\n"
            "  python epet_gui/main.py\n"
            "\n"
            f"Detected TERM={os.environ.get('TERM')!r} and "
            f"XPC_SERVICE_NAME={os.environ.get('XPC_SERVICE_NAME')!r}."
        )
        raise SystemExit(1)


def _ensure_project_venv_python() -> None:
    if os.environ.get("EPET_GUI_REEXECED") == "1":
        return
    expected = ROOT / ".venv" / "bin" / "python"
    try:
        current_prefix = Path(sys.prefix).resolve()
        expected_prefix = expected.parent.parent.resolve()
        if expected.exists() and current_prefix != expected_prefix:
            os.environ["EPET_GUI_REEXECED"] = "1"
            os.execv(str(expected), [str(expected), *sys.argv])
    except OSError:
        # If re-exec fails, continue with the current interpreter.
        pass


_ensure_project_venv_python()
_preflight_environment()


def _find_qt_plugin_root() -> Path | None:
    py_minor = f"python{sys.version_info.major}.{sys.version_info.minor}"
    candidates = [
        Path(sys.executable).resolve().parent.parent / "lib" / py_minor / "site-packages" / "PySide6" / "Qt" / "plugins",
        Path(sys.prefix) / "lib" / py_minor / "site-packages" / "PySide6" / "Qt" / "plugins",
        ROOT / ".venv" / "lib" / py_minor / "site-packages" / "PySide6" / "Qt" / "plugins",
    ]
    for candidate in candidates:
        if (candidate / "platforms").exists():
            return candidate
    return None


def _write_qt_conf(plugin_root: Path) -> None:
    # Qt reads qt.conf from the executable directory before QApplication is created.
    # That is more reliable on macOS than relying only on environment variables.
    prefix = plugin_root.parent
    contents = (
        "[Paths]\n"
        f"Prefix = {prefix}\n"
        "Plugins = plugins\n"
    )
    candidates = [
        ROOT / ".venv" / "bin" / "qt.conf",
        Path(sys.executable).resolve().parent / "qt.conf",
    ]
    for qt_conf in candidates:
        try:
            if not qt_conf.parent.exists():
                continue
            if not qt_conf.exists() or qt_conf.read_text(encoding="utf-8") != contents:
                qt_conf.write_text(contents, encoding="utf-8")
            return
        except OSError:
            continue


def _configure_qt_paths():
    plugin_root = _find_qt_plugin_root()
    if plugin_root is None:
        return None
    # Set these unconditionally so an inherited empty value cannot disable Qt's
    # plugin discovery on macOS.
    os.environ["QT_PLUGIN_PATH"] = str(plugin_root)
    os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(plugin_root / "platforms")
    os.environ["QT_QPA_PLATFORM"] = "cocoa"
    _write_qt_conf(plugin_root)
    return plugin_root


_PLUGIN_ROOT = _configure_qt_paths()

try:
    if _PLUGIN_ROOT is not None:
        from PySide6.QtCore import QCoreApplication
        QCoreApplication.setLibraryPaths([str(_PLUGIN_ROOT)])
    from PySide6.QtWidgets import QApplication
except ModuleNotFoundError as exc:
    print(
        "E-Pet Control Center requires PySide6.\n"
        "Install it with: python -m pip install -r requirements.txt\n"
        f"Import error: {exc}"
    )
    raise SystemExit(1)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("E-Pet Control Center")
    app.setOrganizationName("EPet")
    from epet_gui.app import EpetControlCenter

    window = EpetControlCenter()
    window.show()
    sys.exit(app.exec())
