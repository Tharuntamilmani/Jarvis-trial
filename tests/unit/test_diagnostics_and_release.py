"""Unit & privacy-regression tests for Phase 8.7 Diagnostics & Release Validation."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

from jarvis.config import Settings
from jarvis.core.engine import CoreEngine
from jarvis.monitoring.diagnostics import DiagnosticsService


@pytest.mark.asyncio
async def test_unified_diagnostic_report_is_privacy_safe_and_complete(
    tmp_path: Path,
) -> None:
    settings = Settings(
        log_dir=tmp_path / "logs",
        audit_log_path=tmp_path / "logs" / "audit.log",
    )
    engine = CoreEngine(settings)
    engine.assemble()

    diag = DiagnosticsService(engine=engine)
    report = await diag.collect()

    expected_sections = {
        "core_engine",
        "postgresql",
        "pgvector",
        "ollama",
        "voice",
        "vision",
        "agents",
        "security",
        "tools",
        "packaging",
        "optional_dependencies",
    }
    assert expected_sections.issubset(report.keys())

    serialized = json.dumps(report)
    for forbidden in (
        "gsk_",
        "raw_audio",
        "embedding",
        "screenshot",
        "clipboard",
        "file_content",
    ):
        assert forbidden not in serialized.lower()

    export_path = tmp_path / "diag_report.json"
    diag.export_report(report, export_path)
    assert export_path.is_file()
    loaded = json.loads(export_path.read_text(encoding="utf-8"))
    assert loaded["packaging"]["version"] == "1.0.0"


@pytest.mark.asyncio
async def test_diagnostics_reports_missing_optional_dependencies_gracefully(
    tmp_path: Path,
) -> None:
    settings = Settings(
        log_dir=tmp_path / "logs",
        audit_log_path=tmp_path / "logs" / "audit.log",
    )
    engine = CoreEngine(settings)
    engine.assemble()

    diag = DiagnosticsService(engine=engine)
    with patch.dict(
        sys.modules,
        {"cv2": None, "insightface": None, "mediapipe": None, "piper": None},
    ):
        report = await diag.collect()

    deps = report["optional_dependencies"]
    assert deps["cv2"]["available"] is False
    assert deps["insightface"]["available"] is False
    assert deps["mediapipe"]["available"] is False
    assert deps["piper"]["available"] is False
