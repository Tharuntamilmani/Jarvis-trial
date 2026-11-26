"""Windows 11 PyInstaller build, bundle-audit & release manifest generator (Phase 8.4 / 9.3)."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any

_FORBIDDEN_BUNDLE_PATTERNS = (
    ".env",
    "secrets/",
    "biometrics/",
    "*.bio.bin",
    "*.dump",
)

_FORBIDDEN_DIST_FILENAMES = frozenset({".env", "audit.log", "jarvis.log"})
_FORBIDDEN_DIST_SUFFIXES = (".bio.bin", ".dump", ".dump.enc", ".manifest.json")


def validate_packaging_layout(project_root: Path) -> bool:
    """Verify packaging spec exists, targets main.py, and excludes sensitive data."""
    spec_path = project_root / "packaging" / "jarvis.spec"
    if not spec_path.is_file():
        return False
    spec_text = spec_path.read_text(encoding="utf-8")
    if "main.py" not in spec_text or "datas=[]" not in spec_text:
        return False
    return "secrets" in spec_text and "biometrics" in spec_text


def audit_dist_bundle(dist_dir: Path) -> list[str]:
    """Return any forbidden secret, biometric, or DB files found in dist_dir."""
    violations: list[str] = []
    if not dist_dir.exists():
        return violations
    for item in dist_dir.rglob("*"):
        if not item.is_file():
            continue
        name = item.name.lower()
        rel = str(item.relative_to(dist_dir)).replace("\\", "/")
        if (
            name in _FORBIDDEN_DIST_FILENAMES
            or any(name.endswith(sfx) for sfx in _FORBIDDEN_DIST_SUFFIXES)
            or "/secrets/" in f"/{rel}"
            or "/biometrics/" in f"/{rel}"
            or "/backups/" in f"/{rel}"
        ):
            violations.append(rel)
    return violations


def generate_release_manifest(
    dist_dir: Path,
    *,
    version: str = "1.0.0",
    exe_name: str = "JARVIS.exe",
) -> dict[str, Any]:
    """Generate a release manifest with SHA-256 and bundle-security audit."""
    violations = audit_dist_bundle(dist_dir)
    if violations:
        raise ValueError(f"Forbidden files found in distribution: {violations}")
    exe_path = dist_dir / exe_name
    sha256 = ""
    if exe_path.is_file():
        sha256 = hashlib.sha256(exe_path.read_bytes()).hexdigest()
    manifest = {
        "app": "JARVIS",
        "version": version,
        "build_timestamp": datetime.now(timezone.utc).isoformat(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "executable": exe_name,
        "artifact_sha256": sha256,
        "bundle_audit_clean": True,
    }
    manifest_path = dist_dir / "release_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Build JARVIS Windows bundle")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    if not validate_packaging_layout(root):
        print("Packaging layout validation failed")
        return 1
    if args.verify_only:
        print("Packaging layout validated cleanly")
        return 0

    if importlib.util.find_spec("PyInstaller") is None:
        print(
            "PyInstaller is not installed. Install with: pip install -e .[packaging]"
        )
        return 2

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        str(root / "packaging" / "jarvis.spec"),
    ]
    rc = subprocess.run(cmd, check=False).returncode  # noqa: S603
    if rc != 0:
        return rc

    dist_dir = root / "dist" / "JARVIS"
    manifest = generate_release_manifest(dist_dir)
    print(f"Release manifest written: {dist_dir / 'release_manifest.json'}")
    print(f"JARVIS.exe sha256: {manifest['artifact_sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
