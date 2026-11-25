"""Video-to-Test-Case: Streamlit POC that turns demo videos into QA test suites.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import os
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
import streamlit as st

import models_registry
from exporters import (
    COL_CATEGORY,
    COL_ID,
    COL_PRECONDITIONS,
    COL_PRIORITY,
    COL_SOURCE,
    COL_STEPS,
    COL_TIMESTAMP,
    COL_TITLE,
    TABLE_COLUMNS,
    dataframe_to_cases,
    export_bundle,
    slugify,
    suite_to_dataframe,
)
from models_registry import (
    AnalysisConfig,
    AnalysisResult,
    AIStudioCredentials,
    BACKEND_AI_STUDIO,
    BACKEND_OPTIONS,
    DEFAULT_MEDIA_RESOLUTION,
    DEFAULT_MODEL_ID,
    DEFAULT_THINKING_LEVEL,
    FPS_DEFAULT,
    FPS_MAX,
    FPS_MIN,
    ExecutionProgress,
    MEDIA_RESOLUTION_OPTIONS,
    MODELS_REGISTRY,
    SUPPORTED_VIDEO_MIME_TYPES,
    THINKING_LEVEL_OPTIONS,
    VideoTestSuiteError,
    VertexCredentials,
    format_usd,
    mime_type_for,
    model_label,
)
from schemas import (
    CATEGORIES,
    DEFAULT_OUTPUT_FOCUS,
    OUTPUT_FOCUS_OPTIONS,
    PRIORITIES,
    SOURCE_BASES,
    PromptContext,
    TestCase,
    TestSuite,
)

APP_TITLE = "Video-to-Test-Case"
APP_ICON = "\U0001F3AC"
UPLOAD_TYPES = sorted(SUPPORTED_VIDEO_MIME_TYPES)
MAX_VIDEO_BYTES = 2 * 1024 * 1024 * 1024  # informational guard for the uploader

STAGES: tuple[tuple[str, str], ...] = (
    ("upload", "Uploading video"),
    ("processing", "Processing video"),
    ("analyzing", "Analyzing video + audio"),
    ("validating", "Validating schema"),
)

SS_RESULT = "analysis_result"
SS_EDITOR_VERSION = "editor_version"
SS_ERROR = "last_error"
SS_UPLOAD_SIGNATURE = "upload_signature"
SS_EXPORTS = "export_payloads"

# ---------------------------------------------------------------------------
# Session state helpers
# ---------------------------------------------------------------------------
def _init_state() -> None:
    defaults = {
        SS_RESULT: None,
        SS_EDITOR_VERSION: 0,
        SS_ERROR: None,
        SS_UPLOAD_SIGNATURE: "",
        SS_EXPORTS: {},
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


@dataclass(slots=True)
class PipelineState:
    status_handle: Any
    slots: dict[str, Any]
    current: str = ""
    done: set[str] = field(default_factory=set)
    detail_slot: Any = None
    last_detail: str = ""

    def advance(self, key: str, label: str) -> None:
        if key in self.slots and self.current == key:
            return
        self.current = key
        for stage_key, stage_label in STAGES:
            slot = self.slots.get(stage_key)
            if slot is None:
                continue
            if stage_key in self.done:
                slot.markdown(f":green[\u2714] **{stage_label}**")
            elif stage_key == key:
                slot.markdown(f"\u23f3 **{label or stage_label}**")
            else:
                slot.markdown(f":gray[\u25cb {stage_label}]")
        self.write_detail(label)

    def complete_stage(self, key: str) -> None:
        self.done.add(key)
        for stage_key, stage_label in STAGES:
            if stage_key == key and stage_key in self.slots:
                self.slots[stage_key].markdown(f":green[\u2714] **{stage_label}**")

    def write_detail(self, message: str, force: bool = False) -> None:
        if not message or self.detail_slot is None:
            return
        if message == self.last_detail and not force:
            return
        self.last_detail = message
        self.detail_slot.caption(f"... {message}")


def _human_size(size_bytes: int | float) -> str:
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} GB"


def _build_prompt_context(
    feature_name: str,
    target_roles: str,
    output_focus: str,
    extra_notes: str,
) -> PromptContext:
    return PromptContext(
        feature_name=feature_name.strip(),
        target_roles=target_roles.strip(),
        output_focus=output_focus if output_focus in OUTPUT_FOCUS_OPTIONS else DEFAULT_OUTPUT_FOCUS,
        extra_notes=extra_notes.strip(),
    )


def _save_tmp_video(uploaded_file: Any) -> Path:
    """Persist the Streamlit upload to a temp file the SDK can reopen."""

    suffix = Path(uploaded_file.name).suffix or ".mp4"
    tmp_file = tempfile.NamedTemporaryFile(
        prefix="video-to-test-case-", suffix=suffix, delete=False
    )
    try:
        tmp_file.write(uploaded_file.getbuffer())
    finally:
        tmp_file.close()
    return Path(tmp_file.name)


def _clear_result() -> None:
    st.session_state[SS_RESULT] = None
    st.session_state[SS_ERROR] = None
    st.session_state[SS_EDITOR_VERSION] = int(st.session_state[SS_EDITOR_VERSION]) + 1


def _run_generation(
    uploaded_file: Any,
    config: AnalysisConfig,
    credentials: AIStudioCredentials | VertexCredentials,
    gcs_uri: str,
) -> None:
    """Execute the pipeline inside ``st.status`` and persist the result."""

    started = time.perf_counter()
    upload_size = getattr(uploaded_file, "size", 0) or 0
    tmp_path: Path | None = None

    with st.status("Generating QA test cases...", expanded=True) as status:
        slots = {key: st.empty() for key, _ in STAGES}
        pipeline = PipelineState(status_handle=status, slots=slots)
        pipeline.detail_slot = st.empty()
        pipeline.advance("upload", f"Uploading {uploaded_file.name} ({_human_size(upload_size)})")

        def on_stage(key: str, label: str) -> None:
            pipeline.advance(key, label)

        def on_detail(message: str) -> None:
            pipeline.write_detail(message)

        try:
            if config.backend == BACKEND_AI_STUDIO:
                tmp_path = _save_tmp_video(uploaded_file)
                result = models_registry.generate_test_suite(
                    config,
                    credentials,
                    video_path=tmp_path,
                    video_filename=uploaded_file.name,
                    progress=ExecutionProgress(on_stage=on_stage, on_detail=on_detail),
                )
            else:
                gcs = gcs_uri.strip()
                video_bytes = None if gcs else uploaded_file.getvalue()
                result = models_registry.generate_test_suite(
                    config,
                    credentials,
                    video_bytes=video_bytes,
                    video_filename=uploaded_file.name,
                    gcs_uri=gcs or None,
                    progress=ExecutionProgress(on_stage=on_stage, on_detail=on_detail),
                )
        except VideoTestSuiteError as exc:
            _finish_with_error(status, pipeline, str(exc))
            _show_persistent_error()
            return
        except Exception as exc:  # noqa: BLE001 - surface unexpected SDK/network errors
            _finish_with_error(
                status,
                pipeline,
                f"Unexpected error while talking to Gemini: {exc}",
            )
            _show_persistent_error()
            return
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass

        pipeline.write_detail("", force=False)
        for stage_key, _ in STAGES:
            pipeline.complete_stage(stage_key)
        total_runtime = time.perf_counter() - started
        status.update(
            label=(
                f"Generated {len(result.suite.test_cases)} test cases "
                f"in {total_runtime:.1f}s"
            ),
            state="complete",
            expanded=False,
        )

    st.session_state[SS_RESULT] = result
    st.session_state[SS_ERROR] = None
    st.session_state[SS_EDITOR_VERSION] = int(st.session_state[SS_EDITOR_VERSION]) + 1
    if result.upload_mode == "Gemini Files API":
        st.toast("Remote file and local temp copy cleaned up.", icon="\U0001F9F9")


def _finish_with_error(status: Any, pipeline: PipelineState, message: str) -> None:
    pipeline.write_detail(message, force=True)
    status.update(label="Generation failed", state="error", expanded=True)
    st.session_state[SS_ERROR] = message


def _show_persistent_error() -> None:
    """Render the stored error *after* the status block so it survives reruns."""

    message = st.session_state.get(SS_ERROR)
    if message:
        st.error(f"Generation failed: {message}")


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
def _render_sidebar() -> dict[str, Any]:
    sidebar = st.sidebar
    sidebar.title(f"{APP_ICON} {APP_TITLE}")
    sidebar.caption("Dual backend: free AI Studio POC now, Vertex AI in production.")

    backend = sidebar.radio(
        "Execution Backend",
        BACKEND_OPTIONS,
        index=0,
        help="AI Studio is ideal for the free POC. Vertex AI is the enterprise path.",
    )

    api_key = ""
    api_key_input = None
    project_id = ""
    location = "global"
    gcs_uri = ""
    if backend == BACKEND_AI_STUDIO:
        env_key = os.environ.get("GEMINI_API_KEY", "")
        api_key_input = sidebar.text_input(
            "GEMINI_API_KEY",
            value="",
            type="password",
            placeholder="Uses GEMINI_API_KEY env var when left empty",
            help="Create a free key at https://aistudio.google.com/apikey",
        )
        api_key = api_key_input.strip() or env_key
        if api_key_input.strip():
            sidebar.success("API key supplied for this session.")
        elif env_key:
            sidebar.caption("Using `GEMINI_API_KEY` from the environment.")
        else:
            sidebar.warning("No API key yet - paste one above or export GEMINI_API_KEY.")
        sidebar.caption(
            "Uploads go to the Gemini Files API, are processed remotely, then deleted "
            "together with the local temp copy."
        )
    else:
        project_id = sidebar.text_input(
            "GCP Project ID",
            value=os.environ.get("GOOGLE_CLOUD_PROJECT", ""),
            placeholder="my-gcp-project",
        ).strip()
        location = sidebar.text_input(
            "Vertex Location",
            value=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"),
        ).strip() or "global"
        gcs_uri = sidebar.text_input(
            "Cloud Storage URI (optional)",
            value="",
            placeholder="gs://my-bucket/walkthrough.mp4",
            help="If set, the uploaded file is ignored and this URI is analyzed.",
        ).strip()
        if gcs_uri and not gcs_uri.startswith("gs://"):
            sidebar.warning("Cloud Storage URIs must start with `gs://`.")
        sidebar.caption(
            "Uses Application Default Credentials (`gcloud auth application-default login`). "
            "Without a gs:// URI the video is sent as inline bytes (< 256 MB)."
        )

    sidebar.divider()
    model_options = list(MODELS_REGISTRY)
    model_id = sidebar.selectbox(
        "Model",
        model_options,
        index=model_options.index(DEFAULT_MODEL_ID),
        format_func=lambda model: model_label(model, backend),
        help="Driven by MODELS_REGISTRY in models_registry.py - add new models there.",
    )
    sidebar.caption(MODELS_REGISTRY[model_id]["description"])

    fps = sidebar.slider(
        "Sampling Rate (fps)",
        min_value=FPS_MIN,
        max_value=FPS_MAX,
        value=FPS_DEFAULT,
        step=0.5,
        help="1 frame per second is the free-tier safe default. Higher values cost more tokens.",
    )
    media_resolution = sidebar.selectbox(
        "Media Resolution",
        MEDIA_RESOLUTION_OPTIONS,
        index=MEDIA_RESOLUTION_OPTIONS.index(DEFAULT_MEDIA_RESOLUTION),
        help="HIGH keeps small UI text (toasts, validation) readable.",
    )
    thinking_level = sidebar.selectbox(
        "Thinking Level",
        THINKING_LEVEL_OPTIONS,
        index=THINKING_LEVEL_OPTIONS.index(DEFAULT_THINKING_LEVEL),
        help='"minimal" is intentionally not exposed: Gemini 3.x QA extraction needs reasoning.',
    )

    sidebar.divider()
    sidebar.subheader("Context (optional)")
    feature_name = sidebar.text_input(
        "Module / Feature Name", placeholder="e.g. Checkout - Promo Codes"
    )
    target_roles = sidebar.text_input(
        "Target User Roles", placeholder="e.g. Admin, Billing Analyst"
    )
    output_focus = sidebar.selectbox(
        "Output Focus",
        OUTPUT_FOCUS_OPTIONS,
        index=OUTPUT_FOCUS_OPTIONS.index(DEFAULT_OUTPUT_FOCUS),
    )
    extra_notes = sidebar.text_area(
        "Extra PRD Notes",
        placeholder="Limits, compliance rules or acceptance criteria to honour.",
        height=90,
    )

    sidebar.divider()
    with sidebar.expander("Configuration summary", expanded=False):
        st.markdown(
            "\n".join(
                [
                    f"- **Backend:** {backend}",
                    f"- **Model:** `{model_id}`",
                    f"- **fps:** {fps}",
                    f"- **Media resolution:** {media_resolution}",
                    f"- **Thinking level:** {thinking_level}",
                    f"- **Output focus:** {output_focus}",
                ]
            )
        )

    return {
        "backend": backend,
        "api_key": api_key,
        "api_key_from_env": api_key_input is None and bool(os.environ.get("GEMINI_API_KEY", "")),
        "project_id": project_id,
        "location": location,
        "gcs_uri": gcs_uri,
        "model_id": model_id,
        "fps": fps,
        "media_resolution": media_resolution,
        "thinking_level": thinking_level,
        "prompt_context": _build_prompt_context(
            feature_name, target_roles, output_focus, extra_notes
        ),
        "feature_name": feature_name,
        "target_roles": target_roles,
        "output_focus": output_focus,
        "extra_notes": extra_notes,
    }


# ---------------------------------------------------------------------------
# Workspace: upload + preview
# ---------------------------------------------------------------------------
def _render_upload_workspace(settings: Mapping[str, Any]) -> Any:
    st.subheader("1. Upload a walkthrough video")
    upload_column, info_column = st.columns([3, 2], gap="large")
    with upload_column:
        uploaded_file = st.file_uploader(
            "Demo / walkthrough video",
            type=UPLOAD_TYPES,
            accept_multiple_files=False,
            key="video_uploader",
            help="Supported: .mp4, .mov, .webm, .mkv",
        )
        generate_clicked = st.button(
            "Generate QA Test Cases",
            type="primary",
            width="stretch",
            disabled=uploaded_file is None,
        )
    with info_column:
        if uploaded_file is not None:
            st.caption("Preview")
            st.video(uploaded_file, format=mime_type_for(uploaded_file.name))
        else:
            st.info("Upload a video to preview it here.")

    if uploaded_file is None:
        st.caption(
            "Tip: a 2-5 minute walkthrough with spoken narration produces the richest "
            "Negative/Boundary coverage."
        )
        return None

    if getattr(uploaded_file, "size", 0) and uploaded_file.size > MAX_VIDEO_BYTES:
        st.error("The video exceeds 2 GB. Please upload a shorter clip.")
        return None

    signature = f"{uploaded_file.name}:{uploaded_file.size}"
    if signature != st.session_state.get(SS_UPLOAD_SIGNATURE):
        st.session_state[SS_UPLOAD_SIGNATURE] = signature
        st.session_state[SS_RESULT] = None
        st.session_state[SS_ERROR] = None

    summary = pd.DataFrame(
        {
            "Setting": [
                "File name",
                "Size",
                "MIME type",
                "Model",
                "Backend",
                "fps",
                "Media resolution",
                "Thinking level",
                "Output focus",
            ],
            "Value": [
                uploaded_file.name,
                _human_size(uploaded_file.size),
                mime_type_for(uploaded_file.name),
                settings["model_id"],
                settings["backend"],
                f"{settings['fps']} fps",
                settings["media_resolution"],
                settings["thinking_level"],
                settings["output_focus"],
            ],
        }
    )
    st.dataframe(summary, hide_index=True, width="stretch")

    if getattr(uploaded_file, "size", 0) > 20 * 1024 * 1024:
        st.caption(
            "Large file detected - the Gemini Files API upload can take a moment; "
            "the progress panel reports each stage."
        )
    return uploaded_file if generate_clicked else None


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
def _metrics_row(result: AnalysisResult) -> None:
    columns = st.columns(5)
    columns[0].metric("Total Test Cases", len(result.suite.test_cases))
    columns[1].metric(
        "Input Tokens",
        f"{result.input_tokens:,}",
        help="Prompt + video tokens reported by usage_metadata.",
    )
    columns[2].metric(
        "Output + Thinking Tokens",
        f"{result.output_tokens + result.thinking_tokens:,}",
        help=f"{result.output_tokens:,} output + {result.thinking_tokens:,} thinking tokens.",
    )
    columns[3].metric(
        "Est. Cost ($ USD)",
        format_usd(result.estimated_cost_usd),
        help="MODELS_REGISTRY price x usage_metadata tokens (thinking billed as output).",
    )
    columns[4].metric(
        "Latency (s)",
        f"{result.latency_seconds:.1f}",
        help="Upload + processing + generation wall clock time.",
    )
    st.caption(
        f"Model `{result.model_id}` - {result.backend} - {result.upload_mode} - "
        f"{result.fps} fps - {result.media_resolution} - thinking `{result.thinking_level}`"
    )


def _editor_config() -> dict[str, Any]:
    return {
        COL_ID: st.column_config.TextColumn(COL_ID, width="small", required=True),
        COL_TITLE: st.column_config.TextColumn(COL_TITLE, width="large", required=True),
        COL_CATEGORY: st.column_config.SelectboxColumn(COL_CATEGORY, options=list(CATEGORIES), required=True),
        COL_PRIORITY: st.column_config.SelectboxColumn(COL_PRIORITY, options=list(PRIORITIES), required=True),
        COL_TIMESTAMP: st.column_config.TextColumn(COL_TIMESTAMP, width="small", help="MM:SS-MM:SS"),
        COL_SOURCE: st.column_config.SelectboxColumn(COL_SOURCE, options=list(SOURCE_BASES), required=True),
        COL_PRECONDITIONS: st.column_config.TextColumn(COL_PRECONDITIONS, width="medium", help="One per line"),
        COL_STEPS: st.column_config.TextColumn(
            COL_STEPS,
            width="large",
            help="One step per line: `1. action | test data | expected result`",
        ),
    }


def _detailed_cases(edited_df: pd.DataFrame, fallback: TestSuite) -> list[TestCase]:
    """Prefer the edited table for the detail view, falling back to the generated suite."""

    try:
        cases, _ = dataframe_to_cases(edited_df)
        return cases or fallback.test_cases
    except Exception:  # noqa: BLE001 - the detail view must never break the page
        return fallback.test_cases


def _render_results(result: AnalysisResult, settings: Mapping[str, Any]) -> None:
    suite = result.suite
    editor_version = int(st.session_state[SS_EDITOR_VERSION])
    with st.container(border=True):
        _metrics_row(result)

    st.divider()
    st.subheader("2. Generated QA suite")
    summary_default = st.text_area(
        "Feature summary",
        value=suite.feature_summary,
        height=110,
        key=f"feature_summary_{editor_version}",
        help="Editable - included in CSV labels, JSON and the Gherkin feature description.",
    )
    with st.expander(
        f"Business rules extracted from presenter audio ({len(suite.presenter_business_rules)})",
        expanded=bool(suite.presenter_business_rules),
    ):
        if suite.presenter_business_rules:
            st.markdown("\n".join(f"- {rule}" for rule in suite.presenter_business_rules))
        else:
            st.caption("The presenter did not state any explicit business rules in this video.")
        rules_text = st.text_area(
            "Edit rules (one per line)",
            value="\n".join(suite.presenter_business_rules),
            height=120,
            key=f"business_rules_{editor_version}",
            help="Edited rules are included in the JSON export and the Gherkin feature comments.",
        )

    st.markdown("#### Test case matrix")
    st.caption(
        "Edit any cell, delete rows, or add new rows with the row controls. "
        "Steps use `1. action | test data | expected result` per line."
    )
    display_df = suite_to_dataframe(suite)
    edited_df = st.data_editor(
        display_df,
        key=f"test_case_editor_{editor_version}",
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        column_config=_editor_config(),
        column_order=list(TABLE_COLUMNS),
    )

    rules = [line.strip() for line in str(rules_text or "").splitlines() if line.strip()]

    # Exports always reflect the *current* edited widgets.
    try:
        exports = export_bundle(
            edited_df,
            suite,
            summary_default,
            rules,
            extra_context=PromptContext(
                feature_name=settings["feature_name"],
                target_roles=settings["target_roles"],
                output_focus=settings["output_focus"],
                extra_notes=settings["extra_notes"],
            ),
        )
        export_error = None
    except ValueError as exc:
        exports = {}
        export_error = str(exc)

    st.markdown("#### Step-by-step detail")
    detail_cases = _detailed_cases(edited_df, suite)
    with st.expander(f"Detailed view ({len(detail_cases)} test cases)", expanded=False):
        for case in detail_cases:
            st.markdown(f"**{case.id} - {case.title}**  \n`{case.category}` | `{case.priority}` | `{case.video_timestamp}` | {case.source_basis}")
            if case.preconditions:
                st.caption("Preconditions: " + "; ".join(case.preconditions))
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "#": step.step_number,
                            "Action": step.action,
                            "Test Data": step.test_data or "-",
                            "Expected Result": step.expected_result,
                        }
                        for step in case.steps
                    ]
                ),
                hide_index=True,
                width="stretch",
            )

    _publish_exports(exports)

    st.subheader("3. Export")
    if export_error:
        st.error(f"Exports need attention: {export_error}")
    base_slug = slugify(settings["feature_name"] or suite.feature_summary.split(".")[0])
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    col_csv, col_json, col_feature = st.columns(3)
    with col_csv:
        st.download_button(
            "Download CSV",
            data=exports.get("csv", b""),
            file_name=f"{base_slug}-{stamp}.csv",
            mime="text/csv",
            width="stretch",
            disabled=not exports,
            on_click="ignore",
            help="Flattened, one row per test case - TestRail / Jira / Excel friendly.",
        )
    with col_json:
        st.download_button(
            "Download JSON",
            data=exports.get("json", b""),
            file_name=f"{base_slug}-{stamp}.json",
            mime="application/json",
            width="stretch",
            disabled=not exports,
            on_click="ignore",
            help="Full Pydantic TestSuite (nested steps, rules, summary).",
        )
    with col_feature:
        st.download_button(
            "Download Gherkin",
            data=exports.get("gherkin", b""),
            file_name=f"{base_slug}-{stamp}.feature",
            mime="text/plain",
            width="stretch",
            disabled=not exports,
            on_click="ignore",
            help="Cucumber .feature file with tags, preconditions tables and Given/When/Then steps.",
        )
    if exports:
        sizes = " / ".join(
            f"{name.upper()} {_human_size(len(data))}" for name, data in exports.items()
        )
        st.caption(
            "Exports are regenerated from the edited table on every rerun, so downloads "
            f"always match what you see ({sizes}). Invalid rows are skipped with a warning."
        )


def _publish_exports(exports: Mapping[str, bytes]) -> None:
    """Store this run's export payloads so download sizes can be reported.

    ``st.download_button`` does not expose its payload back to the caller, so the
    payloads are kept in session state (a few KB) to show sizes next to the buttons.
    """

    st.session_state[SS_EXPORTS] = dict(exports)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    st.set_page_config(
        page_title=f"{APP_TITLE} | Video to QA Test Cases",
        page_icon=APP_ICON,
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _init_state()
    settings = _render_sidebar()

    st.title(f"{APP_ICON} {APP_TITLE}")
    st.caption(
        "Turn a demo / walkthrough recording into an editable, exportable QA test suite "
        "using Gemini video understanding (visual UI + presenter audio)."
    )

    uploaded_file = _render_upload_workspace(settings)
    if uploaded_file is not None:
        config = AnalysisConfig(
            model_id=settings["model_id"],
            fps=float(settings["fps"]),
            media_resolution=settings["media_resolution"],
            thinking_level=settings["thinking_level"],
            backend=settings["backend"],
            prompt_context=settings["prompt_context"],
        )
        if settings["backend"] == BACKEND_AI_STUDIO:
            credentials: AIStudioCredentials | VertexCredentials = AIStudioCredentials(
                api_key=settings["api_key"]
            )
        else:
            credentials = VertexCredentials(
                project_id=settings["project_id"], location=settings["location"]
            )
        _run_generation(uploaded_file, config, credentials, settings["gcs_uri"])

    if st.session_state[SS_ERROR] and st.session_state[SS_RESULT] is None:
        st.error(f"Last run failed: {st.session_state[SS_ERROR]}")

    result: AnalysisResult | None = st.session_state[SS_RESULT]
    if result is not None:
        _render_results(result, settings)
    else:
        st.divider()
        with st.expander("How it works", expanded=False):
            st.markdown(
                "\n".join(
                    [
                        "1. **Upload** - the video is streamed to the Gemini Files API (AI Studio) "
                        "or sent inline / by `gs://` URI (Vertex AI).",
                        "2. **Process** - Gemini validates and indexes the video; the app polls "
                        "`client.files.get()` until the state is `ACTIVE` and aborts cleanly on `FAILED`.",
                        "3. **Analyze** - a single multimodal call receives the video part first "
                        "(`fps`, media resolution, thinking level), then the QA prompt.",
                        "4. **Validate** - the JSON answer is parsed into the Pydantic `TestSuite` "
                        "schema and normalized (sequential ids, clean timestamp windows).",
                        "5. **Export** - edit the table, then download CSV, JSON or Gherkin.",
                    ]
                )
            )
        st.info(
            "Session persistence is on: results survive reruns, so editing, downloading "
            "or tweaking the sidebar will never clear your generated suite."
        )


if __name__ == "__main__":
    main()
