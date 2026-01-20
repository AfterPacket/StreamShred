# StreamShred 🧹💥

**Cross-platform, best-effort secure file overwrite + delete** using **few-pass, high-quality randomness** (chunked/streaming) written in Python.

> ⚠️ Reality check (SSD/NVMe / M.2):
> File-level overwriting on modern SSD/NVMe is **best-effort**. Controllers and filesystems can remap blocks, journal metadata, and keep copies outside the logical file view.
> If you need **high assurance**, use **full-disk / container encryption + key destruction (crypto-erase)** or a device/vendor sanitize workflow.

---

## What it does

- **3–7 passes** (configurable), writing **cryptographically strong random bytes** in **chunks**
- Optional **verification mode**: reads back **N small random samples per pass** and compares against what was written  
  - captures only the expected sample bytes during streaming (no huge RAM use)
- Best-effort **rename → truncate → unlink** (delete)
- Best-effort `fsync` to reduce “still in cache / not flushed” risk
- **Linux-only cache hint**: `posix_fadvise(DONTNEED)` (best-effort) to reduce page cache footprint
- Optional **GUI file picker** (`--pick`) if Tkinter is available

---

## How it works (algorithm)

StreamShred follows a simple, correctness-first overwrite workflow designed for low memory usage:

1) **Validate target**
- Refuses directories and symlinks.
- Prompts for `YES` unless `--force` is used.

2) **Choose pass count**
- Default: randomly picks a value in **[3, 7]** (or fixed if you disable randomization).

3) **Overwrite in streaming chunks**
- For each pass, writes from offset `0 → EOF` using `secrets.token_bytes()` in **bounded chunk size** (default 1 MiB).
- This keeps memory usage ~`chunk_size`, not file size.

4) **Flush + fsync**
- After each pass: `flush()` + `os.fsync(fd)` to reduce risk of data remaining only in cache.

5) **Optional verification (sample read-back)**
- If `--verify` is enabled:
  - Picks `N` random offsets and `L` bytes per sample.
  - While streaming writes, the script captures the expected bytes for just those sample windows.
  - After the pass, it reads those windows back from disk and compares.
- This confirms the OS-visible bytes match what was written at sampled offsets without storing the full output.

6) **Linux-only cache hint (optional)**
- If `--drop-cache` is enabled (Linux):
  - Calls `posix_fadvise(..., DONTNEED)` as a **hint** to drop cached pages for the file region.

7) **Metadata cleanup + delete**
- Renames the file `N` times (default 2) with random names (best-effort metadata churn).
- Truncates to 0 bytes (best-effort), then deletes (`unlink`).
- On POSIX systems, best-effort `fsync` of the directory is used to help persist metadata updates.

### What verification *does* and *does not* prove
- ✅ Confirms overwrite writes are happening correctly at the logical file layer (for sampled offsets).
- ❌ Does not guarantee physical sanitization on SSD/NVMe due to wear leveling/remapping and filesystem behavior.

---

## Install

No dependencies.

- Python **3.9+** recommended (works on Windows & Linux)
- Put `StreamShred.py` somewhere on your PATH, or run it directly.

---

## Sample commands

### Windows (PowerShell)

**Basic wipe (random pass count between 3 and 7)**
```powershell
python .\StreamShred.py "C:\Users\Jordan\Desktop\secret.txt"
