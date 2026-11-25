"""Model registry, backend configuration and the Gemini caller.

Everything that talks to Google Gemini lives here so ``app.py`` stays pure UI.
The module is written against the **new** ``google-genai`` SDK
(``from google import genai`` / ``from google.genai import types``); the legacy
``google-generativeai`` package and ``genai.configure()`` are never used.
"""

from __future__ import annotations

import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from google import genai
from google.genai import types

from schemas import (
    TestSuite,
    build_user_prompt,
    normalize_suite,
    PromptContext,
    SYSTEM_INSTRUCTION,
)

# ---------------------------------------------------------------------------
# Model registry -- edit this dictionary to add or retire models.
# ---------------------------------------------------------------------------
MODELS_REGISTRY: dict[str, dict[str, Any]] = {
    "gemini-3.8-flash": {
        "label": "Gemini 3.8 Flash",
        "description": "Default - best quality/cost balance for POC demos on the free AI Studio tier.",
        "input_price_per_million": 0.75,
        "output_price_per_million": 3.75,
        "input_price_per_million_vertex": None,  # None -> Vertex pricing assumed equal
        "output_price_per_million_vertex": None,
        "supports_thinking": True,
        "is_default": True,
    },
    "gemini-4-hydrogen": {
        "label": "Gemini 4 Hydrogen",
        "description": "Free AI Studio alternative with stronger reasoning; use when 3.8 Flash misses edge cases.",
        "input_price_per_million": 1.50,
        "output_price_per_million": 7.50,
        "input_price_per_million_vertex": None,
        "output_price_per_million_vertex": None,
        "supports_thinking": True,
        "is_default": False,
    },
    "gemini-4-neon": {
        "label": "Gemini 4 Neon",
        "description": "Paid escalation model for complex walkthroughs; highest quality and cost.",
        "input_price_per_million": 3.00,
        "output_price_per_million": 15.00,
        "input_price_per_million_vertex": None,
        "output_price_per_million_vertex": None,
        "supports_thinking": True,
        "is_default": False,
    },
    "gemini-3.5-flash-lite": {
        "label": "Gemini 3.5 Flash-Lite",
        "description": "High-volume free fallback - cheapest and fastest, best for smoke-level suites.",
        "input_price_per_million": 0.30,
        "output_price_per_million": 2.50,
        "input_price_per_million_vertex": None,
        "output_price_per_million_vertex": None,
        "supports_thinking": True,
        "is_default": False,
    },
}

DEFAULT_MODEL_ID: str = next(
    (model_id for model_id, cfg in MODELS_REGISTRY.items() if cfg.get("is_default")),
    next(iter(MODELS_REGISTRY)),
)

# ---------------------------------------------------------------------------
# Backends, sampling and thinking options
# ---------------------------------------------------------------------------
BACKEND_AI_STUDIO = "Free Pilot POC (Google AI Studio)"
BACKEND_VERTEX = "Enterprise Vertex AI"
BACKEND_OPTIONS: tuple[str, ...] = (BACKEND_AI_STUDIO, BACKEND_VERTEX)

MEDIA_RESOLUTION_OPTIONS: tuple[str, ...] = ("HIGH", "MEDIUM", "LOW")
DEFAULT_MEDIA_RESOLUTION = "HIGH"

THINKING_LEVEL_OPTIONS: tuple[str, ...] = ("low", "medium", "high")  # never "minimal"
DEFAULT_THINKING_LEVEL = "medium"

FPS_MIN, FPS_MAX, FPS_DEFAULT = 0.5, 3.0, 1.0

SUPPORTED_VIDEO_MIME_TYPES: dict[str, str] = {
    "mp4": "video/mp4",
    "mov": "video/quicktime",
    "webm": "video/webm",
    "mkv": "video/x-matroska",
}
MAX_DIRECT_UPLOAD_BYTES = 256 * 1024 * 1024  # 256 MB inline limit for Vertex AI

POLL_INTERVAL_SECONDS = 2.0
MAX_POLL_ATTEMPTS = 150  # ~5 minutes
MAX_POLL_INTERVAL_SECONDS = 10.0
LRO_TIMEOUT_SECONDS = 300.0


class VideoTestSuiteError(Exception):
    """Raised for expected, user-facing failures (bad input, API or schema issues)."""


# ---------------------------------------------------------------------------
# Mime detection
# ---------------------------------------------------------------------------
def mime_type_for(filename: str) -> str:
    """Return the video mime type for a supported extension."""

    extension = Path(filename).suffix.lower().lstrip(".")
    try:
        return SUPPORTED_VIDEO_MIME_TYPES[extension]
    except KeyError as exc:
        supported = ", ".join(f".{ext}" for ext in SUPPORTED_VIDEO_MIME_TYPES)
        raise VideoTestSuiteError(
            f"Unsupported video type '.{extension}'. Supported types: {supported}."
        ) from exc


def is_supported_video(filename: str) -> bool:
    return Path(filename).suffix.lower().lstrip(".") in SUPPORTED_VIDEO_MIME_TYPES


# ---------------------------------------------------------------------------
# Configuration objects
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class AnalysisConfig:
    """Everything the caller needs, decoupled from Streamlit."""

    model_id: str = DEFAULT_MODEL_ID
    fps: float = FPS_DEFAULT
    media_resolution: str = DEFAULT_MEDIA_RESOLUTION
    thinking_level: str = DEFAULT_THINKING_LEVEL
    backend: str = BACKEND_AI_STUDIO
    prompt_context: PromptContext = field(default_factory=PromptContext)

    def validate(self) -> None:
        if self.model_id not in MODELS_REGISTRY:
            raise VideoTestSuiteError(f"Unknown model '{self.model_id}'.")
        if not FPS_MIN <= float(self.fps) <= FPS_MAX:
            raise VideoTestSuiteError(
                f"Sampling rate must be between {FPS_MIN} and {FPS_MAX} fps."
            )
        if self.media_resolution not in MEDIA_RESOLUTION_OPTIONS:
            raise VideoTestSuiteError(
                f"Media resolution must be one of {', '.join(MEDIA_RESOLUTION_OPTIONS)}."
            )
        if self.thinking_level not in THINKING_LEVEL_OPTIONS:
            raise VideoTestSuiteError(
                'Thinking level must be one of "low", "medium", "high" '
                '("minimal" is not supported).'
            )
        if self.backend not in BACKEND_OPTIONS:
            raise VideoTestSuiteError(f"Unknown backend '{self.backend}'.")


@dataclass(slots=True)
class AIStudioCredentials:
    api_key: str


@dataclass(slots=True)
class VertexCredentials:
    project_id: str
    location: str = "global"


@dataclass(slots=True)
class AnalysisResult:
    suite: TestSuite
    model_id: str
    backend: str
    latency_seconds: float
    input_tokens: int
    output_tokens: int
    thinking_tokens: int
    estimated_cost_usd: float | None
    fps: float
    media_resolution: str
    thinking_level: str
    upload_mode: str  # "Gemini Files API" or "Inline bytes" / "Cloud Storage URI"
    prompt_characters: int

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.thinking_tokens


@dataclass(slots=True)
class ExecutionProgress:
    """Optional progress hooks so the UI can mirror each pipeline stage."""

    on_stage: Callable[[str, str], None] | None = None
    on_detail: Callable[[str], None] | None = None

    def stage(self, key: str, label: str) -> None:
        if self.on_stage is not None:
            self.on_stage(key, label)

    def detail(self, message: str) -> None:
        if self.on_detail is not None:
            self.on_detail(message)


# ---------------------------------------------------------------------------
# Pricing / formatting helpers
# ---------------------------------------------------------------------------
def _price_for(model_id: str, backend: str) -> tuple[float, float]:
    config = MODELS_REGISTRY[model_id]
    if backend == BACKEND_VERTEX:
        input_price = config.get("input_price_per_million_vertex")
        output_price = config.get("output_price_per_million_vertex")
        if input_price is not None and output_price is not None:
            return float(input_price), float(output_price)
    return float(config["input_price_per_million"]), float(config["output_price_per_million"])


def estimate_cost_usd(
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    thinking_tokens: int = 0,
    backend: str = BACKEND_AI_STUDIO,
) -> float:
    """Estimated cost: input tokens + (output + thinking) tokens at registry prices.

    Thinking tokens are billed as output tokens, so they share the output price.
    """

    input_price, output_price = _price_for(model_id, backend)
    return (
        max(input_tokens, 0) / 1_000_000 * input_price
        + max(output_tokens, 0) / 1_000_000 * output_price
        + max(thinking_tokens, 0) / 1_000_000 * output_price
    )


def format_usd(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value < 0.01:
        return f"${value:.6f}"
    return f"${value:.4f}"


def media_resolution_enum(value: str) -> types.MediaResolution:
    mapping = {
        "HIGH": types.MediaResolution.MEDIA_RESOLUTION_HIGH,
        "MEDIUM": types.MediaResolution.MEDIA_RESOLUTION_MEDIUM,
        "LOW": types.MediaResolution.MEDIA_RESOLUTION_LOW,
    }
    try:
        return mapping[value]
    except KeyError as exc:  # pragma: no cover - guarded by AnalysisConfig.validate
        raise VideoTestSuiteError(f"Unsupported media resolution '{value}'.") from exc


def build_generation_config(config: AnalysisConfig) -> types.GenerateContentConfig:
    """Build the ``GenerateContentConfig`` required for Gemini 3.x video analysis.

    Deliberately omits ``temperature``, ``top_p`` and ``top_k``: Gemini 3.8 Flash
    ignores legacy sampling parameters, so relying on them would be misleading.
    """

    return types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        media_resolution=media_resolution_enum(config.media_resolution),
        thinking_config=types.ThinkingConfig(thinking_level=config.thinking_level),
        response_mime_type="application/json",
        response_schema=TestSuite,
    )


def build_contents(video_part: types.Part, prompt: str) -> list[types.Part]:
    """Video part first, text prompt second - required by Gemini 3.x video rules."""

    return [video_part, types.Part.from_text(text=prompt)]


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------
def create_ai_studio_client(credentials: AIStudioCredentials) -> genai.Client:
    api_key = (credentials.api_key or "").strip()
    if not api_key:
        raise VideoTestSuiteError(
            "A GEMINI_API_KEY is required for the Free Pilot POC backend. "
            "Paste it in the sidebar or set the GEMINI_API_KEY environment variable."
        )
    return genai.Client(api_key=api_key)


def create_vertex_client(credentials: VertexCredentials) -> genai.Client:
    if not (credentials.project_id or "").strip():
        raise VideoTestSuiteError(
            "A GCP project ID is required for the Enterprise Vertex AI backend."
        )
    return genai.Client(
        vertexai=True,
        project=credentials.project_id.strip(),
        location=(credentials.location or "global").strip(),
    )


# ---------------------------------------------------------------------------
# Gemini Files API helpers (AI Studio backend)
# ---------------------------------------------------------------------------
def _file_state_name(file_obj: Any) -> str:
    """Return 'PROCESSING' / 'ACTIVE' / 'FAILED' for a files API object."""

    state = getattr(file_obj, "state", None)
    return getattr(state, "name", None) or str(state or "UNKNOWN")


def wait_for_file_active(
    client: genai.Client,
    file_name: str,
    progress: ExecutionProgress | None = None,
    poll_interval: float = POLL_INTERVAL_SECONDS,
    max_attempts: int = MAX_POLL_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
    started_file: Any = None,
) -> Any:
    """Poll ``client.files.get`` until the uploaded video is ACTIVE.

    Fails fast when the service reports ``FAILED`` and never polls longer than
    ``max_attempts`` intervals (~5 minutes by default).
    """

    file_obj = started_file
    for attempt in range(max_attempts):
        if file_obj is None:
            file_obj = client.files.get(name=file_name)
        state = _file_state_name(file_obj)
        if state == "ACTIVE":
            return file_obj
        if state == "FAILED":
            error = getattr(file_obj, "error", None)
            message = getattr(error, "message", None) or "unknown processing error"
            raise VideoTestSuiteError(
                f"Gemini rejected the uploaded video during processing: {message}"
            )
        if progress is not None and attempt % 5 == 0:
            progress.detail(f"Video processing on Gemini servers ({state.lower()})...")
        sleep(poll_interval)
        file_obj = None
    raise VideoTestSuiteError(
        "Timed out waiting for Gemini to finish processing the video. "
        "Try a shorter clip or a lower FPS sampling rate."
    )


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------
def parse_suite_from_response(response: Any) -> TestSuite:
    """Validate the JSON response against the Pydantic schema."""

    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, TestSuite):
        return parsed
    if isinstance(parsed, dict):
        try:
            return TestSuite.model_validate(parsed)
        except Exception as exc:  # noqa: BLE001 - re-raised with context
            raise VideoTestSuiteError(f"The model returned invalid JSON: {exc}") from exc
    text = (getattr(response, "text", None) or "").strip()
    if not text:
        raise VideoTestSuiteError(
            "Gemini returned an empty response. Try again or lower the thinking level."
        )
    try:
        return TestSuite.model_validate_json(text)
    except Exception as exc:  # noqa: BLE001 - re-raised with context
        preview = text[:280].replace("\n", " ")
        raise VideoTestSuiteError(
            "Gemini returned JSON that does not match the TestSuite schema "
            f"({exc}). Response preview: {preview}"
        ) from exc


def extract_usage(response: Any) -> tuple[int, int, int]:
    """Return ``(input_tokens, output_tokens, thinking_tokens)``."""

    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return 0, 0, 0

    def _count(*names: str) -> int:
        for name in names:
            value = getattr(usage, name, None)
            if value is not None:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    continue
        return 0

    return (
        _count("prompt_token_count", "promptTokenCount"),
        _count("candidates_token_count", "candidatesTokenCount"),
        _count("thoughts_token_count", "thoughtsTokenCount"),
    )


def remove_local_temp_copy(video_path: Path | str | None) -> None:
    """Delete a local video copy **only when it lives in the system temp directory**.

    ``app.py`` always stages uploads in the temp directory, so its copies are
    removed automatically. When the module is used as a library, a caller-supplied
    path outside the temp directory is left untouched on purpose.
    """

    if video_path is None:
        return
    try:
        resolved = Path(video_path).resolve()
        temp_root = Path(tempfile.gettempdir()).resolve()
        if resolved.is_relative_to(temp_root) and resolved.is_file():
            resolved.unlink()
    except (OSError, RuntimeError):
        pass


# ---------------------------------------------------------------------------
# Backend runners
# ---------------------------------------------------------------------------
def _run_ai_studio(
    config: AnalysisConfig,
    credentials: AIStudioCredentials,
    video_path: Path | str,
    mime_type: str,
    progress: ExecutionProgress,
) -> tuple[TestSuite, str, float, int, int, int]:
    client = create_ai_studio_client(credentials)
    tmp_path = Path(video_path)
    uploaded_file = None
    prompt = build_user_prompt(config.prompt_context)
    generation_config = build_generation_config(config)
    started = time.perf_counter()
    try:
        progress.stage("upload", "Uploading video to Gemini Files API")
        uploaded_file = client.files.upload(
            file=tmp_path,
            config=types.UploadFileConfig(
                mime_type=mime_type, display_name=tmp_path.name
            ),
        )
        file_name = uploaded_file.name
        if not file_name:
            raise VideoTestSuiteError(
                "Gemini Files API did not return a file name for the upload."
            )
        progress.stage("processing", "Waiting for Gemini to process the video")
        active_file = wait_for_file_active(
            client,
            file_name,
            progress=progress,
            started_file=uploaded_file,
            poll_interval=POLL_INTERVAL_SECONDS,
            max_attempts=MAX_POLL_ATTEMPTS,
        )
        progress.stage("analyzing", "Analyzing video frames + presenter audio")
        video_part = types.Part(
            file_data=types.FileData(
                file_uri=active_file.uri, mime_type=mime_type
            ),
            video_metadata=types.VideoMetadata(fps=float(config.fps)),
        )
        response = client.models.generate_content(
            model=config.model_id,
            contents=build_contents(video_part, prompt),
            config=generation_config,
        )
        latency = time.perf_counter() - started
        progress.stage("validating", "Validating response against TestSuite schema")
        suite = parse_suite_from_response(response)
        input_tokens, output_tokens, thinking_tokens = extract_usage(response)
        return (
            suite,
            "Gemini Files API",
            latency,
            input_tokens,
            output_tokens,
            thinking_tokens,
        )
    finally:
        # Always release the remote file and the local temporary copy.
        if uploaded_file is not None and getattr(uploaded_file, "name", None):
            try:
                client.files.delete(name=uploaded_file.name)
            except Exception:  # noqa: BLE001 - cleanup must never mask the result
                pass
        remove_local_temp_copy(tmp_path)
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001
                pass


def _run_vertex(
    config: AnalysisConfig,
    credentials: VertexCredentials,
    video_path: Path | str | None,
    mime_type: str,
    progress: ExecutionProgress,
    gcs_uri: str | None = None,
    video_bytes: bytes | None = None,
) -> tuple[TestSuite, str, float, int, int, int]:
    if gcs_uri:
        uri = gcs_uri.strip()
        if not uri.startswith("gs://"):
            raise VideoTestSuiteError(
                "Cloud Storage URIs must start with gs:// (for example gs://bucket/demo.mp4)."
            )
        progress.stage("upload", "Using Cloud Storage URI (no upload needed)")
        video_part = types.Part.from_uri(file_uri=uri, mime_type=mime_type)
        upload_mode = "Cloud Storage URI"
    else:
        if video_bytes is None:
            if video_path is None:
                raise VideoTestSuiteError(
                    "Provide a gs:// URI or upload a video for Vertex AI analysis."
                )
            video_bytes = Path(video_path).read_bytes()
        if len(video_bytes) >= MAX_DIRECT_UPLOAD_BYTES:
            raise VideoTestSuiteError(
                "Direct inline upload is limited to 256 MB. Upload the video to "
                "Cloud Storage and pass a gs:// URI instead."
            )
        progress.stage("upload", "Encoding video bytes for direct Vertex AI upload")
        video_part = types.Part.from_bytes(data=video_bytes, mime_type=mime_type)
        upload_mode = "Inline bytes"

    video_part.video_metadata = types.VideoMetadata(fps=float(config.fps))
    client = create_vertex_client(credentials)
    prompt = build_user_prompt(config.prompt_context)
    started = time.perf_counter()
    try:
        progress.stage("processing", "Submitting request to Vertex AI")
        progress.stage("analyzing", "Analyzing video frames + presenter audio")
        response = client.models.generate_content(
            model=config.model_id,
            contents=build_contents(video_part, prompt),
            config=build_generation_config(config),
        )
        latency = time.perf_counter() - started
        progress.stage("validating", "Validating response against TestSuite schema")
        suite = parse_suite_from_response(response)
        input_tokens, output_tokens, thinking_tokens = extract_usage(response)
        return (
            suite,
            upload_mode,
            latency,
            input_tokens,
            output_tokens,
            thinking_tokens,
        )
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def generate_test_suite(
    config: AnalysisConfig,
    credentials: AIStudioCredentials | VertexCredentials,
    *,
    video_path: Path | str | None = None,
    video_bytes: bytes | None = None,
    video_filename: str = "video.mp4",
    gcs_uri: str | None = None,
    progress: ExecutionProgress | None = None,
) -> AnalysisResult:
    """Run the full video -> TestSuite pipeline for the selected backend."""

    config.validate()
    progress = progress or ExecutionProgress()
    mime_type = mime_type_for(video_filename)

    if config.backend == BACKEND_AI_STUDIO:
        if not isinstance(credentials, AIStudioCredentials):
            raise VideoTestSuiteError(
                "The Free Pilot POC backend needs an AI Studio API key."
            )
        if video_path is None:
            raise VideoTestSuiteError("Upload a video file before generating test cases.")
        (
            suite,
            upload_mode,
            latency,
            input_tokens,
            output_tokens,
            thinking_tokens,
        ) = _run_ai_studio(config, credentials, video_path, mime_type, progress)
    else:
        if not isinstance(credentials, VertexCredentials):
            raise VideoTestSuiteError(
                "The Enterprise backend needs a GCP project ID and location."
            )
        if not gcs_uri and video_bytes is None and video_path is None:
            raise VideoTestSuiteError(
                "Provide a gs:// Cloud Storage URI or upload a video for Vertex AI."
            )
        (
            suite,
            upload_mode,
            latency,
            input_tokens,
            output_tokens,
            thinking_tokens,
        ) = _run_vertex(
            config,
            credentials,
            video_path,
            mime_type,
            progress,
            gcs_uri=gcs_uri,
            video_bytes=video_bytes,
        )

    try:
        normalized = normalize_suite(suite)
    except ValueError as exc:
        raise VideoTestSuiteError(str(exc)) from exc
    return AnalysisResult(
        suite=normalized,
        model_id=config.model_id,
        backend=config.backend,
        latency_seconds=latency,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        thinking_tokens=thinking_tokens,
        estimated_cost_usd=estimate_cost_usd(
            config.model_id,
            input_tokens,
            output_tokens,
            thinking_tokens,
            backend=config.backend,
        ),
        fps=float(config.fps),
        media_resolution=config.media_resolution,
        thinking_level=config.thinking_level,
        upload_mode=upload_mode,
        prompt_characters=len(build_user_prompt(config.prompt_context)),
    )


def iter_registry_choices() -> Iterable[tuple[str, dict[str, Any]]]:
    """Yield ``(model_id, registry entry)`` - handy for the sidebar dropdown."""

    for model_id, entry in MODELS_REGISTRY.items():
        yield model_id, entry


def model_label(model_id: str, backend: str = BACKEND_AI_STUDIO) -> str:
    """Human readable one-liner used in dropdowns and the workspace summary."""

    entry = MODELS_REGISTRY[model_id]
    input_price, output_price = _price_for(model_id, backend)
    return (
        f"{entry['label']} ({model_id}) - "
        f"${input_price:.2f} in / ${output_price:.2f} out per 1M tokens"
    )
