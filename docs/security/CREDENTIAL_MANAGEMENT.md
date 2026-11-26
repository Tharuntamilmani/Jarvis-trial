# Credential Management

Secrets in JARVIS are managed exclusively through `CredentialStore` (`src/jarvis/security/credentials.py`) using **Windows Data Protection API (DPAPI)**.

---

## How DPAPI Storage Works

- Secrets are encrypted using `CryptProtectData` bound to the current Windows user account and decrypted only in-memory via `CryptUnprotectData`.
- `UserAppPreferences` (`src/jarvis/config/preferences.py`) explicitly rejects secret prefixes (`gsk_`, `sk-`, `bearer `).
- `.env` and `JARVIS_` environment variables do **not** store API keys.

---

## Managing the Groq API Key

Use `scripts/import_groq_key.py` (never prints or logs the key value):

```powershell
# Initial import (exits 0 without overwriting if already stored)
$env:GROQ_API_KEY = "gsk_your_key_here"
python scripts/import_groq_key.py
Remove-Item Env:GROQ_API_KEY

# Explicit overwrite
$env:GROQ_API_KEY = "gsk_new_key_here"
python scripts/import_groq_key.py --replace
Remove-Item Env:GROQ_API_KEY
```

---

## Biometric Embeddings

`BiometricStore` (`src/jarvis/security/biometrics.py`) uses the same Win32 DPAPI protection to store opt-in face embeddings as `.bio.bin` files under `data/biometrics/` — never in PostgreSQL or `.env`.
