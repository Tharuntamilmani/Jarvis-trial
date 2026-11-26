# Backup & Restore

JARVIS provides verified backup and restore tooling (`src/jarvis/database/backup.py`, `scripts/backup_db.py`, `scripts/restore_db.py`).

---

## Creating a Backup

```powershell
python scripts/backup_db.py
```

Options:
- `--output-dir DIR` — Override `JARVIS_BACKUP_DIR`
- `--encrypt` — Encrypt the `.dump` archive with Win32 DPAPI (`.dump.enc`)
- `--retention N` — Override `JARVIS_BACKUP_RETENTION` (default `14`)

Each backup writes:
- The PostgreSQL custom-format dump (`.dump` or `.dump.enc`)
- A JSON manifest containing the **SHA-256** checksum and schema revision (`0003_agent_runtime`)

---

## Restoring a Backup

`scripts/restore_db.py` verifies the manifest SHA-256 checksum and expected schema revision before restoring:

```powershell
# 1. Dry-run verification first
python scripts/restore_db.py --backup backups\<file>.dump --target-url "postgresql+asyncpg://jarvis:jarvis@localhost:5433/jarvis_test" --dry-run

# 2. Explicit confirmed restore
python scripts/restore_db.py --backup backups\<file>.dump --target-url "postgresql+asyncpg://jarvis:jarvis@localhost:5433/jarvis_test" --confirm
```
