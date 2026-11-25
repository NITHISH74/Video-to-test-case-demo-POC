"""End-to-end pipeline tests through the real ``google-genai`` SDK.

A local HTTP server speaks the Gemini REST protocol, and the SDK client is
pointed at it with ``HttpOptions(base_url=...)``. That means every assertion
below exercises real request serialization, real resumable uploads, real polling
and the real cleanup code paths - without a network or an API key.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from google.genai import types

import models_registry as registry
from conftest import VALID_SUITE_JSON, make_video_bytes
from models_registry import (
    BACKEND_AI_STUDIO,
    BACKEND_VERTEX,
    AIStudioCredentials,
    AnalysisConfig,
    ExecutionProgress,
    VideoTestSuiteError,
    VertexCredentials,
    estimate_cost_usd,
    generate_test_suite,
    wait_for_file_active,
)
from schemas import PromptContext


# ---------------------------------------------------------------------------
# Harness: point the SDK client at the local fake server
# ---------------------------------------------------------------------------
@pytest.fixture
def sdk_to_fake(monkeypatch: pytest.MonkeyPatch, fake_gemini):
    """Force every created client to talk to the fake server."""

    original_create = registry.create_ai_studio_client

    def patched(credentials: AIStudioCredentials):
        assert credentials.api_key, "test should never create an unauthenticated client"
        import google.genai as genai

        return genai.Client(
            api_key=credentials.api_key,
            http_options=types.HttpOptions(
                base_url=fake_gemini.base_url, api_version="v1beta"
            ),
        )

    monkeypatch.setattr(registry, "create_ai_studio_client", patched)
    assert original_create is not patched  # keep reference for clarity
    # keep polling quick but not a busy loop
    monkeypatch.setattr(registry, "POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(registry, "MAX_POLL_ATTEMPTS", 20)
    return fake_gemini


def _run(config: AnalysisConfig, video_path: Path, progress: ExecutionProgress | None = None):
    return generate_test_suite(
        config,
        AIStudioCredentials(api_key="unit-test-key"),
        video_path=video_path,
        video_filename=video_path.name,
        progress=progress,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
def test_full_pipeline_returns_normalized_suite_and_telemetry(sdk_to_fake, video_file: Path) -> None:
    result = _run(AnalysisConfig(), video_file)

    assert len(result.suite.test_cases) == 3
    assert [case.id for case in result.suite.test_cases] == ["TC-001", "TC-002", "TC-003"]
    assert result.suite.test_cases[1].source_basis == "Spoken by Presenter"
    assert result.upload_mode == "Gemini Files API"
    assert result.input_tokens == 12345
    assert result.output_tokens == 678
    assert result.thinking_tokens == 90
    assert result.latency_seconds > 0
    assert result.estimated_cost_usd == pytest.approx(
        estimate_cost_usd("gemini-3.8-flash", 12345, 678, 90)
    )
    assert result.fps == 1.0
    assert result.media_resolution == "HIGH"
    assert result.thinking_level == "medium"


def test_request_wire_format_follows_gemini_3x_rules(sdk_to_fake, video_file: Path) -> None:
    _run(
        AnalysisConfig(fps=2.0, media_resolution="LOW", thinking_level="high"),
        video_file,
    )
    body = sdk_to_fake.generated_body
    assert body is not None

    parts = body["contents"][0]["parts"]
    assert len(parts) == 2
    # 1) video part comes first and carries fps metadata
    assert parts[0]["fileData"]["file_uri"].endswith("files/mock-video-123")
    assert parts[0]["fileData"]["mime_type"] == "video/mp4"
    assert parts[0]["videoMetadata"]["fps"] == 2.0
    # 2) the prompt comes second
    assert "CONTEXT" in parts[1]["text"]
    assert "Output focus" in parts[1]["text"]

    generation = body["generationConfig"]
    assert generation["responseMimeType"] == "application/json"
    assert generation["mediaResolution"] == "MEDIA_RESOLUTION_LOW"
    assert generation["thinkingConfig"]["thinking_level"] == "HIGH"
    # responseSchema is embedded as a JSON schema (enum values included)
    schema_text = json.dumps(generation["responseSchema"])
    assert "video_timestamp" in schema_text
    assert "Spoken by Presenter" in schema_text
    # 3) legacy sampling params are absent
    for forbidden in ("temperature", "topP", "topK"):
        assert forbidden not in generation
    # 4) system instruction present
    assert "senior QA engineer" in body["systemInstruction"]["parts"][0]["text"]


def test_request_includes_user_context_in_prompt(sdk_to_fake, video_file: Path) -> None:
    config = AnalysisConfig(
        prompt_context=PromptContext(
            feature_name="Checkout - Promo Codes",
            target_roles="Shopper, Admin",
            output_focus="Security & Permissions",
            extra_notes="Codes are single use.",
        )
    )
    _run(config, video_file)
    prompt = sdk_to_fake.generated_body["contents"][0]["parts"][1]["text"]
    assert "Checkout - Promo Codes" in prompt
    assert "Shopper, Admin" in prompt
    assert "Security & Permissions" in prompt
    assert "Codes are single use." in prompt


def test_upload_uses_declared_mime_and_display_name(sdk_to_fake, video_file: Path) -> None:
    _run(AnalysisConfig(), video_file)
    init_request = next(
        request for request in sdk_to_fake.requests
        if request["method"] == "POST" and request["path"].startswith("/upload/")
    )
    payload = json.loads(init_request["body"])
    assert payload["file"]["mime_type"] == "video/mp4"
    assert payload["file"]["display_name"] == "walkthrough.mp4"
    assert sdk_to_fake.uploaded_bytes == make_video_bytes()


# ---------------------------------------------------------------------------
# Cleanup guarantees
# ---------------------------------------------------------------------------
def test_successful_run_deletes_remote_file_and_temp_copy(sdk_to_fake, video_file: Path) -> None:
    _run(AnalysisConfig(), video_file)
    assert sdk_to_fake.deleted is True
    assert "/v1beta/files/mock-video-123" in sdk_to_fake.paths("DELETE")


def test_failed_processing_still_cleans_up(sdk_to_fake) -> None:
    sdk_to_fake.states = ["PROCESSING", "FAILED"]
    # the app always stages uploads in the system temp dir, so that copy is ours
    handle = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    handle.write(make_video_bytes())
    handle.close()
    input_path = Path(handle.name)

    try:
        with pytest.raises(VideoTestSuiteError) as info:
            _run(AnalysisConfig(), input_path)
        assert "rejected the uploaded video" in str(info.value)
        assert sdk_to_fake.deleted is True
        assert not input_path.exists(), "temp copy must be removed even when processing fails"
    finally:
        input_path.unlink(missing_ok=True)


def test_caller_owned_paths_are_never_deleted() -> None:
    import tempfile as tempfile_module

    from models_registry import remove_local_temp_copy

    outside_dir = Path.cwd() / ".probe-caller-owned"
    outside_dir.mkdir(exist_ok=True)
    outside = outside_dir / "customer-video.mp4"
    outside.write_bytes(b"keep me")
    try:
        remove_local_temp_copy(outside)
        assert outside.exists(), "paths outside the temp dir must be left alone"

        inside = Path(tempfile_module.gettempdir()) / "vttc-probe.mp4"
        inside.write_bytes(b"delete me")
        remove_local_temp_copy(inside)
        assert not inside.exists()

        remove_local_temp_copy(None)  # must not raise
    finally:
        outside.unlink(missing_ok=True)
        outside_dir.rmdir()


def test_generation_api_error_propagates_and_cleans_up(sdk_to_fake, video_file: Path) -> None:
    sdk_to_fake.generate_status_code = 429
    with pytest.raises(Exception) as info:
        _run(AnalysisConfig(), video_file)
    assert "429" in str(info.value) or "RESOURCE_EXHAUSTED" in str(info.value)
    assert sdk_to_fake.deleted is True


def test_invalid_schema_response_raises_video_error(sdk_to_fake, video_file: Path) -> None:
    sdk_to_fake.response_json = {"feature_summary": "missing everything else"}
    with pytest.raises(VideoTestSuiteError) as info:
        _run(AnalysisConfig(), video_file)
    assert "schema" in str(info.value).lower()
    assert sdk_to_fake.deleted is True


def test_empty_test_cases_raise_actionable_error(sdk_to_fake, video_file: Path) -> None:
    sdk_to_fake.response_json = {
        "feature_summary": "Nothing observed.",
        "presenter_business_rules": [],
        "test_cases": [],
    }
    with pytest.raises(VideoTestSuiteError) as info:
        _run(AnalysisConfig(), video_file)
    assert "at least 1 item" in str(info.value) or "zero test cases" in str(info.value)
    assert sdk_to_fake.deleted is True


def test_timestamp_normalization_applies_to_model_output(sdk_to_fake, video_file: Path) -> None:
    payload = json.loads(json.dumps(VALID_SUITE_JSON))
    payload["test_cases"][0]["video_timestamp"] = "0:12\u20130:41"
    sdk_to_fake.response_json = payload
    result = _run(AnalysisConfig(), video_file)
    assert result.suite.test_cases[0].video_timestamp == "00:12-00:41"


# ---------------------------------------------------------------------------
# Progress hooks
# ---------------------------------------------------------------------------
def test_progress_stages_are_reported_in_order(sdk_to_fake, video_file: Path) -> None:
    stages: list[tuple[str, str]] = []
    details: list[str] = []
    progress = ExecutionProgress(
        on_stage=lambda key, label: stages.append((key, label)),
        on_detail=details.append,
    )
    _run(AnalysisConfig(), video_file, progress=progress)
    assert [stage for stage, _ in stages] == [
        "upload",
        "processing",
        "analyzing",
        "validating",
    ]
    # an upload that already returned PROCESSING needs no extra detail lines
    assert details == []


# ---------------------------------------------------------------------------
# Polling behaviour
# ---------------------------------------------------------------------------
class _SequenceFiles:
    """Minimal stand-in for ``client.files`` with scripted states."""

    def __init__(self, states: list[object], missing_error: Exception | None = None) -> None:
        self.states = list(states)
        self.calls = 0
        self.missing_error = missing_error

    def get(self, name: str):  # noqa: ANN001
        self.calls += 1
        state = self.states.pop(0)
        if isinstance(state, Exception):
            raise state

        class _File:
            def __init__(self, value: str) -> None:
                self.state = types.FileState(value)
                self.name = name
                self.error = None

        return _File(state)  # type: ignore[arg-type]


class _StubClient:
    def __init__(self, files: _SequenceFiles) -> None:
        self.files = files


def test_wait_for_file_active_polls_until_active() -> None:
    files = _SequenceFiles(["PROCESSING", "PROCESSING", "ACTIVE"])
    sleeps: list[float] = []
    file_obj = wait_for_file_active(
        _StubClient(files),  # type: ignore[arg-type]
        "files/abc",
        sleep=sleeps.append,
        poll_interval=0.5,
    )
    assert files.calls == 3
    assert sleeps == [0.5, 0.5]
    assert file_obj.state == types.FileState.ACTIVE


def test_wait_for_file_active_fails_fast_on_failed_state() -> None:
    files = _SequenceFiles(["PROCESSING", "FAILED"])
    with pytest.raises(VideoTestSuiteError) as info:
        wait_for_file_active(
            _StubClient(files),  # type: ignore[arg-type]
            "files/abc",
            sleep=lambda _: None,
        )
    assert files.calls == 2
    assert "rejected" in str(info.value)


def test_wait_for_file_active_times_out() -> None:
    files = _SequenceFiles(["PROCESSING"] * 10)
    with pytest.raises(VideoTestSuiteError) as info:
        wait_for_file_active(
            _StubClient(files),  # type: ignore[arg-type]
            "files/abc",
            sleep=lambda _: None,
            max_attempts=3,
        )
    assert "Timed out" in str(info.value)
    assert files.calls == 3


# ---------------------------------------------------------------------------
# Local temp-file lifecycle through the public API
# ---------------------------------------------------------------------------
def test_temp_copy_is_deleted_by_the_pipeline(sdk_to_fake, tmp_path: Path) -> None:
    temp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    temp.write(make_video_bytes())
    temp.close()
    temp_path = Path(temp.name)
    assert temp_path.exists()

    _run(AnalysisConfig(), temp_path)
    assert not temp_path.exists(), "the pipeline must unlink its temp copy in finally"


# ---------------------------------------------------------------------------
# Vertex AI path (client-level, no network) + input guards
# ---------------------------------------------------------------------------
def test_vertex_inline_upload_rejects_oversized_video(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "MAX_DIRECT_UPLOAD_BYTES", 4)
    with pytest.raises(VideoTestSuiteError) as info:
        generate_test_suite(
            AnalysisConfig(backend=BACKEND_VERTEX),
            VertexCredentials(project_id="demo-project", location="global"),
            video_bytes=b"12345",
            video_filename="walkthrough.mp4",
        )
    assert "256 MB" in str(info.value)


def test_vertex_rejects_non_gcs_uri() -> None:
    with pytest.raises(VideoTestSuiteError) as info:
        generate_test_suite(
            AnalysisConfig(backend=BACKEND_VERTEX),
            VertexCredentials(project_id="demo-project"),
            gcs_uri="s3://bucket/demo.mp4",
            video_filename="walkthrough.mp4",
        )
    assert "gs://" in str(info.value)


def test_vertex_requires_some_video_source() -> None:
    with pytest.raises(VideoTestSuiteError) as info:
        generate_test_suite(
            AnalysisConfig(backend=BACKEND_VERTEX),
            VertexCredentials(project_id="demo-project"),
            video_filename="walkthrough.mp4",
        )
    assert "gs://" in str(info.value)


def test_backend_credentials_must_match_backend() -> None:
    with pytest.raises(VideoTestSuiteError):
        generate_test_suite(
            AnalysisConfig(backend=BACKEND_AI_STUDIO),
            VertexCredentials(project_id="demo-project"),
            video_filename="walkthrough.mp4",
        )
    with pytest.raises(VideoTestSuiteError):
        generate_test_suite(
            AnalysisConfig(backend=BACKEND_VERTEX),
            AIStudioCredentials(api_key="k"),
            video_filename="walkthrough.mp4",
        )


def test_unsupported_extension_is_rejected_before_any_client_work() -> None:
    with pytest.raises(VideoTestSuiteError) as info:
        generate_test_suite(
            AnalysisConfig(),
            AIStudioCredentials(api_key="k"),
            video_path="/tmp/whatever.mp3",
            video_filename="screen-recording.mp3",
        )
    assert "Unsupported video type" in str(info.value)


def test_unexpected_upload_failure_is_surfaced(fake_gemini, monkeypatch: pytest.MonkeyPatch, video_file: Path) -> None:
    fake_gemini.upload_status = 400

    import google.genai as genai

    def patched(credentials: AIStudioCredentials):
        return genai.Client(
            api_key=credentials.api_key,
            http_options=types.HttpOptions(base_url=fake_gemini.base_url, api_version="v1beta"),
        )

    monkeypatch.setattr(registry, "create_ai_studio_client", patched)
    with pytest.raises(Exception) as info:
        _run(AnalysisConfig(), video_file)
    assert "400" in str(info.value)
