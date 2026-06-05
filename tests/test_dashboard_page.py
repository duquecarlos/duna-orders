from __future__ import annotations

import importlib.util
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def _load_dashboard_page_module():
    module_path = Path(__file__).resolve().parents[1] / "pages" / "3_Dashboard.py"
    spec = importlib.util.spec_from_file_location(
        "dashboard_page_for_test",
        module_path,
    )
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


def test_dashboard_page_shows_friendly_error_when_scenario_load_fails(
    monkeypatch,
) -> None:
    module = _load_dashboard_page_module()
    captured: dict[str, Exception] = {}

    def raise_scenario_error(*args, **kwargs):
        raise RuntimeError("simulated dashboard load failure")

    def capture_dashboard_error(error: Exception) -> None:
        captured["error"] = error

    monkeypatch.setattr(module, "sheets_request_context", lambda storage: nullcontext())
    monkeypatch.setattr(
        module,
        "run_locked_dashboard_read_scenario",
        raise_scenario_error,
    )
    monkeypatch.setattr(module, "render_dashboard_load_error", capture_dashboard_error)

    module._render_dashboard_body(
        storage=object(),
        tenant_id="tenant",
        now=datetime(2026, 5, 27, 12, 0, tzinfo=ZoneInfo("America/Bogota")),
        timezone_name="America/Bogota",
        dashboard_mode="runtime",
    )

    assert isinstance(captured["error"], RuntimeError)
    assert str(captured["error"]) == "simulated dashboard load failure"