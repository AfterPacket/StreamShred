#!/usr/bin/env python3
"""
trash_purge.py - List/delete/empty Recycle Bin (Windows) or Trash (Linux/macOS).

Commands:
  list                 Show items in trash/recycle
  delete --match TEXT  Permanently delete matching items
  empty                Empty trash/recycle (permanent)

Safety:
  - Default is DRY RUN for delete/empty unless --force is provided.
  - Deleting from trash is irreversible.
"""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple
from urllib.parse import unquote

IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

# ---------------------------
# Common structures/utilities
# ---------------------------

@dataclass
class TrashItem:
    platform: str
    display_name: str
    original_path: str
    deleted_time: Optional[str]
    size_bytes: Optional[int]
    recycle_location: str
    delete_targets: List[Path]  # paths to remove to purge item


def fmt_size(n: Optional[int]) -> str:
    if n is None:
        return "?"
    units = ["B", "KB", "MB", "GB", "TB"]
    x = float(n)
    for u in units:
        if x < 1024 or u == units[-1]:
            return f"{x:.1f}{u}" if u != "B" else f"{int(x)}B"
        x /= 1024
    return f"{n}B"


def safe_delete_path(p: Path) -> None:
    if not p.exists():
        return
    if p.is_dir():
        shutil.rmtree(p, ignore_errors=False)
    else:
        p.unlink(missing_ok=True)


def matches(item: TrashItem, needle: str) -> bool:
    n = needle.lower()
    return (
        n in item.display_name.lower()
        or n in item.original_path.lower()
        or n in item.recycle_location.lower()
    )


# ---------------------------
# Windows: enumerate $Recycle.Bin and parse $I files
# ---------------------------

def win_get_logical_drives() -> List[str]:
    """Return drive letters like ['C:', 'D:']."""
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    drives = []
    for i in range(26):
        if mask & (1 << i):
            drives.append(f"{chr(ord('A') + i)}:")
    return drives


def win_filetime_to_iso(filetime: int) -> str:
    # FILETIME is 100-ns intervals since 1601-01-01 UTC
    if filetime <= 0:
        return ""
    us = filetime // 10
    epoch_1601 = dt.datetime(1601, 1, 1, tzinfo=dt.timezone.utc)
    t = epoch_1601 + dt.timedelta(microseconds=us)
    return t.astimezone().isoformat(timespec="seconds")


def win_parse_I_file(i_path: Path) -> Tuple[Optional[str], Optional[int], Optional[str]]:
    """
    Parse $Ixxxxx metadata file.
    Typical Windows 10/11 format:
      0x00: QWORD version
      0x08: QWORD original size
      0x10: QWORD deletion time FILETIME
      0x18: UTF-16LE original path (null-terminated)
    """
    try:
        data = i_path.read_bytes()
        if len(data) < 0x18:
            return None, None, None
        version = int.from_bytes(data[0:8], "little", signed=False)
        size = int.from_bytes(data[8:16], "little", signed=False)
        ftime = int.from_bytes(data[16:24], "little", signed=False)

        # UTF-16LE path starting at 0x18
        raw = data[24:]
        # split at UTF-16 null terminator
        try:
            # find double-null in bytes
            end = raw.find(b"\x00\x00")
            if end != -1:
                raw = raw[: end + 2]
            orig_path = raw.decode("utf-16le", errors="ignore").rstrip("\x00")
        except Exception:
            orig_path = ""

        deleted = win_filetime_to_iso(ftime) if ftime else None
        return str(orig_path), int(size), deleted
    except Exception:
        return None, None, None


def win_enumerate_recycle_items() -> List[TrashItem]:
    items: List[TrashItem] = []
    for drive in win_get_logical_drives():
        base = Path(f"{drive}\\$Recycle.Bin")
        if not base.exists():
            continue

        # SID subfolders (may include other users; we only list what we can access)
        try:
            sid_dirs = [p for p in base.iterdir() if p.is_dir()]
        except Exception:
            continue

        for sid in sid_dirs:
            try:
                for entry in sid.iterdir():
                    name = entry.name
                    # We prefer parsing $I files to get original path info
                    if name.startswith("$I") and entry.is_file():
                        suffix = name[2:]  # after $I
                        r_name = "$R" + suffix
                        r_path = sid / r_name

                        orig_path, size, deleted = win_parse_I_file(entry)
                        display = Path(orig_path).name if orig_path else r_name

                        delete_targets = [entry]
                        if r_path.exists():
                            delete_targets.append(r_path)

                        items.append(
                            TrashItem(
                                platform="windows",
                                display_name=display,
                                original_path=orig_path or "",
                                deleted_time=deleted,
                                size_bytes=size,
                                recycle_location=str(sid),
                                delete_targets=delete_targets,
                            )
                        )
            except Exception:
                continue
    return items


def win_empty_recycle_bin(dry_run: bool) -> None:
    """
    Empty recycle bin via official Shell API.
    SHEmptyRecycleBinW(hwnd, pszRootPath, dwFlags)
    """
    if dry_run:
        return
    shell32 = ctypes.windll.shell32
    # flags:
    # 0x1 = SHERB_NOCONFIRMATION
    # 0x2 = SHERB_NOPROGRESSUI
    # 0x4 = SHERB_NOSOUND
    flags = 0x1 | 0x2 | 0x4
    res = shell32.SHEmptyRecycleBinW(None, None, flags)
    if res != 0:
        raise RuntimeError(f"SHEmptyRecycleBinW failed with code: {res}")


# ---------------------------
# Linux: ~/.local/share/Trash (FreeDesktop)
# ---------------------------

def linux_trash_root() -> Path:
    return Path.home() / ".local" / "share" / "Trash"


def linux_enumerate_trash_items() -> List[TrashItem]:
    root = linux_trash_root()
    files_dir = root / "files"
    info_dir = root / "info"
    items: List[TrashItem] = []

    if not files_dir.exists() or not info_dir.exists():
        return items

    for f in files_dir.iterdir():
        info = info_dir / (f.name + ".trashinfo")
        orig_path = ""
        deleted = None
        size = None

        if info.exists():
            try:
                txt = info.read_text(encoding="utf-8", errors="ignore").splitlines()
                for line in txt:
                    if line.startswith("Path="):
                        orig_path = unquote(line[len("Path="):].strip())
                    elif line.startswith("DeletionDate="):
                        deleted = line[len("DeletionDate="):].strip()
            except Exception:
                pass

        try:
            if f.is_file():
                size = f.stat().st_size
        except Exception:
            size = None

        items.append(
            TrashItem(
                platform="linux",
                display_name=f.name,
                original_path=orig_path,
                deleted_time=deleted,
                size_bytes=size,
                recycle_location=str(files_dir),
                delete_targets=[f] + ([info] if info.exists() else []),
            )
        )
    return items


def linux_empty_trash(dry_run: bool) -> None:
    root = linux_trash_root()
    for sub in (root / "files", root / "info"):
        if not sub.exists():
            continue
        for p in sub.iterdir():
            if dry_run:
                continue
            safe_delete_path(p)


# ---------------------------
# macOS: ~/.Trash
# ---------------------------

def mac_trash_root() -> Path:
    return Path.home() / ".Trash"


def mac_enumerate_trash_items() -> List[TrashItem]:
    root = mac_trash_root()
    items: List[TrashItem] = []
    if not root.exists():
        return items

    for p in root.iterdir():
        size = None
        try:
            if p.is_file():
                size = p.stat().st_size
        except Exception:
            pass

        items.append(
            TrashItem(
                platform="mac",
                display_name=p.name,
                original_path="",
                deleted_time=None,
                size_bytes=size,
                recycle_location=str(root),
                delete_targets=[p],
            )
        )
    return items


def mac_empty_trash(dry_run: bool) -> None:
    root = mac_trash_root()
    if not root.exists():
        return
    for p in root.iterdir():
        if dry_run:
            continue
        safe_delete_path(p)


# ---------------------------
# Dispatcher
# ---------------------------

def enumerate_items() -> List[TrashItem]:
    if IS_WINDOWS:
        return win_enumerate_recycle_items()
    if IS_LINUX:
        return linux_enumerate_trash_items()
    if IS_MAC:
        return mac_enumerate_trash_items()
    return []


def empty_trash(dry_run: bool) -> None:
    if IS_WINDOWS:
        win_empty_recycle_bin(dry_run=dry_run)
    elif IS_LINUX:
        linux_empty_trash(dry_run=dry_run)
    elif IS_MAC:
        mac_empty_trash(dry_run=dry_run)
    else:
        raise RuntimeError("Unsupported OS")


def cmd_list() -> int:
    items = enumerate_items()
    if not items:
        print("[*] No items found (or insufficient permissions).")
        return 0

    # Simple table-like output
    print(f"Found {len(items)} item(s):\n")
    for i, it in enumerate(items, start=1):
        print(f"{i:>4}. {it.display_name}")
        if it.original_path:
            print(f"     Original : {it.original_path}")
        if it.deleted_time:
            print(f"     Deleted  : {it.deleted_time}")
        print(f"     Size     : {fmt_size(it.size_bytes)}")
        print(f"     Location : {it.recycle_location}")
        print()
    return 0


def cmd_delete(match_text: str, dry_run: bool) -> int:
    items = enumerate_items()
    targets = [it for it in items if matches(it, match_text)]

    if not targets:
        print(f"[*] No matches for: {match_text!r}")
        return 0

    print(f"Matched {len(targets)} item(s) for {match_text!r}:")
    for it in targets:
        print(f" - {it.display_name}  |  {it.original_path or it.recycle_location}")

    if dry_run:
        print("\n[DRY RUN] Nothing deleted. Re-run with --force to permanently delete.")
        return 0

    for it in targets:
        for p in it.delete_targets:
            safe_delete_path(p)
        # best-effort dir fsync
        try:
            fsync_dir_best_effort(Path(it.recycle_location))
        except Exception:
            pass

    print("[+] Deleted matched items permanently.")
    return 0


def cmd_empty(dry_run: bool) -> int:
    if dry_run:
        print("[DRY RUN] Would empty recycle/trash. Re-run with --force to actually empty.")
        return 0
    empty_trash(dry_run=False)
    print("[+] Emptied recycle/trash permanently.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="List/delete/empty Recycle Bin (Windows) or Trash (Linux/macOS).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="List items currently in recycle/trash")

    d = sub.add_parser("delete", help="Delete items from recycle/trash matching text (permanent)")
    d.add_argument("--match", required=True, help="Substring match against name/original path/location")
    d.add_argument("--force", action="store_true", help="Actually delete (otherwise dry-run)")

    e = sub.add_parser("empty", help="Empty recycle/trash (permanent)")
    e.add_argument("--force", action="store_true", help="Actually empty (otherwise dry-run)")

    args = ap.parse_args(argv)

    if args.cmd == "list":
        return cmd_list()

    if args.cmd == "delete":
        return cmd_delete(args.match, dry_run=not args.force)

    if args.cmd == "empty":
        return cmd_empty(dry_run=not args.force)

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
