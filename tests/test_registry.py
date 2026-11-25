"""Registry contents, pricing, configuration building and SDK usage rules."""

from __future__ import annotations

import inspect

import pytest
from google.genai import types

import models_registry as registry
from models_registry import (
    BACKEND_AI_STUDIO,
    BACKEND_VERTEX,
    DEFAULT_MODEL_ID,
    MODELS_REGISTRY,
    AnalysisConfig,
    AIStudioCredentials,
    VertexCredentials,
    VideoTestSuiteError,
    build_contents,
    build_generation_config,
    create_ai_studio_client,
    create_vertex_client,
    estimate_cost_usd,
    extract_usage,
    format_usd,
    is_supported_video,
    mime_type_for,
    model_label,
    parse_suite_from_response,
)


# ---------------------------------------------------------------------------
# Registry contract
# ---------------------------------------------------------------------------
EXPECTED_MODELS = {
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-4-hydrogen": (1.50, 7.50),
    "gemini-4-neon": (3.00, 15.00),
    "gemini-3.5-flash-lite": (0.30, 2.50),
}


def test_registry_has_exactly_the_four_documented_models() -> None:
    assert set(MODELS_REGISTRY) == set(EXPECTED_MODELS)


@pytest.mark.parametrize(("model_id", "prices"), EXPECTED_MODELS.items())
def test_registry_prices_match_spec(model_id: str, prices: tuple[float, float]) -> None:
    entry = MODELS_REGISTRY[model_id]
    assert entry["input_price_per_million"] == prices[0]
    assert entry["output_price_per_million"] == prices[1]
    assert entry["label"]
    assert entry["description"]


def test_default_model_is_gemini_3_8_flash() -> None:
    assert DEFAULT_MODEL_ID == "gemini-3.8-flash"


def test_model_label_mentions_prices() -> None:
    label = model_label("gemini-3.8-flash")
    assert "gemini-3.8-flash" in label and "$0.75" in label and "$3.75" in label


def test_model_label_follows_backend_pricing() -> None:
    assert model_label("gemini-4-neon", backend=BACKEND_VERTEX).endswith(
        "$3.00 in / $15.00 out per 1M tokens"
    )


def test_iter_registry_choices_matches_registry_order() -> None:
    choices = list(registry.iter_registry_choices())
    assert [model_id for model_id, _ in choices] == list(MODELS_REGISTRY)
    assert all(entry is MODELS_REGISTRY[model_id] for model_id, entry in choices)


def test_only_new_sdk_is_used() -> None:
    source = inspect.getsource(registry)
    # strip the module docstring + comments so prose about the deprecated API is ignored
    code = registry.__doc__ and source.replace(registry.__doc__, "", 1) or source
    code = "\n".join(
        line for line in code.splitlines() if not line.strip().startswith("#")
    )
    assert "from google import genai" in code
    assert "from google.genai import types" in code
    assert "google.generativeai" not in code
    assert "genai.configure(" not in code


# ---------------------------------------------------------------------------
# Mime handling
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("demo.mp4", "video/mp4"),
        ("DEMO.MOV", "video/quicktime"),
        ("clip.webm", "video/webm"),
        ("capture.mkv", "video/x-matroska"),
    ],
)
def test_mime_type_for_supported_videos(filename: str, expected: str) -> None:
    assert mime_type_for(filename) == expected
    assert is_supported_video(filename)


@pytest.mark.parametrize("filename", ["notes.txt", "trailer.avi", "noextension"])
def test_mime_type_for_rejects_other_files(filename: str) -> None:
    assert not is_supported_video(filename)
    with pytest.raises(VideoTestSuiteError):
        mime_type_for(filename)


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------
def test_config_defaults() -> None:
    config = AnalysisConfig()
    assert config.model_id == "gemini-3.8-flash"
    assert config.fps == 1.0
    assert config.media_resolution == "HIGH"
    assert config.thinking_level == "medium"
    assert config.backend == BACKEND_AI_STUDIO
    config.validate()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"model_id": "gemini-9-unknown"},
        {"fps": 0.25},
        {"fps": 4.0},
        {"media_resolution": "ULTRA"},
        {"thinking_level": "minimal"},
        {"thinking_level": "MEDIUM"},
        {"backend": "On Prem"},
    ],
)
def test_config_validation_rejects_bad_values(kwargs: dict) -> None:
    with pytest.raises(VideoTestSuiteError):
        AnalysisConfig(**kwargs).validate()


@pytest.mark.parametrize("fps", [0.5, 1.0, 1.5, 2.0, 2.5, 3.0])
def test_config_accepts_slider_range(fps: float) -> None:
    AnalysisConfig(fps=fps).validate()


def test_minimal_is_never_exposed_in_options() -> None:
    assert registry.THINKING_LEVEL_OPTIONS == ("low", "medium", "high")
    assert "minimal" not in registry.THINKING_LEVEL_OPTIONS


# ---------------------------------------------------------------------------
# GenerateContentConfig rules
# ---------------------------------------------------------------------------
def test_generate_content_config_matches_gemini_3x_rules() -> None:
    config = build_generation_config(
        AnalysisConfig(media_resolution="MEDIUM", thinking_level="high")
    )
    assert isinstance(config, types.GenerateContentConfig)
    assert config.system_instruction
    assert config.media_resolution == types.MediaResolution.MEDIA_RESOLUTION_MEDIUM
    assert isinstance(config.thinking_config, types.ThinkingConfig)
    assert config.thinking_config.thinking_level == types.ThinkingLevel.HIGH
    assert config.response_mime_type == "application/json"
    assert config.response_schema is registry.TestSuite


def test_generate_content_config_never_sets_legacy_sampling_params() -> None:
    for resolution in ("HIGH", "MEDIUM", "LOW"):
        config = build_generation_config(AnalysisConfig(media_resolution=resolution))
        assert config.temperature is None
        assert config.top_p is None
        assert config.top_k is None


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        ("low", types.ThinkingLevel.LOW),
        ("medium", types.ThinkingLevel.MEDIUM),
        ("high", types.ThinkingLevel.HIGH),
    ],
)
def test_thinking_levels_map_to_enum(level: str, expected: types.ThinkingLevel) -> None:
    config = build_generation_config(AnalysisConfig(thinking_level=level))
    assert config.thinking_config.thinking_level == expected


@pytest.mark.parametrize(
    ("resolution", "expected"),
    [
        ("HIGH", types.MediaResolution.MEDIA_RESOLUTION_HIGH),
        ("MEDIUM", types.MediaResolution.MEDIA_RESOLUTION_MEDIUM),
        ("LOW", types.MediaResolution.MEDIA_RESOLUTION_LOW),
    ],
)
def test_media_resolution_maps_to_enum(resolution: str, expected: types.MediaResolution) -> None:
    config = build_generation_config(AnalysisConfig(media_resolution=resolution))
    assert config.media_resolution == expected


# ---------------------------------------------------------------------------
# Contents ordering + video metadata
# ---------------------------------------------------------------------------
def test_build_contents_places_video_before_prompt() -> None:
    video_part = types.Part.from_uri(file_uri="gs://bucket/demo.mp4", mime_type="video/mp4")
    video_part.video_metadata = types.VideoMetadata(fps=1.5)
    contents = build_contents(video_part, "PROMPT")
    assert len(contents) == 2
    assert contents[0].file_data is not None and contents[0].file_data.file_uri.endswith("demo.mp4")
    assert contents[0].video_metadata.fps == 1.5
    assert contents[1].text == "PROMPT"


def test_video_metadata_fps_serializes_camel_case() -> None:
    part = types.Part(
        file_data=types.FileData(file_uri="https://example/files/1", mime_type="video/mp4"),
        video_metadata=types.VideoMetadata(fps=2.0),
    )
    dumped = part.model_dump(exclude_none=True, by_alias=True)
    assert dumped["videoMetadata"]["fps"] == 2.0


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------
def test_estimate_cost_uses_registry_prices_and_bills_thinking_as_output() -> None:
    cost = estimate_cost_usd("gemini-3.8-flash", 1_000_000, 1_000_000, 1_000_000)
    assert cost == pytest.approx(0.75 + 3.75 * 2)


def test_estimate_cost_zero_tokens() -> None:
    assert estimate_cost_usd("gemini-4-neon", 0, 0, 0) == 0.0


def test_estimate_cost_vertex_falls_back_to_shared_prices() -> None:
    assert estimate_cost_usd("gemini-4-neon", 1000, 1000, 0, backend=BACKEND_VERTEX) == pytest.approx(
        estimate_cost_usd("gemini-4-neon", 1000, 1000, 0)
    )


def test_estimate_cost_rejects_negative_counts() -> None:
    assert estimate_cost_usd("gemini-3.8-flash", -10, -10, -10) == 0.0


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "n/a"),
        (0.0, "$0.000000"),
        (0.0012345, "$0.001234"),
        (1.234567, "$1.2346"),
    ],
)
def test_format_usd(value: float | None, expected: str) -> None:
    assert format_usd(value) == expected


# ---------------------------------------------------------------------------
# Client construction
# ---------------------------------------------------------------------------
def test_ai_studio_client_requires_api_key() -> None:
    with pytest.raises(VideoTestSuiteError):
        create_ai_studio_client(AIStudioCredentials(api_key="   "))


def test_ai_studio_client_uses_api_key() -> None:
    client = create_ai_studio_client(AIStudioCredentials(api_key="test-key"))
    try:
        assert client._api_client.api_key == "test-key"
        assert not client._api_client.vertexai
        assert client._api_client._http_options.base_url == "https://generativelanguage.googleapis.com/"
    finally:
        client.close()


def test_vertex_client_requires_project() -> None:
    with pytest.raises(VideoTestSuiteError):
        create_vertex_client(VertexCredentials(project_id=" "))


class _FakeCredentials:
    """Stand-in for google.auth credentials to avoid ADC lookups in tests."""

    def __init__(self) -> None:
        self.expired = False
        self.valid = True
        self.token = "token"
        self.universe_domain = "googleapis.com"

    def refresh(self, request) -> None:  # noqa: ANN001 - google.auth signature
        self.token = "token"

    def apply(self, headers, token=None) -> None:  # noqa: ANN001 - google.auth signature
        headers["authorization"] = f"Bearer {token or self.token}"


def test_vertex_client_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    import google.auth.credentials as gac

    fake = _FakeCredentials()

    def fake_default(*args, **kwargs):  # noqa: ANN002, ANN003
        return fake, "my-project"

    monkeypatch.setattr(gac, "default", fake_default, raising=False)
    monkeypatch.setattr("google.auth.default", fake_default, raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-project")
    client = create_vertex_client(VertexCredentials(project_id="my-project", location="global"))
    try:
        assert client._api_client.vertexai is True
        assert client._api_client.project == "my-project"
        assert client._api_client.location == "global"
        assert client._api_client._http_options.base_url == "https://aiplatform.googleapis.com/"
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Response parsing / token extraction
# ---------------------------------------------------------------------------
class _Usage:
    prompt_token_count = 100
    candidates_token_count = 20
    thoughts_token_count = 5


class _Response:
    def __init__(self, text: str, parsed=None) -> None:  # noqa: ANN001
        self.text = text
        self.parsed = parsed
        self.usage_metadata = _Usage()


def test_parse_suite_prefers_parsed_model() -> None:
    from schemas import TestSuite

    parsed = TestSuite.model_validate(
        {"feature_summary": "s", "presenter_business_rules": [], "test_cases": [
            {
                "id": "TC-001", "title": "t", "category": "Happy Path", "priority": "High",
                "video_timestamp": "00:01-00:02", "source_basis": "Observed in UI",
                "preconditions": [], "steps": [
                    {"step_number": 1, "action": "a", "test_data": "", "expected_result": "e"}
                ],
            }
        ]}
    )
    assert parse_suite_from_response(_Response("ignored", parsed=parsed)) is parsed


def test_parse_suite_from_json_text() -> None:
    import json

    from conftest import VALID_SUITE_JSON

    suite = parse_suite_from_response(_Response(json.dumps(VALID_SUITE_JSON)))
    assert len(suite.test_cases) == 3


def test_parse_suite_rejects_empty_response() -> None:
    with pytest.raises(VideoTestSuiteError):
        parse_suite_from_response(_Response("   "))


def test_parse_suite_rejects_invalid_json() -> None:
    with pytest.raises(VideoTestSuiteError) as info:
        parse_suite_from_response(_Response('{"feature_summary": 1}'))
    assert "schema" in str(info.value)


def test_extract_usage_reads_token_counts() -> None:
    assert extract_usage(_Response("{}")) == (100, 20, 5)
    assert extract_usage(object()) == (0, 0, 0)
