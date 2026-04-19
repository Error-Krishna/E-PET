from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

IS_MACOS = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")


def _expand(path: str) -> Path:
    return Path(path).expanduser().resolve()


def create_file(path: str, content: str = "") -> None:
    target = _expand(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(str(content), encoding="utf-8")


def read_file(path: str) -> str:
    return _expand(path).read_text(encoding="utf-8")


def write_file(path: str, content: str, append: bool = False) -> None:
    target = _expand(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with target.open(mode, encoding="utf-8") as handle:
        handle.write(str(content))


def delete_file(path: str) -> None:
    _expand(path).unlink(missing_ok=True)


def move_file(src: str, dst: str) -> None:
    shutil.move(str(_expand(src)), str(_expand(dst)))


def copy_file(src: str, dst: str) -> None:
    source = _expand(src)
    destination = _expand(dst)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def rename_file(path: str, new_name: str) -> None:
    source = _expand(path)
    destination = source.with_name(new_name)
    shutil.move(str(source), str(destination))


def list_directory(path: str = None) -> list[dict]:
    base = _expand(path or ".")
    items = []
    for entry in sorted(base.iterdir(), key=lambda p: p.name.lower()):
        stat = entry.stat()
        items.append(
            {
                "name": entry.name,
                "type": "directory" if entry.is_dir() else "file",
                "size": stat.st_size,
                "modified": stat.st_mtime,
            }
        )
    return items


def create_directory(path: str) -> None:
    _expand(path).mkdir(parents=True, exist_ok=True)


def delete_directory(path: str, recursive: bool = False) -> None:
    target = _expand(path)
    if recursive:
        shutil.rmtree(target, ignore_errors=False)
    else:
        target.rmdir()


def open_in_finder(path: str) -> None:
    target = _expand(path)
    if IS_MACOS:
        subprocess.run(["open", str(target)], check=True, timeout=10)
        return
    if IS_WINDOWS:
        subprocess.run(["explorer", str(target)], check=True, timeout=10)
        return
    subprocess.run(["xdg-open", str(target)], check=True, timeout=10)


def get_file_info(path: str) -> dict:
    target = _expand(path)
    stat = target.stat()
    return {
        "size": stat.st_size,
        "created": stat.st_ctime,
        "modified": stat.st_mtime,
        "type": "directory" if target.is_dir() else "file",
    }


def search_files(query: str, directory: str = None, file_type: str = None) -> list[str]:
    search_root = _expand(directory or ".")
    needle = str(query or "").strip().lower()
    results: list[str] = []

    if IS_MACOS and query:
        try:
            predicate = f'kMDItemFSName == "*{needle}*" || kMDItemTextContent == "*{needle}*"'
            if file_type:
                predicate = f"({predicate}) && kMDItemKind == '{file_type}'"
            command = ["mdfind", predicate]
            if directory:
                command.extend(["-onlyin", str(search_root)])
            completed = subprocess.run(command, check=True, capture_output=True, text=True, timeout=15)
            for line in completed.stdout.splitlines():
                if line.strip():
                    results.append(line.strip())
            if results:
                return results
        except Exception:
            pass

    for root, _, files in os.walk(search_root):
        for filename in files:
            if needle and needle not in filename.lower():
                continue
            if file_type and not filename.lower().endswith(f".{file_type.lower().lstrip('.')}"):
                continue
            results.append(str(Path(root) / filename))
    return results


def zip_files(files: list[str], output: str) -> None:
    destination = _expand(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in files:
            path = _expand(item)
            archive.write(path, arcname=path.name)


def unzip_file(path: str, destination: str = None) -> None:
    archive_path = _expand(path)
    target = _expand(destination or archive_path.with_suffix(""))
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "r") as archive:
        archive.extractall(target)


def get_downloads_folder() -> str:
    return str(Path.home() / "Downloads")


def get_desktop_folder() -> str:
    return str(Path.home() / "Desktop")


def get_documents_folder() -> str:
    return str(Path.home() / "Documents")


def open_file_with(file_path: str, app_name: str) -> None:
    file_target = _expand(file_path)
    app = str(app_name or "").strip()
    if IS_MACOS:
        subprocess.run(["open", "-a", app, str(file_target)], check=True, timeout=10)
        return
    if IS_WINDOWS:
        subprocess.run(
            ["cmd", "/c", "start", "", app, str(file_target)],
            shell=False,
            check=True,
            timeout=10,
        )
        return
    subprocess.run([app, str(file_target)], check=True, timeout=10)
