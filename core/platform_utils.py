from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MEMORY_FILENAME = "epet.kv.json"


def get_project_root() -> Path:
    """Return the repository root, regardless of the current working directory."""
    return PROJECT_ROOT


def get_config_path(filename: str = "config.yaml") -> Path:
    """Return a config path anchored at the project root."""
    return get_project_root() / filename


def get_database_path(filename: str = DEFAULT_MEMORY_FILENAME) -> Path:
    """Return a database path anchored at the project root."""
    return get_project_root() / filename


def is_interactive_input() -> bool:
    """True when stdin can be used for keyboard-style fallback input."""
    return bool(getattr(sys.stdin, "isatty", lambda: False)())


def resolve_executable(command: str | os.PathLike[str]) -> str | None:
    """
    Resolve a command to an executable path.

    Accepts either a bare command name or a filesystem path. On Windows we also
    try a .exe suffix when the bare name is not present on PATH.
    """
    # This is for real executables, not GUI app bundle names that may contain
    # spaces; those are handled by the OS bridge instead.
    candidate = Path(command).expanduser()
    if candidate.exists():
        return str(candidate)

    resolved = shutil.which(str(command))
    if resolved:
        return resolved

    if os.name == "nt" and not str(command).lower().endswith(".exe"):
        resolved = shutil.which(f"{command}.exe")
        if resolved:
            return resolved

    return None
