"""Unit tests for Phase 8.4 lazy loading, startup profiling & PyInstaller packaging."""

from __future__ import annotations

from pathlib import Path
import sys

from jarvis.monitoring.performance import StartupProfiler, check_lazy_optional_imports


def test_optional_heavy_packages_are_lazy_loaded() -> None:
    # Importing core/voice/vision packages must not eagerly import heavy ML/CV libs
    import jarvis.core.engine  # noqa: F401
    import jarvis.vision  # noqa: F401
    import jarvis.voice  # noqa: F401

    eager = check_lazy_optional_imports(sys.modules)
    assert eager == []


def test_startup_profiler_records_phase_timings() -> None:
    prof = StartupProfiler()
    with prof.measure("core_assemble_ms"):
        _ = 1 + 1
    prof.record("cold_start_ms", 850.0)
    prof.record("first_token_ms", 320.0)

    snap = prof.snapshot()
    assert snap["cold_start_ms"] == 850.0
    assert snap["first_token_ms"] == 320.0
    assert "core_assemble_ms" in snap
    assert prof.within_stretch_targets() is True


def test_packaging_spec_and_builder_exclude_secrets_and_dev_data(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    spec_text = (root / "packaging" / "jarvis.spec").read_text(encoding="utf-8")
    assert ".env" not in spec_text or "excludes" in spec_text
    assert "secrets" in spec_text
    assert "biometrics" in spec_text
    assert "PySide6" in spec_text or "jarvis" in spec_text

    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_windows", root / "packaging" / "build_windows.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.validate_packaging_layout(root) is True

    # Release manifest & bundle audit
    fake_dist = tmp_path / "dist" / "JARVIS"
    fake_dist.mkdir(parents=True)
    (fake_dist / "JARVIS.exe").write_bytes(b"MZ_FAKE_EXE")
    manifest = mod.generate_release_manifest(fake_dist)
    assert manifest["bundle_audit_clean"] is True
    assert len(manifest["artifact_sha256"]) == 64

    # Forbidden secret file in dist fails bundle audit
    (fake_dist / ".env").write_text("SECRET=1", encoding="utf-8")
    assert mod.audit_dist_bundle(fake_dist) == [".env"]
