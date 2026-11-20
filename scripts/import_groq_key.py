"""Import a Groq API key into the DPAPI credential store (Stage 2.3).

Safe credential precedence (never overwrites silently):

1. If ``groq_api_key`` already exists in the DPAPI store → leave it
   unchanged and exit 0. Overwriting requires an explicit ``--replace``
   flag together with a new key.
2. Else, if ``GROQ_API_KEY`` is present in the environment → import it
   into the DPAPI store (explicit, one-time convenience).
3. Else → report the not-configured state and exit 1.

The key is NEVER printed, logged, or written to .env/settings/docs.

Usage:
    python scripts/import_groq_key.py             # import from env if absent
    python scripts/import_groq_key.py --replace   # overwrite an existing key
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from jarvis.security.credentials import CredentialStore  # noqa: E402
from jarvis.config import get_settings  # noqa: E402

CREDENTIAL_NAME = "groq_api_key"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import the Groq API key into the DPAPI credential store"
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Explicitly overwrite an existing stored key",
    )
    args = parser.parse_args()

    store = CredentialStore(get_settings())

    if store.load_secret(CREDENTIAL_NAME) is not None:
        if not args.replace:
            print(
                f"'{CREDENTIAL_NAME}' is already stored — unchanged. "
                "Use --replace to overwrite explicitly."
            )
            return 0
        env_key = os.environ.get("GROQ_API_KEY", "")
        if not env_key.strip():
            print(
                "--replace requires a new key: set GROQ_API_KEY in the "
                "environment for this invocation."
            )
            return 1
        store.store_secret(CREDENTIAL_NAME, env_key.strip())
        print(f"'{CREDENTIAL_NAME}' replaced (value never displayed).")
        return 0

    env_key = os.environ.get("GROQ_API_KEY", "")
    if not env_key.strip():
        print(
            "Groq is not configured: no stored key and no GROQ_API_KEY in "
            "the environment. JARVIS will use local AI only."
        )
        return 1

    store.store_secret(CREDENTIAL_NAME, env_key.strip())
    print(f"'{CREDENTIAL_NAME}' imported into the DPAPI credential store.")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
