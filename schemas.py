"""Structured output schema, prompts and normalization helpers.

The Pydantic models in this module serve four purposes:

1. they are passed as ``response_schema`` to Gemini so the model *must* answer with
   machine-readable JSON,
2. they validate the model response before it reaches the UI,
3. they round-trip through the editable Streamlit table, and
4. they are the source of truth for the JSON export.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Allowed values (also used by the UI column configuration)
# ---------------------------------------------------------------------------
CATEGORIES: tuple[str, ...] = ("Happy Path", "Negative", "Boundary", "Permissions", "UI/UX")
PRIORITIES: tuple[str, ...] = ("High", "Medium", "Low")
SOURCE_BASES: tuple[str, ...] = ("Observed in UI", "Spoken by Presenter", "Inferred Edge Case")
OUTPUT_FOCUS_OPTIONS: tuple[str, ...] = (
    "Balanced",
    "Negative & Boundary Heavy",
    "Security & Permissions",
)
DEFAULT_OUTPUT_FOCUS: str = OUTPUT_FOCUS_OPTIONS[0]

Category = Literal["Happy Path", "Negative", "Boundary", "Permissions", "UI/UX"]
Priority = Literal["High", "Medium", "Low"]
SourceBasis = Literal["Observed in UI", "Spoken by Presenter", "Inferred Edge Case"]


# ---------------------------------------------------------------------------
# Timestamp helpers -- the model occasionally answers "0:05-0:30" instead of
# "00:05-00:30"; we normalize instead of failing the whole run.
# ---------------------------------------------------------------------------
_TIMESTAMP_SPLIT = re.compile(r"\s*[-\u2013\u2014]\s*")
_NORMALIZED_TIMESTAMP = re.compile(r"^(\d{2,3}):([0-5]\d)-(\d{2,3}):([0-5]\d)$")


def _clock_to_seconds(value: str) -> int:
    """Convert ``MM:SS`` or ``HH:MM:SS`` into a number of seconds."""

    parts = value.strip().split(":")
    if not 2 <= len(parts) <= 3:
        raise ValueError(f"invalid timestamp component {value!r}; expected MM:SS")
    try:
        numbers = [int(part) for part in parts]
    except ValueError as exc:
        raise ValueError(f"invalid timestamp component {value!r}; expected digits") from exc
    if any(number < 0 for number in numbers):
        raise ValueError(f"invalid negative timestamp component {value!r}")
    seconds = numbers[-1]
    minutes = numbers[-2]
    hours = numbers[-3] if len(numbers) == 3 else 0
    if seconds > 59 or minutes > 59:
        raise ValueError(f"invalid timestamp {value!r}; minutes and seconds must be < 60")
    return hours * 3600 + minutes * 60 + seconds


def _seconds_to_clock(total_seconds: int) -> str:
    """Format a number of seconds as ``MM:SS`` (minutes may exceed 99)."""

    return f"{total_seconds // 60:02d}:{total_seconds % 60:02d}"


def normalize_timestamp(value: Any) -> str:
    """Return a canonical ``MM:SS-MM:SS`` window.

    Accepts en dashes, em dashes and surrounding whitespace. Raises ``ValueError``
    when the value is not a valid start/end window.
    """

    text = str(value).strip()
    fast_path = _NORMALIZED_TIMESTAMP.match(text)
    if fast_path is not None:
        start = int(fast_path.group(1)) * 60 + int(fast_path.group(2))
        end = int(fast_path.group(3)) * 60 + int(fast_path.group(4))
        if end < start:
            raise ValueError(f"timestamp {text!r} ends before it starts")
        return text
    parts = _TIMESTAMP_SPLIT.split(text)
    if len(parts) != 2 or not all(part.strip() for part in parts):
        raise ValueError(
            f"timestamp {text!r} must be a start-end window formatted as MM:SS-MM:SS"
        )
    start = _clock_to_seconds(parts[0])
    end = _clock_to_seconds(parts[1])
    if end < start:
        raise ValueError(f"timestamp {text!r} ends before it starts")
    return f"{_seconds_to_clock(start)}-{_seconds_to_clock(end)}"


# ---------------------------------------------------------------------------
# Schema models
# ---------------------------------------------------------------------------
def _coerce_string_list(value: Any) -> list[str]:
    """Best-effort conversion of model output into a clean ``list[str]``."""

    if value is None:
        return []
    if isinstance(value, str):
        items: list[Any] = re.split(r"[\n;]+", value)
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        items = [value]
    cleaned: list[str] = []
    for item in items:
        text = str(item).strip().lstrip("-*\u2022 ").strip()
        if text:
            cleaned.append(text)
    return cleaned


class TestStep(BaseModel):
    """One imperative action inside a test case."""

    __test__ = False  # not a pytest test class despite the Test* name

    step_number: int = Field(..., ge=1, description="1-based order of the step")
    action: str = Field(..., description="Imperative action performed by the tester")
    test_data: str = Field(
        default="",
        description="Concrete data used in the action, or an empty string when none",
    )
    expected_result: str = Field(
        ..., description="Observable result expected after the action"
    )

    @field_validator("action", "test_data", "expected_result", mode="before")
    @classmethod
    def _clean_text(cls, value: Any) -> str:
        return "" if value is None else str(value).strip()

    @field_validator("step_number", mode="before")
    @classmethod
    def _positive_step_number(cls, value: Any) -> int:
        number = int(value)
        if number < 1:
            raise ValueError("step_number must be >= 1")
        return number

    @field_validator("action")
    @classmethod
    def _action_required(cls, value: str) -> str:
        if not value:
            raise ValueError("step action must not be empty")
        return value


class TestCase(BaseModel):
    """A single executable test case extracted from the walkthrough."""

    __test__ = False

    id: str = Field(..., description='Stable identifier such as "TC-001"')
    title: str = Field(..., description="Short behavioural title")
    category: Category = Field(..., description="Test category")
    priority: Priority = Field(..., description="Business priority")
    video_timestamp: str = Field(
        ..., description="Evidence window in the video, formatted MM:SS-MM:SS"
    )
    source_basis: SourceBasis = Field(
        ..., description="Where the evidence for this test case came from"
    )
    preconditions: list[str] = Field(
        default_factory=list,
        description="Required roles, data or state before the first step",
    )
    steps: list[TestStep] = Field(
        ..., min_length=1, description="Ordered executable steps"
    )

    @field_validator("id", "title", mode="before")
    @classmethod
    def _clean_label(cls, value: Any) -> str:
        return "" if value is None else str(value).strip()

    @field_validator("id")
    @classmethod
    def _id_required(cls, value: str) -> str:
        if not value:
            raise ValueError("test case id must not be empty")
        return value

    @field_validator("title")
    @classmethod
    def _title_required(cls, value: str) -> str:
        if not value:
            raise ValueError("test case title must not be empty")
        return value

    @field_validator("video_timestamp", mode="before")
    @classmethod
    def _valid_timestamp(cls, value: Any) -> str:
        return normalize_timestamp(value)

    @field_validator("preconditions", mode="before")
    @classmethod
    def _clean_preconditions(cls, value: Any) -> list[str]:
        return _coerce_string_list(value)

    @model_validator(mode="after")
    def _order_steps(self) -> TestCase:
        ordered = sorted(self.steps, key=lambda step: step.step_number)
        renumbered = [
            step.model_copy(update={"step_number": index})
            for index, step in enumerate(ordered, start=1)
        ]
        if renumbered != self.steps:
            object.__setattr__(self, "steps", renumbered)
        return self


class TestSuite(BaseModel):
    """Complete QA suite produced for one demo video."""

    __test__ = False

    feature_summary: str = Field(
        ..., min_length=1, description="2-4 sentence summary of the demonstrated feature"
    )
    presenter_business_rules: list[str] = Field(
        default_factory=list,
        description="Business rules, limits and caveats stated by the presenter",
    )
    test_cases: list[TestCase] = Field(
        ...,
        min_length=1,
        description="Generated test cases (the model must return at least one)",
    )

    @field_validator("feature_summary", mode="before")
    @classmethod
    def _clean_summary(cls, value: Any) -> str:
        return "" if value is None else str(value).strip()

    @field_validator("presenter_business_rules", mode="before")
    @classmethod
    def _clean_rules(cls, value: Any) -> list[str]:
        return _coerce_string_list(value)


# ---------------------------------------------------------------------------
# Normalization of a freshly generated suite
# ---------------------------------------------------------------------------
def normalize_suite(suite: TestSuite) -> TestSuite:
    """Return a deterministic, UI-ready copy of ``suite``.

    * test case ids are renumbered sequentially (``TC-001``, ...),
    * duplicate/blank business rules are removed,
    * an empty summary gets an explicit, honest fallback message,
    * a suite without test cases is rejected.
    """

    if not suite.test_cases:
        raise ValueError(
            "The model returned zero test cases. Try a different output focus, "
            "a higher thinking level, or a longer walkthrough video."
        )
    cases = [
        case.model_copy(update={"id": f"TC-{index:03d}"})
        for index, case in enumerate(suite.test_cases, start=1)
    ]
    rules: list[str] = []
    seen: set[str] = set()
    for rule in suite.presenter_business_rules:
        if rule not in seen:
            seen.add(rule)
            rules.append(rule)
    summary = suite.feature_summary or "No feature summary was returned by the model."
    return TestSuite(
        feature_summary=summary,
        presenter_business_rules=rules,
        test_cases=cases,
    )


# ---------------------------------------------------------------------------
# Prompt engineering
# ---------------------------------------------------------------------------
SYSTEM_INSTRUCTION = """You are a senior QA engineer who converts recorded product demo videos into precise, executable test suites.

You receive one screen-recorded walkthrough of a software feature with presenter narration. Analyze BOTH evidence streams:
1. Visual UI evidence: every screen, form field, button, menu, toast, inline validation message, empty/loading/error state and transition that is actually shown.
2. Spoken audio evidence: the presenter's narration, especially caveats, constraints, limits, required roles and "you can only ... if ..." statements.

RULES FOR TEST CASES
- Produce atomic, independently executable test cases. Aim for 10-25 cases; return at least 6 whenever the video shows enough material.
- id: sequential "TC-001", "TC-002", ... in output order.
- category: exactly one of "Happy Path", "Negative", "Boundary", "Permissions", "UI/UX".
- priority: "High" for revenue, access-control, data-integrity or security paths; "Medium" for core flow variants; "Low" for cosmetic checks.
- Every spoken caveat, constraint, limit or permission rule MUST be converted into at least one Negative, Boundary or Permissions test case whose source_basis is "Spoken by Presenter".
- source_basis values:
  * "Observed in UI" - behaviour demonstrated on screen.
  * "Spoken by Presenter" - behaviour or rule stated in the audio, including converted caveats.
  * "Inferred Edge Case" - a standard QA edge case derived from observed behaviour; never a fabricated UI element.
- video_timestamp: "MM:SS-MM:SS" of the exact evidence window. Use only timestamps you can justify from the video, never guess, and never exceed the video duration.
- steps: 3-8 imperative steps. Each step has an action, concrete test_data (empty string when no data is used) and an expected_result describing observable UI feedback.
- preconditions: roles, data, feature flags or state required before step 1.
- Never invent UI elements, labels, fields, integrations or messages that are neither visible nor explicitly mentioned. When a detail cannot be verified, express it as a precondition or omit it.
- feature_summary: 2-4 sentences describing what the feature does and who it is for.
- presenter_business_rules: every business rule, limit or caveat stated by the presenter, one item each. Return an empty list if the presenter states none.

Return only JSON that conforms to the provided schema."""

FOCUS_DIRECTIVES: dict[str, str] = {
    "Balanced": (
        "Balance the suite across the whole walkthrough: cover the happy path, "
        "at least one negative case per validated input, boundary cases for stated "
        "limits, permission checks and UI/UX feedback."
    ),
    "Negative & Boundary Heavy": (
        "Bias the suite towards Negative and Boundary coverage: invalid inputs, "
        "empty states, maximum/minimum lengths, special characters, repeated "
        "submissions and failure recovery. Keep only the essential happy paths "
        "for context (at least 60% of cases must be Negative or Boundary)."
    ),
    "Security & Permissions": (
        "Bias the suite towards Permissions and security behaviour: unauthenticated "
        "access, role escalation attempts, cross-tenant or cross-user data access, "
        "session expiry, audit/visibility rules and every access caveat the "
        "presenter mentions. Include high-priority Negative cases for bypass attempts."
    ),
}


@dataclass(slots=True)
class PromptContext:
    """Optional user supplied context merged into the user prompt."""

    feature_name: str = ""
    target_roles: str = ""
    output_focus: str = DEFAULT_OUTPUT_FOCUS
    extra_notes: str = ""


def build_user_prompt(context: PromptContext | None = None) -> str:
    """Build the text part that follows the video part in ``contents``."""

    ctx = context or PromptContext()
    focus = ctx.output_focus if ctx.output_focus in FOCUS_DIRECTIVES else DEFAULT_OUTPUT_FOCUS
    feature_name = ctx.feature_name.strip() or "Infer the module/feature name from the video."
    target_roles = ctx.target_roles.strip() or "Not specified; infer from the UI and narration."
    lines = [
        "Generate the QA test suite for the software walkthrough in the attached video.",
        "",
        "CONTEXT",
        f"- Module / feature: {feature_name}",
        f"- Target user roles: {target_roles}",
        f"- Output focus: {focus}",
    ]
    notes = ctx.extra_notes.strip()
    if notes:
        lines.append("- Extra PRD notes (treat as authoritative context):")
        lines.extend(f"    {note.strip()}" for note in notes.splitlines() if note.strip())
    lines.extend(
        [
            "",
            "FOCUS DIRECTIVE",
            FOCUS_DIRECTIVES[focus],
            "",
            "REMINDERS",
            "- Analyze the visual UI and the spoken narration together, not just the screens.",
            "- Cite a MM:SS-MM:SS evidence window on every test case.",
            "- Convert every spoken caveat into a Negative, Boundary or Permissions case with source_basis \"Spoken by Presenter\".",
            "- Do not invent UI that is neither visible nor mentioned; mark derived checks as \"Inferred Edge Case\".",
            "- Return strict JSON only - no markdown fences, no commentary.",
        ]
    )
    return "\n".join(lines)
