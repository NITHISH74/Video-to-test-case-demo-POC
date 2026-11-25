"""Table round-trip, CSV/JSON/Gherkin exports and edited-row resilience."""

from __future__ import annotations

import csv
import io
import json
import re

import pandas as pd
import pytest

from conftest import sample_suite
from exporters import (
    COL_CATEGORY,
    COL_ID,
    COL_PRECONDITIONS,
    COL_PRIORITY,
    COL_SOURCE,
    COL_STEPS,
    COL_TIMESTAMP,
    COL_TITLE,
    CSV_COLUMNS,
    TABLE_COLUMNS,
    build_suite_from_edited,
    dataframe_to_cases,
    dataframe_to_csv_bytes,
    export_bundle,
    slugify,
    steps_to_text,
    suite_to_dataframe,
    suite_to_gherkin,
    suite_to_json_bytes,
    text_to_preconditions,
    text_to_steps,
)
from schemas import PromptContext


# ---------------------------------------------------------------------------
# Cell conversions
# ---------------------------------------------------------------------------
def test_steps_to_text_and_back_round_trip() -> None:
    suite = sample_suite()
    for case in suite.test_cases:
        text = steps_to_text(case.steps)
        parsed = text_to_steps(text)
        assert [step.action for step in parsed] == [step.action for step in case.steps]
        assert [step.test_data for step in parsed] == [step.test_data for step in case.steps]
        assert [step.expected_result for step in parsed] == [
            step.expected_result for step in case.steps
        ]


def test_text_to_steps_renumbers_and_tolerates_short_lines() -> None:
    steps = text_to_steps(
        "3. Open the page\n"
        "1. Enter SUMMER25 | SUMMER25 | Discount applied\n"
        "\n"
        "no number here | data | expected"
    )
    assert [step.step_number for step in steps] == [1, 2, 3]
    assert steps[0].action == "Open the page"
    assert steps[0].expected_result == "No explicit expected result provided."
    assert steps[0].test_data == ""
    assert steps[1].test_data == "SUMMER25"
    assert steps[2].action == "no number here"


def test_text_to_steps_rejects_empty_cell() -> None:
    with pytest.raises(ValueError):
        text_to_steps("   \n  ")


def test_preconditions_text_round_trip() -> None:
    assert text_to_preconditions("Logged in\nCart has 1 item\n\n") == [
        "Logged in",
        "Cart has 1 item",
    ]


# ---------------------------------------------------------------------------
# DataFrame round trip
# ---------------------------------------------------------------------------
def test_suite_to_dataframe_has_all_columns_and_rows() -> None:
    frame = suite_to_dataframe(sample_suite())
    assert list(frame.columns) == list(TABLE_COLUMNS)
    assert len(frame) == 2
    assert frame.iloc[0][COL_ID] == "TC-001"
    assert "Open checkout" in frame.iloc[0][COL_STEPS]


def test_dataframe_to_cases_renumbers_and_coerces_values() -> None:
    frame = suite_to_dataframe(sample_suite())
    frame.loc[0, COL_CATEGORY] = "ui/ux"  # case-insensitive coercion
    frame.loc[1, COL_PRIORITY] = "p1"  # alias coercion
    frame.loc[1, COL_SOURCE] = "SPOKEN BY PRESENTER"
    frame.loc[0, COL_TIMESTAMP] = "0:12-0:41"
    cases, warnings = dataframe_to_cases(frame)
    assert warnings == []
    assert [case.id for case in cases] == ["TC-001", "TC-002"]
    assert cases[0].category == "UI/UX"
    assert cases[1].priority == "High"
    assert cases[1].source_basis == "Spoken by Presenter"
    assert cases[0].video_timestamp == "00:12-00:41"


def test_dataframe_to_cases_skips_bad_rows_with_warnings() -> None:
    frame = suite_to_dataframe(sample_suite())
    frame.loc[0, COL_TITLE] = ""  # missing title
    frame.loc[1, COL_TIMESTAMP] = "not-a-window"
    cases, warnings = dataframe_to_cases(frame)
    assert cases == []
    assert len(warnings) == 2
    assert "title is empty" in warnings[0]
    assert "timestamp is invalid" in warnings[1]


def test_dataframe_to_cases_reports_empty_steps() -> None:
    frame = suite_to_dataframe(sample_suite())
    frame.loc[0, COL_STEPS] = ""
    cases, warnings = dataframe_to_cases(frame)
    assert len(cases) == 1
    assert "needs at least one step" in warnings[0]


def test_dataframe_to_cases_handles_added_rows_and_nan() -> None:
    frame = suite_to_dataframe(sample_suite())
    added = pd.DataFrame(
        [
            {
                COL_ID: "",
                COL_TITLE: "Brand new case",
                COL_CATEGORY: "Boundary",
                COL_PRIORITY: "Low",
                COL_TIMESTAMP: "02:00-02:20",
                COL_SOURCE: "Inferred Edge Case",
                COL_PRECONDITIONS: None,
                COL_STEPS: "1. Do a thing | - | It works",
            },
            {
                COL_ID: "TC-999",
                COL_TITLE: None,  # empty dynamic row from the editor
                COL_CATEGORY: None,
                COL_PRIORITY: None,
                COL_TIMESTAMP: None,
                COL_SOURCE: None,
                COL_PRECONDITIONS: None,
                COL_STEPS: None,
            },
        ]
    )
    combined = pd.concat([frame, added], ignore_index=True)
    cases, warnings = dataframe_to_cases(combined)
    assert [case.title for case in cases] == [
        "Apply a valid promo code",
        "Reject an expired promo code",
        "Brand new case",
    ]
    assert cases[-1].id == "TC-003"
    assert cases[-1].category == "Boundary"
    assert cases[-1].preconditions == []
    assert len(warnings) == 1 and "title is empty" in warnings[0]


def test_dataframe_to_cases_unknown_literals_fall_back() -> None:
    frame = suite_to_dataframe(sample_suite())
    frame.loc[0, COL_CATEGORY] = "Totally unknown"
    frame.loc[0, COL_PRIORITY] = ""
    frame.loc[0, COL_SOURCE] = ""
    cases, warnings = dataframe_to_cases(frame)
    assert cases[0].category == "Happy Path"
    assert cases[0].priority == "Medium"
    assert cases[0].source_basis == "Observed in UI"
    assert warnings == []


def test_build_suite_from_edited_keeps_summary_and_rules() -> None:
    base = sample_suite()
    frame = suite_to_dataframe(base)
    suite, warnings = build_suite_from_edited(
        frame, base, feature_summary="Edited summary", business_rules=["R1", "R2"]
    )
    assert suite.feature_summary == "Edited summary"
    assert suite.presenter_business_rules == ["R1", "R2"]
    assert warnings == []


def test_build_suite_from_edited_prefers_base_values_when_not_given() -> None:
    base = sample_suite()
    suite, _ = build_suite_from_edited(suite_to_dataframe(base), base, feature_summary="  ")
    assert suite.feature_summary == base.feature_summary
    assert suite.presenter_business_rules == base.presenter_business_rules


def test_build_suite_from_edited_rejects_all_invalid() -> None:
    frame = suite_to_dataframe(sample_suite())
    frame[COL_TITLE] = ""
    with pytest.raises(ValueError) as info:
        build_suite_from_edited(frame, sample_suite())
    assert "No valid rows" in str(info.value)


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
def test_csv_export_is_flattened_and_excel_friendly() -> None:
    payload = dataframe_to_csv_bytes(sample_suite())
    rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8"))))
    assert len(rows) == 2
    assert list(rows[0]) == list(CSV_COLUMNS)
    assert rows[0]["ID"] == "TC-001"
    assert rows[0]["Category"] == "Happy Path"
    assert rows[0]["Step Count"] == "2"
    assert "1. Open checkout | - | Checkout loads" in rows[0]["Steps"]
    assert "2. Enter SUMMER25 | SUMMER25 | Total drops" in rows[0]["Steps"]
    assert rows[1]["Preconditions"] == ""
    # a single header row, no index column
    assert payload.decode("utf-8").splitlines()[0] == ",".join(CSV_COLUMNS)


def test_csv_export_quotes_multi_line_cells() -> None:
    payload = dataframe_to_csv_bytes(sample_suite()).decode("utf-8")
    assert '"' in payload  # steps cell spans lines -> quoted by the csv module
    assert payload.count("\r") == 0


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------
def test_json_export_is_full_pydantic_payload() -> None:
    suite = sample_suite()
    payload = json.loads(suite_to_json_bytes(suite))
    assert payload == json.loads(suite.model_dump_json())
    assert payload["test_cases"][0]["steps"][0]["expected_result"] == "Checkout loads"
    assert payload["presenter_business_rules"][0].startswith("Promo codes expire")


# ---------------------------------------------------------------------------
# Gherkin
# ---------------------------------------------------------------------------
def test_gherkin_structure() -> None:
    text = suite_to_gherkin(
        sample_suite(),
        PromptContext(feature_name="Checkout", target_roles="Shopper"),
    )
    lines = text.splitlines()
    assert lines[0].startswith("Feature: ")
    assert "Scenario: Apply a valid promo code" in text
    assert "Scenario: Reject an expired promo code" in text
    assert "@tc-tc-001" in text and "@priority-high" in text
    assert "# Video evidence: 00:12-00:41 (Observed in UI)" in text
    assert "Given the following preconditions are true:" in text
    assert "| Shopper is logged in |" in text
    assert "When Open checkout" in text
    assert "Then Checkout loads" in text
    assert "(test data: SUMMER25)" in text
    assert "# - Promo codes expire after 30 days." in text
    # No step may repeat the same keyword more than once per line
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith(("#", "@", "Feature:", "Scenario:", "|")):
            assert re.match(r"^(Given|When|Then|And) .+", stripped), stripped


def test_gherkin_is_parseable_by_simple_scenario_counter() -> None:
    text = suite_to_gherkin(sample_suite())
    assert text.count("Scenario:") == 2
    assert text.endswith("\n")
    # every scenario has at least one Given/When/Then line after it
    blocks = [block for block in text.split("Scenario:")[1:]]
    for block in blocks:
        assert any(
            line.strip().startswith(("Given", "When", "Then", "And"))
            for line in block.splitlines()[1:]
        )


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------
def test_export_bundle_contains_three_payloads_from_edited_table() -> None:
    base = sample_suite()
    frame = suite_to_dataframe(base)
    frame.loc[0, COL_TITLE] = "Apply a valid promo code (edited)"
    bundle = export_bundle(frame, base, base.feature_summary, base.presenter_business_rules)
    assert set(bundle) == {"csv", "json", "gherkin"}
    assert b"edited" in bundle["csv"]
    assert b"edited" in bundle["json"]
    assert b"edited" in bundle["gherkin"]


def test_slugify() -> None:
    assert slugify("Checkout - Promo Codes!") == "checkout-promo-codes"
    assert slugify("") == "qa-test-suite"
    assert slugify("Витяги") == "qa-test-suite"
