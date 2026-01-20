#!/usr/bin/env python3
"""
StreamShred.py — Cross-platform best-effort secure file overwrite + delete.

Copyright (C) 2026 Jordan Lassiter

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.

--------------------------------------------------------------------

What it does:
- 3–7 passes (configurable), each pass writes cryptographically strong random bytes in chunks.
- Optional verification: reads back N small samples per pass and compares to what was written.
  (Captures only the expected bytes for those samples during streaming; no large RAM usage.)
- Rename -> truncate -> unlink (delete) with fsync best-effort.
- Linux-only cache hint: posix_fadvise(POSIX_FADV_DONTNEED) to attempt to free cached pages.

Important SSD/NVMe note:
- On SSD/NVMe (including most M.2), file-level overwriting is best-effort; controllers abstract
  physical media and may remap blocks.
- If you need high assurance on NVMe, prefer: encrypted container + key destruction (crypto erase),
  or device sanitize tools where appropriate.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

APP_NAME = "StreamShred"

# -------------------------
# Helpers: fsync + cache hints
# -------------------------


def fsync_dir_best_effort(directory: Path) -> None:
    """POSIX best-effort fsync of the directory to persist rename/unlink metadata."""
    if os.name != "posix":
        return
    try:
        dfd = os.open(str(directory), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except Exception:
        pass


def linux_drop_page_cache_best_effort(fd: int, file_size: int) -> None:
    """
    Linux-only best-effort: posix_fadvise(..., DONTNEED) attempts to free cached pages
    for the specified region.
    """
    try:
        if os.name != "posix":
            return
        if not (hasattr(os, "posix_fadvise") and hasattr(os, "POSIX_FADV_DONTNEED")):
            return

        page = os.sysconf("SC_PAGESIZE") if hasattr(os, "sysconf") else 4096
        off = 0
        length = ((file_size + page - 1) // page) * page
        os.posix_fadvise(fd, off, length, os.POSIX_FADV_DONTNEED)
    except Exception:
        pass


def rand_name_hex(nbytes: int = 16) -> str:
    return secrets.token_hex(nbytes)


# -------------------------
# Verification sampling
# -------------------------


@dataclass
class Sample:
    offset: int
    length: int


def choose_samples(file_size: int, sample_count: int, sample_len: int) -> List[Sample]:
    if file_size <= 0 or sample_count <= 0 or sample_len <= 0:
        return []
    sample_len = min(sample_len, file_size)
    samples: List[Sample] = []
    for _ in range(sample_count):
        start = secrets.randbelow(file_size - sample_len + 1)
        samples.append(Sample(offset=start, length=sample_len))
    return samples


def capture_expected_from_chunk(
    samples: List[Sample],
    expected: List[bytearray],
    filled: List[bytearray],
    chunk_data: bytes,
    chunk_start: int,
) -> None:
    """
    As we stream-write, record the expected bytes for any sample ranges that overlap this chunk.
    expected[i] holds the expected bytes for sample i.
    filled[i] is a bytearray mask (0/1) to track which bytes in expected[i] have been filled.
    """
    chunk_end = chunk_start + len(chunk_data)
    for i, s in enumerate(samples):
        s_start = s.offset
        s_end = s.offset + s.length
        if s_end <= chunk_start or s_start >= chunk_end:
            continue

        overlap_start = max(s_start, chunk_start)
        overlap_end = min(s_end, chunk_end)

        sample_i0 = overlap_start - s_start
        sample_i1 = overlap_end - s_start
        chunk_i0 = overlap_start - chunk_start
        chunk_i1 = overlap_end - chunk_start

        expected[i][sample_i0:sample_i1] = chunk_data[chunk_i0:chunk_i1]
        filled[i][sample_i0:sample_i1] = b"\x01" * (sample_i1 - sample_i0)


def all_filled(mask: bytearray) -> bool:
    return all(b == 1 for b in mask)


# -------------------------
# Core overwrite + delete
# -------------------------


def build_pass_count(min_passes: int, max_passes: int, randomize: bool) -> int:
    if min_passes < 1 or max_passes < min_passes:
        raise ValueError("Invalid pass bounds")
    if not randomize or min_passes == max_passes:
        return min_passes
    return min_passes + secrets.randbelow(max_passes - min_passes + 1)


def overwrite_random_streaming(
    path: Path,
    passes: int,
    chunk_size: int,
    verify: bool,
    verify_samples: int,
    verify_len: int,
    drop_cache_linux: bool,
) -> None:
    size = path.stat().st_size
    if size == 0:
        return

    with open(path, "r+b", buffering=0) as f:
        fd = f.fileno()

        for p in range(1, passes + 1):
            samples = choose_samples(size, verify_samples, verify_len) if verify else []
            expected: List[bytearray] = [bytearray(s.length) for s in samples]
            filled: List[bytearray] = [bytearray(b"\x00" * s.length) for s in samples]

            f.seek(0)
            written = 0
            while written < size:
                n = min(chunk_size, size - written)
                data = secrets.token_bytes(n)

                if verify and samples:
                    capture_expected_from_chunk(
                        samples, expected, filled, data, written
                    )

                f.write(data)
                written += n

            f.flush()
            os.fsync(fd)

            if drop_cache_linux:
                linux_drop_page_cache_best_effort(fd, size)

            if verify and samples:
                for m in filled:
                    if not all_filled(m):
                        raise RuntimeError(
                            "Internal verification capture failed (sample not fully captured)."
                        )

                for i, s in enumerate(samples):
                    f.seek(s.offset)
                    got = f.read(s.length)
                    if got != bytes(expected[i]):
                        raise RuntimeError(
                            f"Verification failed on pass {p}: sample {i + 1} mismatch at offset {s.offset}"
                        )

                if drop_cache_linux:
                    linux_drop_page_cache_best_effort(fd, size)

            print(
                f"[*] {APP_NAME}: pass {p}/{passes} complete"
                + (" (verified)" if verify else "")
            )


def rename_truncate_unlink(path: Path, rename_passes: int, keep: bool) -> Path:
    current = path

    for _ in range(max(0, rename_passes)):
        new_name = rand_name_hex(16)
        candidate = current.with_name(new_name)
        tries = 0
        while candidate.exists() and tries < 10:
            new_name = rand_name_hex(16)
            candidate = current.with_name(new_name)
            tries += 1
        current.rename(candidate)
        current = candidate
        fsync_dir_best_effort(current.parent)

    try:
        with open(current, "r+b", buffering=0) as f:
            f.truncate(0)
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        pass

    if keep:
        fsync_dir_best_effort(current.parent)
        return current

    current.unlink()
    fsync_dir_best_effort(current.parent)
    return current


def wipe_file(
    file_path: Path,
    *,
    min_passes: int,
    max_passes: int,
    randomize_pass_count: bool,
    chunk_size: int,
    verify: bool,
    verify_samples: int,
    verify_len: int,
    rename_passes: int,
    drop_cache_linux: bool,
    force: bool,
    keep: bool,
) -> None:
    if not file_path.exists():
        raise FileNotFoundError(file_path)
    if file_path.is_dir():
        raise IsADirectoryError(f"Refusing to wipe a directory: {file_path}")
    if file_path.is_symlink():
        raise RuntimeError(f"Refusing to wipe a symlink: {file_path}")

    if not force:
        print(f"[!] {APP_NAME} target: {file_path}")
        print("[!] Destructive operation. On SSD/NVMe this is best-effort only.")
        if input('Type "YES" to continue: ').strip() != "YES":
            print("[*] Aborted.")
            return

    passes = build_pass_count(min_passes, max_passes, randomize_pass_count)
    size = file_path.stat().st_size
    print(
        f"[*] {APP_NAME}: size={size} bytes | passes={passes} | chunk={chunk_size} bytes"
    )

    if size > 0:
        overwrite_random_streaming(
            file_path,
            passes=passes,
            chunk_size=chunk_size,
            verify=verify,
            verify_samples=verify_samples,
            verify_len=verify_len,
            drop_cache_linux=drop_cache_linux,
        )
    else:
        print(f"[*] {APP_NAME}: file is 0 bytes; skipping overwrites.")

    final_path = rename_truncate_unlink(
        file_path, rename_passes=rename_passes, keep=keep
    )
    if keep:
        print(f"[+] {APP_NAME}: completed overwrites. File kept at: {final_path}")
    else:
        print(f"[+] {APP_NAME}: completed overwrites + delete.")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog=APP_NAME,
        description=f"{APP_NAME} — cross-platform streaming overwrite + delete (few passes, random, optional verification).",
    )
    ap.add_argument("file", nargs="?", help="Path to file to shred")
    ap.add_argument(
        "--pick", action="store_true", help="GUI file picker (if available)"
    )
    ap.add_argument(
        "--min-passes", type=int, default=3, help="Minimum passes (default 3)"
    )
    ap.add_argument(
        "--max-passes", type=int, default=7, help="Maximum passes (default 7)"
    )
    ap.add_argument(
        "--no-randomize-pass-count",
        action="store_true",
        help="Use exactly min-passes instead of random in [min,max]",
    )
    ap.add_argument(
        "--chunk",
        type=int,
        default=1024 * 1024,
        help="Chunk size bytes (default 1 MiB)",
    )
    ap.add_argument(
        "--verify",
        action="store_true",
        help="Enable read-back verification samples per pass",
    )
    ap.add_argument(
        "--verify-samples",
        type=int,
        default=6,
        help="Samples per pass when verifying (default 6)",
    )
    ap.add_argument(
        "--verify-len",
        type=int,
        default=64,
        help="Bytes per sample when verifying (default 64)",
    )
    ap.add_argument(
        "--rename-passes",
        type=int,
        default=2,
        help="Rename passes before delete (default 2)",
    )
    ap.add_argument(
        "--drop-cache",
        action="store_true",
        help="Linux only: posix_fadvise(DONTNEED) after each pass (best-effort)",
    )
    ap.add_argument(
        "--keep", action="store_true", help="Do not delete after shredding (testing)"
    )
    ap.add_argument("--force", action="store_true", help="Skip confirmation prompt")
    args = ap.parse_args(argv)

    # Build target list: either CLI file, or GUI picker (single or multi)
    targets: List[Path] = []

    if args.file and not args.pick:
        targets = [Path(args.file).expanduser()]
    else:
        # No file provided OR --pick explicitly requested -> use GUI picker
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()

            picked = filedialog.askopenfilenames(
                title=f"Select file(s) to shred ({APP_NAME})"
            )
            if not picked:
                print("[*] No files selected.")
                return 0

            targets = [Path(p) for p in picked]
        except Exception as e:
            print(f"[!] GUI picker unavailable ({e}). Provide a file path instead.")
            return 2

    # Run wipe for each selected target; keep going even if one fails.
    exit_code = 0
    for t in targets:
        try:
            wipe_file(
                t,
                min_passes=args.min_passes,
                max_passes=args.max_passes,
                randomize_pass_count=not args.no_randomize_pass_count,
                chunk_size=max(4096, args.chunk),
                verify=args.verify,
                verify_samples=max(1, args.verify_samples),
                verify_len=max(1, args.verify_len),
                rename_passes=max(0, args.rename_passes),
                drop_cache_linux=args.drop_cache,
                force=args.force,
                keep=args.keep,
            )
        except Exception as e:
            print(f"[!] {APP_NAME}: failed on {t}: {e}", file=sys.stderr)
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
