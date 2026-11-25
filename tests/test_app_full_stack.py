"""Full-stack UI test: app.py -> models_registry -> real google-genai SDK -> HTTP.

Nothing in the Gemini layer is stubbed here. The only seam replaced is
``genai.Client`` construction (redirected to the local fake Gemini server), so
this test proves the widget values chosen in the sidebar really reach the wire,
the upload/poll/generate/cleanup flow works end to end, and the resulting
telemetry is rendered with the values the API reported.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from google.genai import types
from streamlit.testing.v1 import AppTest

import models_registry
from conftest import make_video_bytes

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture
def full_stack(monkeypatch: pytest.MonkeyPatch, fake_gemini, tmp_path: Path):
    """Redirect every AI Studio client the app builds to the fake Gemini server."""

    monkeypatch.setenv("GEMINI_API_KEY", "env-test-key")

    def fake_client(credentials):  # noqa: ANN001 - AIStudioCredentials
        import google.genai as genai

        assert credentials.api_key == "env-test-key"
        return genai.Client(
            api_key=credentials.api_key,
            http_options=types.HttpOptions(
                base_url=fake_gemini.base_url, api_version="v1beta"
            ),
        )

    monkeypatch.setattr(models_registry, "create_ai_studio_client", fake_client)
    # keep polling fast without busy-looping the fake server
    monkeypatch.setattr(models_registry, "POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(models_registry, "MAX_POLL_ATTEMPTS", 20)
    return fake_gemini


def _app() -> AppTest:
    app = AppTest.from_file(APP_PATH, default_timeout=120)
    for key, value in {
        "analysis_result": None,
        "editor_version": 0,
        "last_error": None,
        "upload_signature": "",
        "export_payloads": {},
    }.items():
        app.session_state[key] = value
    return app


def _generate(app: AppTest) -> AppTest:
    app.get("file_uploader")[0].upload("walkthrough.mp4", make_video_bytes(), "video/mp4")
    app.run()
    next(button for button in app.button if button.label == "Generate QA Test Cases").click()
    return app.run()


def _label(element) -> str:  # noqa: ANN001
    return str(getattr(element.proto, "label", "") or "")


def test_sidebar_choices_reach_the_gemini_request(full_stack) -> None:
    app = _app().run()
    assert not app.exception

    sidebar = {_label(widget): widget for widget in app.sidebar.selectbox}
    sidebar["Model"].set_value("gemini-4-neon")
    sidebar["Media Resolution"].set_value("MEDIUM")
    sidebar["Thinking Level"].set_value("high")
    app.sidebar.slider[0].set_value(2.5)
    app.run()

    text_inputs = {_label(widget): widget for widget in app.sidebar.text_input}
    text_inputs["Module / Feature Name"].input("Checkout - Promo Codes")
    text_inputs["Target User Roles"].input("Shopper, Admin")
    app.run()

    app = _generate(app)
    assert not app.exception, [e.message for e in app.exception]

    # --- the wire request reflects every sidebar choice -------------------
    body = full_stack.generated_body
    assert body is not None
    generation = body["generationConfig"]
    assert generation["mediaResolution"] == "MEDIA_RESOLUTION_MEDIUM"
    assert generation["thinkingConfig"]["thinking_level"] == "HIGH"
    assert "temperature" not in generation and "topP" not in generation and "topK" not in generation

    parts = body["contents"][0]["parts"]
    assert parts[0]["videoMetadata"]["fps"] == 2.5
    assert parts[0]["fileData"]["mime_type"] == "video/mp4"
    assert "Checkout - Promo Codes" in parts[1]["text"]
    assert "Shopper, Admin" in parts[1]["text"]

    # model selection is honoured on the URL
    assert any("gemini-4-neon:generateContent" in path for path in full_stack.paths("POST"))

    # --- Files API lifecycle: upload -> generate -> delete ----------------
    assert any(path.startswith("/upload/") for path in full_stack.paths("POST"))
    assert any(":generateContent" in path for path in full_stack.paths("POST"))
    assert full_stack.deleted is True

    # --- telemetry rendered from the mocked usage metadata ---------------
    metrics = {_label(metric): metric.value for metric in app.metric}
    assert metrics["Total Test Cases"] == "3"
    assert metrics["Input Tokens"] == "12,345"
    assert metrics["Output + Thinking Tokens"] == "768"
    assert float(metrics["Latency (s)"]) >= 0
    # cost for gemini-4-neon: 12345*3.00/1M + (678+90)*15.00/1M
    expected_cost = 12345 * 3.00 / 1_000_000 + 768 * 15.00 / 1_000_000
    assert metrics["Est. Cost ($ USD)"] == f"${expected_cost:.4f}"

    # --- results UI -------------------------------------------------------
    statuses = app.get("status")
    assert statuses and statuses[0].state == "complete"
    editors = app.get("data_editor")
    main_editor = max(editors, key=lambda element: len(element.value))
    assert len(main_editor.value) == 3
    labels = {button.label for button in app.get("download_button")}
    assert labels == {"Download CSV", "Download JSON", "Download Gherkin"}

    exports = app.session_state["export_payloads"]
    payload = json.loads(exports["json"])
    assert [case["id"] for case in payload["test_cases"]] == ["TC-001", "TC-002", "TC-003"]
    feature_text = exports["gherkin"].decode("utf-8")
    assert feature_text.count("Scenario:") == 3


def test_failed_remote_processing_is_reported_and_cleaned(full_stack) -> None:
    full_stack.states = ["PROCESSING", "FAILED"]
    app = _generate(_app().run())
    assert not app.exception
    assert any("rejected the uploaded video" in error.value for error in app.error)
    statuses = app.get("status")
    assert statuses and statuses[0].state == "error"
    assert full_stack.deleted is True
    assert not app.get("download_button")


def test_quota_error_is_wrapped_for_the_user(full_stack) -> None:
    full_stack.generate_status_code = 429
    app = _generate(_app().run())
    assert not app.exception
    messages = " ".join(error.value for error in app.error)
    assert "429" in messages or "RESOURCE_EXHAUSTED" in messages
    assert full_stack.deleted is True


def test_polling_happens_when_remote_processing_is_slow(full_stack) -> None:
    full_stack.states = ["PROCESSING", "PROCESSING", "ACTIVE"]
    app = _generate(_app().run())
    assert not app.exception
    polled = [
        path for path in full_stack.paths("GET") if "/v1beta/files/mock-video-123" in path
    ]
    assert len(polled) == 2, polled
    metrics = {_label(metric): metric.value for metric in app.metric}
    assert metrics["Total Test Cases"] == "3"


def test_generated_results_survive_a_real_rerun(full_stack) -> None:
    app = _generate(_app().run())
    assert not app.exception
    full_stack.requests.clear()

    # rerun without clicking generate (e.g. the user toggles a widget)
    app.sidebar.selectbox[1].set_value("LOW")  # media resolution
    app.run()
    assert not app.exception
    assert full_stack.requests == [], "a rerun must not call the API again"
    metrics = {_label(metric): metric.value for metric in app.metric}
    assert metrics["Total Test Cases"] == "3"


def test_schema_mismatch_response_shows_actionable_error(full_stack) -> None:
    full_stack.response_json = {"feature_summary": "only a summary"}
    app = _generate(_app().run())
    assert not app.exception
    messages = " ".join(error.value for error in app.error)
    assert "schema" in messages.lower()
    assert full_stack.deleted is True
