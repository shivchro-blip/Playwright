"""Zip files for the remote-runner exchange: the workspace goes to a runner, the results come back."""
from __future__ import annotations

import os
import tempfile
import zipfile
from pathlib import Path

WORKSPACE_FOLDERS = ("Data", "Pages", "Tests")  # what the suite reads; never .env, reports or users
MAX_FILES = 50_000
MAX_BYTES = 2 * 1024**3  # unpacked size limit for a results archive


class ArchiveError(ValueError):
    pass


def zip_workspace(workspace: Path) -> Path:
    """The test definitions of a workspace as a temporary zip (the caller deletes it)."""
    handle, name = tempfile.mkstemp(suffix=".zip", prefix="autoplay-workspace-")
    os.close(handle)
    root = Path(workspace).resolve()
    with zipfile.ZipFile(name, "w", zipfile.ZIP_DEFLATED) as z:
        for folder in WORKSPACE_FOLDERS:
            for path in sorted((root / folder).rglob("*")):
                if path.is_file() and not path.is_symlink():
                    z.write(path, path.relative_to(root).as_posix())
    return Path(name)


def zip_folder(folder: Path, target: Path) -> Path:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(Path(folder).rglob("*")):
            if path.is_file() and not path.is_symlink():
                z.write(path, path.relative_to(folder).as_posix())
    return target


def extract_safely(archive: Path, dest: Path, max_files: int = MAX_FILES, max_bytes: int = MAX_BYTES) -> int:
    """Unpack into `dest`. Refuses paths that leave it, links, and archives over the size or file limits."""
    dest = Path(dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    try:
        z = zipfile.ZipFile(archive)
    except zipfile.BadZipFile as e:
        raise ArchiveError("Not a zip file.") from e
    with z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        if len(infos) > max_files or sum(i.file_size for i in infos) > max_bytes:
            raise ArchiveError("The archive is too large.")
        for info in infos:
            if (info.external_attr >> 16) & 0o170000 == 0o120000:  # a symbolic link
                raise ArchiveError(f"Links are not allowed: {info.filename}")
            target = (dest / info.filename).resolve()
            if dest != target and dest not in target.parents:
                raise ArchiveError(f"Path leaves the folder: {info.filename}")
        written = 0
        for info in infos:
            target = (dest / info.filename).resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, target.open("wb") as out:
                while chunk := src.read(1024 * 1024):
                    written += len(chunk)
                    if written > max_bytes:  # sizes in the header can lie
                        raise ArchiveError("The archive is too large.")
                    out.write(chunk)
    return len(infos)
