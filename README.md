# StreamShred 🧹💥

**Lightweight Python tools for secure file deletion and trash management across Windows, Linux, and macOS.**

### 🗂️ StreamShred.py
Best-effort file overwriting with multiple cryptographic passes, verification, and secure deletion. Ideal for sanitizing sensitive files before removal.

### 🗑️ trash_purge.py
Cross-platform trash/recycle bin management with pattern matching and dry-run protection. Safely audit and permanently delete specific items or empty trash entirely.

> ⚠️ **Important: Modern SSD/NVMe/M.2 Limitations**  
> 
> **File-level overwriting cannot guarantee secure deletion on modern solid-state drives.** Here's why:
> 
> - **Wear leveling**: Controllers remap blocks to distribute writes, leaving old data in unmapped cells
> - **Over-provisioning**: Extra hidden storage may retain copies of overwritten data
> - **TRIM/garbage collection**: Filesystems may have already marked blocks as unused before overwriting
> - **Snapshots & journaling**: File systems may keep historical copies in metadata structures
> 
> **For genuine data destruction on SSDs:**
> - Use **full-disk encryption + key destruction** (crypto-erase)
> - Use manufacturer's **Secure Erase** or **Sanitize** commands (via vendor tools or `hdparm`/`nvme-cli`)
> - For ultimate assurance: **physical destruction**
> 
> **StreamShred is best for:** Reducing data recoverability by casual forensics, compliance scenarios requiring "reasonable effort," or traditional HDDs where overwriting is more effective.

---

## 📋 Contents

- [Install](#install)
- [StreamShred](#streamshred)
  - [Windows Commands](#windows-commands)
  - [Linux Commands](#linux-commands)
  - [Options](#options)
- [Trash Purge](#trash-purge)
  - [Windows Commands](#windows-commands-1)
  - [Linux Commands](#linux-commands-1)
  - [macOS Commands](#macos-commands)
  - [Commands Reference](#commands-reference)
- [License](#license)
- [Disclaimer](#disclaimer)

---

## Install

**No dependencies required.**

- Python **3.9+** recommended
- Run scripts directly:
  ```bash
  python StreamShred.py
  python trash_purge.py
  ```

---

## StreamShred

**Best-effort secure overwrite + delete** for selected files:

- Few passes (**3–7** configurable)
- Writes **cryptographically strong random bytes** in **chunks**
- Optional **verification**: reads back **N samples per pass** and compares to what was written
- Best-effort **rename → truncate → delete** with `fsync` where possible
- Optional Linux cache hint: `posix_fadvise(DONTNEED)` (best-effort)

### Windows Commands

#### ✅ Recommended "best default" (SSD/NVMe hygiene)
3 passes (fixed), verification on, force (no prompt), pick multiple files via GUI

```powershell
python .\StreamShred.py --pick --force --verify --verify-samples 25 --verify-len 256 --min-passes 3 --max-passes 3
```

#### Random pass count (3–7) with verification
```powershell
python .\StreamShred.py --pick --force --verify --verify-samples 25 --verify-len 256
```

#### Exactly 5 passes (fixed)
```powershell
python .\StreamShred.py --pick --force --verify --verify-samples 25 --verify-len 256 --min-passes 5 --max-passes 5
```

#### Single file (no picker)
```powershell
python .\StreamShred.py "C:\path\to\secret.txt" --force --verify --verify-samples 25 --verify-len 256 --min-passes 3 --max-passes 3
```

### Linux Commands

#### ✅ Recommended "best default"
```bash
python3 ./StreamShred.py --pick --force --verify --verify-samples 25 --verify-len 256 --min-passes 3 --max-passes 3
```

#### With Linux cache hint (best-effort)
```bash
python3 ./StreamShred.py --pick --force --verify --verify-samples 25 --verify-len 256 --min-passes 3 --max-passes 3 --drop-cache
```

#### Single file
```bash
python3 ./StreamShred.py ~/secret.txt --force --verify --verify-samples 25 --verify-len 256 --min-passes 3 --max-passes 3
```

### Options

View all options:
```bash
python StreamShred.py --help
```

**Common flags:**

| Flag | Description |
|------|-------------|
| `--pick` | GUI picker (multi-file) |
| `--force` | Skip the "YES" confirmation prompt |
| `--min-passes N` | Minimum number of passes |
| `--max-passes N` | Maximum number of passes (set equal to min for fixed) |
| `--verify` | Enable sampling read-back verification per pass |
| `--verify-samples N` | Number of samples per pass (default: 6) |
| `--verify-len BYTES` | Bytes per sample (default: 64) |
| `--chunk BYTES` | Write chunk size (default: 1 MiB) |
| `--rename-passes N` | Rename steps before delete (default: 2) |
| `--drop-cache` | Linux-only cache hint |
| `--keep` | Overwrite but do not delete (testing) |

---

## Trash Purge

List / delete / empty Recycle Bin (Windows) or Trash (Linux/macOS).

**Safety behavior:**
- `delete` and `empty` are **DRY RUN** by default
- You must add `--force` to actually delete

### Windows Commands

#### List items in Recycle Bin
```powershell
python .\trash_purge.py list
```

#### Dry-run delete (preview matches only)
```powershell
python .\trash_purge.py delete --match invoice
```

#### Permanently delete matching items
```powershell
python .\trash_purge.py delete --match invoice --force
```

#### Empty Recycle Bin (dry run)
```powershell
python .\trash_purge.py empty
```

#### Empty Recycle Bin (PERMANENT)
```powershell
python .\trash_purge.py empty --force
```

### Linux Commands

#### List trash contents
```bash
python3 ./trash_purge.py list
```

#### Delete matching items (dry run → then force)
```bash
python3 ./trash_purge.py delete --match report
python3 ./trash_purge.py delete --match report --force
```

#### Empty trash (dry run → then force)
```bash
python3 ./trash_purge.py empty
python3 ./trash_purge.py empty --force
```

### macOS Commands

#### List ~/.Trash
```bash
python3 ./trash_purge.py list
```

#### Delete matching items (dry run → then force)
```bash
python3 ./trash_purge.py delete --match screenshot
python3 ./trash_purge.py delete --match screenshot --force
```

#### Empty trash (dry run → then force)
```bash
python3 ./trash_purge.py empty
python3 ./trash_purge.py empty --force
```

### Commands Reference

View all commands:
```bash
python trash_purge.py --help
```

**Subcommands:**

| Command | Description |
|---------|-------------|
| `list` | Show items currently in recycle/trash |
| `delete --match TEXT [--force]` | Delete matching items |
| `empty [--force]` | Empty recycle/trash |

---

## License

**GNU General Public License v3.0 (GPL-3.0)**

This project is licensed under the GNU GPL v3.0. See the [LICENSE](LICENSE) file for details.

---

## Disclaimer

⚠️ **This software is provided "as is", without warranty. You are responsible for using it legally and safely. Destructive operations are irreversible. Use at your own risk.**
