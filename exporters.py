"""Pure conversion helpers between ``TestSuite``, the editable table and exports.

Keeping this logic out of ``app.py`` makes the round-trip
(Pydantic -> DataFrame -> edited DataFrame -> Pydantic/CSV/JSON/Gherkin)
unit-testable without a Streamlit runtime.
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any, Sequence

import pandas as pd

from schemas import (
    CATEGORIES,
    DEFAULT_OUTPUT_FOCUS,
    PRIORITIES,
    SOURCE_BASES,
    PromptContext,
    TestCase,
    TestStep,
    TestSuite,
    normalize_timestamp,
)

# ---------------------------------------------------------------------------
# Column contract shared by the editor, CSV export and round-trip parsing
# ---------------------------------------------------------------------------
COL_ID = "ID"
COL_TITLE = "Title"
COL_CATEGORY = "Category"
COL_PRIORITY = "Priority"
COL_TIMESTAMP = "Video Timestamp"
COL_SOURCE = "Source Basis"
COL_PRECONDITIONS = "Preconditions"
COL_STEPS = "Steps"

TABLE_COLUMNS: tuple[str, ...] = (
    COL_ID,
    COL_TITLE,
    COL_CATEGORY,
    COL_PRIORITY,
    COL_TIMESTAMP,
    COL_SOURCE,
    COL_PRECONDITIONS,
    COL_STEPS,
)

CSV_COLUMNS: tuple[str, ...] = TABLE_COLUMNS + ("Step Count",)

PRECONDITION_SEPARATOR = "\n"
STEP_SEPARATOR = "\n"
STEP_FIELD_SEPARATOR = " | "


# ---------------------------------------------------------------------------
# Cell <-> model conversions
# ---------------------------------------------------------------------------
NO_TEST_DATA = "-"


def steps_to_text(steps: Sequence[TestStep]) -> str:
    """Render steps into a cell (and CSV) friendly multi-line string.

    Every line always has exactly three ``action | test_data | expected`` fields so
    the cell can be parsed back unambiguously; a step without data uses ``-``.
    """

    lines = []
    for step in steps:
        action = step.action.replace(STEP_FIELD_SEPARATOR, "/")
        test_data = (step.test_data or NO_TEST_DATA).replace(STEP_FIELD_SEPARATOR, "/")
        expected = step.expected_result.replace(STEP_FIELD_SEPARATOR, "/")
        lines.append(
            f"{step.step_number}. {action}{STEP_FIELD_SEPARATOR}{test_data}"
            f"{STEP_FIELD_SEPARATOR}{expected}"
        )
    return STEP_SEPARATOR.join(lines)


def _clean_cell_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):  # type: ignore[arg-type]
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value)
    return "" if text.strip().lower() == "nan" else text


def text_to_steps(text: Any) -> list[TestStep]:
    """Parse the edited steps cell back into ``TestStep`` models.

    Accepts ``"1. action | data | expected"`` lines as produced by
    :func:`steps_to_text`. Missing trailing fields are tolerated so users can
    quickly type a bare action; a completely empty cell raises ``ValueError``.
    """

    raw = _clean_cell_text(text)
    steps: list[TestStep] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        number_match = re.match(r"^\s*(\d+)[.)]?\s+(.*)$", line)
        if number_match:
            number = int(number_match.group(1))
            body = number_match.group(2)
        else:
            number = len(steps) + 1
            body = line
        fields = [field.strip() for field in body.split(STEP_FIELD_SEPARATOR)]
        action = fields[0] if fields else ""
        test_data = fields[1] if len(fields) > 1 else ""
        if test_data in {"-", "n/a", "N/A", "none", "null"}:
            test_data = ""
        expected = fields[2] if len(fields) > 2 else ""
        if not action:
            continue
        if not expected:
            expected = "No explicit expected result provided."
        steps.append(
            TestStep(
                step_number=number,
                action=action,
                test_data=test_data,
                expected_result=expected,
            )
        )
    if not steps:
        raise ValueError("a test case needs at least one step (for example '1. Open the page | - | Page loads')")
    return [
        step.model_copy(update={"step_number": index})
        for index, step in enumerate(steps, start=1)
    ]


def preconditions_to_text(preconditions: Sequence[str]) -> str:
    return PRECONDITION_SEPARATOR.join(preconditions)


def text_to_preconditions(text: Any) -> list[str]:
    raw = _clean_cell_text(text) if not isinstance(text, (list, tuple)) else "\n".join(str(t) for t in text)
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _coerce_choice(value: Any, allowed: Sequence[str], fallback: str) -> str:
    """Map user text onto an allowed literal (case insensitive)."""

    if value is None:
        return fallback
    text = str(value).strip()
    for option in allowed:
        if text.lower() == option.lower():
            return option
    lowered = text.lower()
    for option in allowed:
        if lowered in option.lower() or option.lower() in lowered:
            return option
    return fallback


def _coerce_priority(value: Any) -> str:
    text = _clean_cell_text(value).strip().lower()
    if not text:
        return "Medium"
    aliases = {"p0": "High", "p1": "High", "p2": "Medium", "p3": "Low", "high": "High", "medium": "Medium", "low": "Low"}
    if text in aliases:
        return aliases[text]
    return _coerce_choice(value, PRIORITIES, "Medium")


def _coerce_category(value: Any) -> str:
    return _coerce_choice(value, CATEGORIES, "Happy Path")


def _coerce_source(value: Any) -> str:
    return _coerce_choice(value, SOURCE_BASES, "Observed in UI")


def _cell(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):  # type: ignore[arg-type]
            return ""
    except (TypeError, ValueError):
        pass
    return str(value)


# ---------------------------------------------------------------------------
# Suite <-> DataFrame
# ---------------------------------------------------------------------------
def suite_to_dataframe(suite: TestSuite) -> pd.DataFrame:
    """Build the display/editor DataFrame (all columns are text for flexibility)."""

    rows = [
        {
            COL_ID: case.id,
            COL_TITLE: case.title,
            COL_CATEGORY: case.category,
            COL_PRIORITY: case.priority,
            COL_TIMESTAMP: case.video_timestamp,
            COL_SOURCE: case.source_basis,
            COL_PRECONDITIONS: preconditions_to_text(case.preconditions),
            COL_STEPS: steps_to_text(case.steps),
        }
        for case in suite.test_cases
    ]
    return pd.DataFrame(rows, columns=list(TABLE_COLUMNS), dtype="object")


def dataframe_to_cases(frame: pd.DataFrame) -> tuple[list[TestCase], list[str]]:
    """Convert an edited DataFrame into models, collecting per-row warnings.

    Rows that cannot be parsed are skipped and reported instead of failing the
    whole export, so users never lose their other edits.
    """

    cases: list[TestCase] = []
    warnings: list[str] = []
    if frame is None or len(frame) == 0:
        return cases, ["The table is empty - nothing to export."]

    for position, (_, row) in enumerate(frame.iterrows(), start=1):
        title = _cell(row.get(COL_TITLE)).strip()
        row_label = _cell(row.get(COL_ID)).strip() or f"row {position}"
        if not title:
            warnings.append(f"{row_label}: skipped because the title is empty.")
            continue
        try:
            timestamp = normalize_timestamp(_cell(row.get(COL_TIMESTAMP)) or "00:00-00:00")
        except ValueError as exc:
            warnings.append(f"{row_label}: skipped because the timestamp is invalid ({exc}).")
            continue
        try:
            steps = text_to_steps(row.get(COL_STEPS))
        except ValueError as exc:
            warnings.append(f"{row_label}: skipped because {exc}.")
            continue
        try:
            case = TestCase(
                id=_cell(row.get(COL_ID)).strip() or f"TC-{position:03d}",
                title=title,
                category=_coerce_category(_cell(row.get(COL_CATEGORY))),
                priority=_coerce_priority(_cell(row.get(COL_PRIORITY))),
                video_timestamp=timestamp,
                source_basis=_coerce_source(_cell(row.get(COL_SOURCE))),
                preconditions=text_to_preconditions(row.get(COL_PRECONDITIONS)),
                steps=steps,
            )
        except Exception as exc:  # noqa: BLE001 - reported to the user
            warnings.append(f"{row_label}: skipped because it is invalid ({exc}).")
            continue
        cases.append(case)

    renumbered = [
        case.model_copy(update={"id": f"TC-{index:03d}"})
        for index, case in enumerate(cases, start=1)
    ]
    return renumbered, warnings


def build_suite_from_edited(
    frame: pd.DataFrame,
    base_suite: TestSuite | None = None,
    feature_summary: str | None = None,
    business_rules: Sequence[str] | None = None,
) -> tuple[TestSuite, list[str]]:
    """Rebuild a full ``TestSuite`` from the edited table.

    The summary/rules come from the UI inputs (or the original suite) so edits to
    the table never drop the generated narrative content.
    """

    cases, warnings = dataframe_to_cases(frame)
    if not cases:
        raise ValueError(
            "No valid rows remain in the table - fix the highlighted rows before exporting. "
            + " ".join(warnings)
        )
    summary = (
        (feature_summary or "").strip()
        or (base_suite.feature_summary if base_suite else "")
        or "No feature summary provided."
    )
    rules = list(business_rules) if business_rules is not None else (
        list(base_suite.presenter_business_rules) if base_suite else []
    )
    suite = TestSuite(
        feature_summary=summary,
        presenter_business_rules=rules,
        test_cases=cases,
    )
    return suite, warnings


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------
def dataframe_to_csv_bytes(suite: TestSuite) -> bytes:
    """Flatten the suite to CSV with one row per test case (TestRail/Jira ready).

    Steps are flattened into a single multi-line cell (``1. action | data | expected``)
    and a ``Step Count`` column is added so imports can validate completeness.
    """

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(CSV_COLUMNS), lineterminator="\n")
    writer.writeheader()
    for case in suite.test_cases:
        writer.writerow(
            {
                COL_ID: case.id,
                COL_TITLE: case.title,
                COL_CATEGORY: case.category,
                COL_PRIORITY: case.priority,
                COL_TIMESTAMP: case.video_timestamp,
                COL_SOURCE: case.source_basis,
                COL_PRECONDITIONS: " ; ".join(case.preconditions),
                COL_STEPS: steps_to_text(case.steps),
                "Step Count": len(case.steps),
            }
        )
    return buffer.getvalue().encode("utf-8")


def suite_to_json_bytes(suite: TestSuite) -> bytes:
    """Full nested Pydantic JSON (indent=2, enums/None serialized safely)."""

    payload = suite.model_dump(mode="json")
    return json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")


def _gherkin_escape(text: str) -> str:
    return " ".join(str(text).split())


def _gherkin_tag(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "unknown"


def _with_test_data(action: str, test_data: str) -> str:
    data = (test_data or "").strip()
    if data and data not in {"-", "n/a", "N/A", "none", "no data"}:
        return f"{action} (test data: {data})"
    return action


def suite_to_gherkin(suite: TestSuite, extra_context: PromptContext | None = None) -> str:
    """Render the suite as a Cucumber/Gherkin ``.feature`` document.

    Each test case becomes one ``Scenario`` with a preconditions table and a
    Given/When/Then chain. Narrative metadata (summary, business rules, evidence
    windows) is emitted as ``#`` comments so the file stays valid Gherkin.
    """

    summary = _gherkin_escape(suite.feature_summary)
    feature_name = summary.split(".")[0] if summary else "Generated QA suite"
    if len(feature_name) > 90:
        feature_name = feature_name[:87].rstrip() + "..."
    lines: list[str] = [f"Feature: {feature_name}"]
    lines.append(f"  # {summary}" if summary else "  # No feature summary provided.")
    if extra_context is not None:
        if extra_context.feature_name.strip():
            lines.append(f"  # Module: {_gherkin_escape(extra_context.feature_name)}")
        if extra_context.target_roles.strip():
            lines.append(f"  # Target roles: {_gherkin_escape(extra_context.target_roles)}")
    if suite.presenter_business_rules:
        lines.append("  # Business rules extracted from presenter audio:")
        for rule in suite.presenter_business_rules:
            lines.append(f"  # - {_gherkin_escape(rule)}")

    for case in suite.test_cases:
        lines.append("")
        tags = [
            f"@tc-{_gherkin_tag(case.id)}",
            f"@{_gherkin_tag(case.category)}",
            f"@priority-{_gherkin_tag(case.priority)}",
            f"@{_gherkin_tag(case.source_basis)}",
        ]
        lines.append("  " + " ".join(tags))
        lines.append(f"  Scenario: {_gherkin_escape(case.title)}")
        lines.append(f"    # Video evidence: {case.video_timestamp} ({case.source_basis})")
        if case.preconditions:
            lines.append("    Given the following preconditions are true:")
            for precondition in case.preconditions:
                lines.append(f"      | {_gherkin_escape(precondition)} |")
        total = len(case.steps)
        for index, step in enumerate(case.steps):
            action = _with_test_data(step.action, step.test_data)
            if index == 0:
                lines.append(f"    When {_gherkin_escape(action)}")
            elif index == total - 1:
                lines.append(f"    And {_gherkin_escape(action)}")
            else:
                lines.append(f"    And {_gherkin_escape(action)}")
            if step.expected_result:
                if index == 0 and total == 1:
                    lines.append(f"    Then {_gherkin_escape(step.expected_result)}")
                elif index < total - 1:
                    lines.append(f"    Then {_gherkin_escape(step.expected_result)}")
                else:
                    lines.append(f"    Then {_gherkin_escape(step.expected_result)}")
    lines.append("")
    return "\n".join(lines)


def csv_bytes_for_edited_suite(
    frame: pd.DataFrame,
    base_suite: TestSuite | None = None,
    feature_summary: str | None = None,
    business_rules: Sequence[str] | None = None,
) -> tuple[bytes, list[str]]:
    suite, warnings = build_suite_from_edited(
        frame, base_suite, feature_summary, business_rules
    )
    return dataframe_to_csv_bytes(suite), warnings


def export_bundle(
    frame: pd.DataFrame,
    base_suite: TestSuite,
    feature_summary: str,
    business_rules: Sequence[str],
    extra_context: PromptContext | None = None,
) -> dict[str, bytes]:
    """Build all three export payloads from the *current* edited table."""

    suite, _ = build_suite_from_edited(
        frame, base_suite, feature_summary, business_rules
    )
    return {
        "csv": dataframe_to_csv_bytes(suite),
        "json": suite_to_json_bytes(suite),
        "gherkin": suite_to_gherkin(suite, extra_context).encode("utf-8"),
    }


def slugify(value: str, fallback: str = "qa-test-suite") -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", (value or "").lower()).strip("-")
    return slug or fallback


DEFAULT_FOCUS = DEFAULT_OUTPUT_FOCUS
