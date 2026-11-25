"""Streamlit UI tests driven through ``st.testing.v1.AppTest``.

These runs execute the real ``app.py`` in a scripted runtime: widgets, session
state, the ``st.status`` pipeline and rendering of generated results. The Gemini
call is stubbed at the ``generate_test_suite`` seam, so no network is used; the
network-facing behaviour is covered by ``test_pipeline_integration.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from conftest import VALID_SUITE_JSON, make_video_bytes
from models_registry import (
    AIStudioCredentials,
    AnalysisConfig,
    AnalysisResult,
    BACKEND_AI_STUDIO,
    ExecutionProgress,
    VertexCredentials,
    VideoTestSuiteError,
)
from schemas import TestSuite, normalize_suite

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")

ANALYSIS_RESULT = AnalysisResult(
    suite=normalize_suite(TestSuite.model_validate(json.loads(json.dumps(VALID_SUITE_JSON)))),
    model_id="gemini-3.8-flash",
    backend=BACKEND_AI_STUDIO,
    latency_seconds=42.5,
    input_tokens=12345,
    output_tokens=678,
    thinking_tokens=90,
    estimated_cost_usd=0.0123,
    fps=1.0,
    media_resolution="HIGH",
    thinking_level="medium",
    upload_mode="Gemini Files API",
    prompt_characters=1800,
)


def _new_app() -> AppTest:
    app = AppTest.from_file(APP_PATH, default_timeout=120)
    app.session_state["analysis_result"] = None
    app.session_state["editor_version"] = 0
    app.session_state["last_error"] = None
    app.session_state["upload_signature"] = ""
    return app


def _ui_label(element) -> str:  # noqa: ANN001 - AppTest element
    return str(getattr(element.proto, "label", "") or "")


def _sidebar_widgets(app: AppTest) -> dict[str, object]:
    widgets: dict[str, object] = {}
    for collection in (
        app.sidebar.radio,
        app.sidebar.selectbox,
        app.sidebar.slider,
        app.sidebar.text_input,
        app.sidebar.text_area,
    ):
        for widget in collection:
            if _ui_label(widget):
                widgets[_ui_label(widget)] = widget
    return widgets


def _upload_video(app: AppTest) -> AppTest:
    if not app.get("file_uploader"):
        app.run()  # AppTest needs one run before widgets can be scripted
    uploader = app.get("file_uploader")[0]
    uploader.upload("walkthrough.mp4", make_video_bytes(), "video/mp4")
    return app.run()


def _metrics(app: AppTest) -> dict[str, str]:
    return {_ui_label(metric): metric.value for metric in app.metric}


# ---------------------------------------------------------------------------
# Initial layout
# ---------------------------------------------------------------------------
def test_initial_render_has_all_sidebar_controls() -> None:
    app = _new_app().run()
    assert not app.exception
    widgets = _sidebar_widgets(app)
    for label in (
        "Execution Backend",
        "GEMINI_API_KEY",
        "Model",
        "Sampling Rate (fps)",
        "Media Resolution",
        "Thinking Level",
        "Module / Feature Name",
        "Target User Roles",
        "Output Focus",
        "Extra PRD Notes",
    ):
        assert label in widgets, f"missing sidebar control: {label}"

    # Defaults from the spec
    assert widgets["Execution Backend"].value == BACKEND_AI_STUDIO
    assert widgets["Model"].value == "gemini-3.8-flash"
    assert widgets["Sampling Rate (fps)"].value == 1.0
    assert widgets["Media Resolution"].value == "HIGH"
    assert widgets["Thinking Level"].value == "medium"
    assert widgets["Output Focus"].value == "Balanced"
    # thinking dropdown never offers "minimal"
    assert list(widgets["Thinking Level"].options) == ["low", "medium", "high"]


def test_slider_bounds_come_from_registry() -> None:
    app = _new_app().run()
    slider = app.sidebar.slider[0]
    assert slider.proto.min == pytest.approx(0.5)
    assert slider.proto.max == pytest.approx(3.0)
    assert slider.proto.step == pytest.approx(0.5)


def test_model_dropdown_is_driven_by_registry() -> None:
    from models_registry import MODELS_REGISTRY

    app = _new_app().run()
    model_box = _sidebar_widgets(app)["Model"]
    rendered = list(model_box.options)
    assert len(rendered) == len(MODELS_REGISTRY)
    for option, model_id in zip(rendered, MODELS_REGISTRY):
        entry = MODELS_REGISTRY[model_id]
        assert model_id in option and entry["label"] in option
        assert f"${entry['input_price_per_million']:.2f}" in option


def test_file_uploader_accepts_required_types() -> None:
    app = _new_app().run()
    uploader = app.get("file_uploader")[0]
    allowed = set(uploader.allowed_type)
    assert {".mp4", ".mov", ".webm", ".mkv"} <= allowed


def test_generate_button_disabled_until_upload() -> None:
    app = _new_app().run()
    button = next(b for b in app.button if b.label == "Generate QA Test Cases")
    assert button.disabled is True

    app = _upload_video(app)
    button = next(b for b in app.button if b.label == "Generate QA Test Cases")
    assert button.disabled is False


def test_upload_shows_preview_and_config_summary() -> None:
    app = _upload_video(_new_app())
    assert not app.exception
    video_elements = app.get("video")
    assert len(video_elements) == 1
    summaries = [frame.value for frame in app.dataframe]
    assert summaries, "config summary table should be rendered"
    summary = summaries[0]
    assert "walkthrough.mp4" in summary["Value"].tolist()
    assert "gemini-3.8-flash" in summary["Value"].tolist()
    assert "HIGH" in summary["Value"].tolist()


def test_missing_api_key_warns_and_credentials_input_appears() -> None:
    app = _new_app().run()
    warnings = [warning.value for warning in app.sidebar.warning]
    assert any("No API key" in message for message in warnings)

    api_key_box = _sidebar_widgets(app)["GEMINI_API_KEY"]
    api_key_box.input("test-key-123")
    app.run()
    successes = [success.value for success in app.sidebar.success]
    assert any("API key supplied" in message for message in successes)


def test_api_key_falls_back_to_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    app = _new_app().run()
    captions = [caption.value for caption in app.sidebar.caption]
    assert any("environment" in caption for caption in captions)


# ---------------------------------------------------------------------------
# Backend toggle
# ---------------------------------------------------------------------------
def test_vertex_backend_swaps_credential_inputs() -> None:
    app = _new_app()
    app.run()
    _sidebar_widgets(app)["Execution Backend"].set_value("Enterprise Vertex AI")
    app.run()
    assert not app.exception
    widgets = _sidebar_widgets(app)
    assert "GCP Project ID" in widgets
    assert "Vertex Location" in widgets
    assert widgets["Vertex Location"].value == "global"
    assert "Cloud Storage URI (optional)" in widgets
    assert "GEMINI_API_KEY" not in widgets


def test_vertex_backend_warns_on_non_gcs_uri() -> None:
    app = _new_app()
    app.run()
    _sidebar_widgets(app)["Execution Backend"].set_value("Enterprise Vertex AI")
    app.run()
    _sidebar_widgets(app)["Cloud Storage URI (optional)"].input("s3://bucket/demo.mp4")
    app.run()
    warnings = [warning.value for warning in app.sidebar.warning]
    assert any("gs://" in message for message in warnings)


# ---------------------------------------------------------------------------
# Generation flow
# ---------------------------------------------------------------------------
def _patch_generator(monkeypatch: pytest.MonkeyPatch, behavior) -> list[dict]:  # noqa: ANN001
    calls: list[dict] = []

    def fake_generate(config, credentials, **kwargs):  # noqa: ANN001
        calls.append(
            {"config": config, "credentials": credentials, **kwargs}
        )
        progress: ExecutionProgress = kwargs.get("progress") or ExecutionProgress()
        progress.stage("upload", "Uploading video" )
        progress.stage("processing", "Processing video")
        progress.stage("analyzing", "Analyzing video + audio")
        progress.stage("validating", "Validating schema")
        return behavior(config, credentials, kwargs)

    import models_registry

    monkeypatch.setattr(models_registry, "generate_test_suite", fake_generate)
    return calls


def _import_app(monkeypatch: pytest.MonkeyPatch):
    """app.py calls ``models_registry.generate_test_suite`` so patching is enough."""

    import app as app_module  # noqa: F401 - imported for side effect

    return app_module


def test_generation_success_renders_results_and_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    _import_app(monkeypatch)
    calls = _patch_generator(monkeypatch, lambda *_: ANALYSIS_RESULT)

    app = _upload_video(_new_app())
    next(b for b in app.button if b.label == "Generate QA Test Cases").click()
    app.run()

    assert not app.exception, [e.message for e in app.exception]
    assert len(calls) == 1
    assert isinstance(calls[0]["config"], AnalysisConfig)
    assert isinstance(calls[0]["credentials"], AIStudioCredentials)
    assert calls[0]["credentials"].api_key in {"", "test-key-123"}  # env empty in tests
    assert calls[0]["video_path"] is not None
    assert not Path(calls[0]["video_path"]).exists(), "temp upload must be deleted"

    metrics = _metrics(app)
    assert metrics["Total Test Cases"] == "3"
    assert metrics["Input Tokens"] == "12,345"
    assert metrics["Output + Thinking Tokens"] == "768"
    assert metrics["Est. Cost ($ USD)"] == "$0.0123"
    assert metrics["Latency (s)"] == "42.5"

    # status reached complete state
    statuses = app.get("status")
    assert statuses and statuses[0].state == "complete"

    # summary + editable business rules + main test-case matrix
    assert any("Promo code redemption" in area.value for area in app.text_area)
    assert any(
        "Promo codes expire after 30 days." in area.value for area in app.text_area
    )
    editors = app.get("data_editor")
    assert len(editors) == 1, "only the test-case matrix is an editable table"
    main_editor = editors[0]
    assert list(main_editor.value.columns) == [
        "ID",
        "Title",
        "Category",
        "Priority",
        "Video Timestamp",
        "Source Basis",
        "Preconditions",
        "Steps",
    ]
    assert len(main_editor.value) == 3

    downloads = {button.label: button for button in app.get("download_button")}
    assert set(downloads) == {"Download CSV", "Download JSON", "Download Gherkin"}


def test_generation_survives_reruns_and_keeps_edits(monkeypatch: pytest.MonkeyPatch) -> None:
    _import_app(monkeypatch)
    calls = _patch_generator(monkeypatch, lambda *_: ANALYSIS_RESULT)
    app = _upload_video(_new_app())
    next(b for b in app.button if b.label == "Generate QA Test Cases").click()
    app.run()
    assert len(calls) == 1

    # Trigger reruns that do not click generate: results must persist.
    app.run()
    _sidebar_widgets(app)["Media Resolution"].set_value("LOW")
    app.run()
    assert not app.exception
    assert len(calls) == 1, "rerun must not regenerate test cases"
    assert _metrics(app)["Total Test Cases"] == "3"

    # Downloads keep working after reruns.
    assert len(app.get("download_button")) == 3


def test_generation_error_is_shown_and_state_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    _import_app(monkeypatch)

    def boom(*_args, **_kwargs):
        raise VideoTestSuiteError("Gemini rejected the uploaded video during processing: bad codec")

    calls = _patch_generator(monkeypatch, boom)
    app = _upload_video(_new_app())
    next(b for b in app.button if b.label == "Generate QA Test Cases").click()
    app.run()

    assert not app.exception
    assert len(calls) == 1
    errors = [error.value for error in app.error]
    assert any("bad codec" in message for message in errors)
    captured_error = app.session_state["last_error"]
    assert "bad codec" in captured_error
    statuses = app.get("status")
    assert statuses and statuses[0].state == "error"
    assert not app.get("download_button")


def test_temp_upload_is_deleted_even_when_gemini_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _import_app(monkeypatch)
    seen: list[str] = []

    def boom(config, credentials, kwargs):  # noqa: ANN001
        # the SDK layer failed before cleaning up its own temp copy
        seen.append(str(kwargs["video_path"]))
        raise VideoTestSuiteError("upload rejected")

    _patch_generator(monkeypatch, boom)
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    # Sequence matters: stage the upload on an unpatched run, then patch, then click.
    app = _upload_video(_new_app())
    next(b for b in app.button if b.label == "Generate QA Test Cases").click()
    app.run()

    assert not app.exception
    assert seen, "the generator should have been invoked"
    assert not Path(seen[0]).exists(), "app.py must unlink its temp upload in finally"
    assert list(tmp_path.glob("video-to-test-case-*")) == []


def test_unexpected_exception_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    _import_app(monkeypatch)

    def explode(*_args, **_kwargs):
        raise RuntimeError("socket closed")

    _patch_generator(monkeypatch, explode)
    app = _upload_video(_new_app())
    next(b for b in app.button if b.label == "Generate QA Test Cases").click()
    app.run()
    assert not app.exception
    assert any("socket closed" in error.value for error in app.error)


def test_vertex_generation_passes_bytes_or_uri(monkeypatch: pytest.MonkeyPatch) -> None:
    _import_app(monkeypatch)
    calls = _patch_generator(monkeypatch, lambda *_: ANALYSIS_RESULT)

    app = _new_app()
    app.run()
    _sidebar_widgets(app)["Execution Backend"].set_value("Enterprise Vertex AI")
    app.run()
    _sidebar_widgets(app)["GCP Project ID"].input("demo-project")
    app.run()
    app = _upload_video(app)
    next(b for b in app.button if b.label == "Generate QA Test Cases").click()
    app.run()

    assert len(calls) == 1
    call = calls[0]
    assert isinstance(call["credentials"], VertexCredentials)
    assert call["credentials"].project_id == "demo-project"
    assert call["credentials"].location == "global"
    assert call["video_bytes"] == make_video_bytes()
    assert call["gcs_uri"] is None


def test_vertex_generation_uses_gcs_uri_without_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    _import_app(monkeypatch)
    calls = _patch_generator(monkeypatch, lambda *_: ANALYSIS_RESULT)

    app = _new_app()
    app.run()
    _sidebar_widgets(app)["Execution Backend"].set_value("Enterprise Vertex AI")
    app.run()
    _sidebar_widgets(app)["GCP Project ID"].input("demo-project")
    _sidebar_widgets(app)["Cloud Storage URI (optional)"].input("gs://bucket/demo.mp4")
    app.run()
    app = _upload_video(app)
    next(b for b in app.button if b.label == "Generate QA Test Cases").click()
    app.run()

    call = calls[0]
    assert call["gcs_uri"] == "gs://bucket/demo.mp4"
    assert call["video_bytes"] is None


# ---------------------------------------------------------------------------
# Exports rendered by the UI
# ---------------------------------------------------------------------------
def _download(app: AppTest, label: str) -> bytes:
    """Return the payload handed to ``st.download_button`` on the last run."""

    key = {"Download CSV": "csv", "Download JSON": "json", "Download Gherkin": "gherkin"}[label]
    payloads = app.session_state["export_payloads"]
    assert key in payloads, f"no download payload published for {label}"
    return payloads[key]


def test_download_payloads_are_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    _import_app(monkeypatch)
    _patch_generator(monkeypatch, lambda *_: ANALYSIS_RESULT)
    app = _upload_video(_new_app())
    next(b for b in app.button if b.label == "Generate QA Test Cases").click()
    app.run()

    csv_bytes = _download(app, "Download CSV")
    header = csv_bytes.decode("utf-8").splitlines()[0]
    assert header == "ID,Title,Category,Priority,Video Timestamp,Source Basis,Preconditions,Steps,Step Count"

    json_bytes = _download(app, "Download JSON")
    payload = json.loads(json_bytes)
    assert len(payload["test_cases"]) == 3
    assert payload["test_cases"][0]["steps"]

    feature_text = _download(app, "Download Gherkin").decode("utf-8")
    assert feature_text.startswith("Feature: ")
    assert feature_text.count("Scenario:") == 3
    assert "@priority-high" in feature_text


def test_exports_reflect_edited_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    _import_app(monkeypatch)
    _patch_generator(monkeypatch, lambda *_: ANALYSIS_RESULT)
    app = _upload_video(_new_app())
    next(b for b in app.button if b.label == "Generate QA Test Cases").click()
    app.run()

    summary_area = next(area for area in app.text_area if area.label == "Feature summary")
    summary_area.set_value("Edited by a QA lead in review.")
    app.run()

    payload = json.loads(_download(app, "Download JSON"))
    assert payload["feature_summary"] == "Edited by a QA lead in review."
    assert "Edited by a QA lead in review." in _download(app, "Download Gherkin").decode("utf-8")
