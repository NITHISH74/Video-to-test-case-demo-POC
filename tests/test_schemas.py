"""Schema validation, timestamp normalization and prompt construction."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas import (
    FOCUS_DIRECTIVES,
    SYSTEM_INSTRUCTION,
    PromptContext,
    TestCase,
    TestStep,
    TestSuite,
    build_user_prompt,
    normalize_suite,
    normalize_timestamp,
)


# ---------------------------------------------------------------------------
# Timestamp normalization
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("00:12-00:41", "00:12-00:41"),
        ("0:12-0:41", "00:12-00:41"),
        (" 00:12 - 00:41 ", "00:12-00:41"),
        ("00:12\u201300:41", "00:12-00:41"),  # en dash
        ("00:12\u201400:41", "00:12-00:41"),  # em dash
        ("01:02:03-01:02:59", "62:03-62:59"),  # HH:MM:SS keeps duration semantics
        ("00:00-00:00", "00:00-00:00"),
        ("100:00-101:30", "100:00-101:30"),
    ],
)
def test_normalize_timestamp_accepts_and_canonicalizes(raw: str, expected: str) -> None:
    assert normalize_timestamp(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "00:12",
        "00:12-",
        "-00:41",
        "00:61-01:00",
        "00:41-00:12",  # ends before start
        "abc-def",
        "00:12-00:41-00:50",
        "-1:00-00:10",
    ],
)
def test_normalize_timestamp_rejects_invalid(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_timestamp(raw)


# ---------------------------------------------------------------------------
# Model validation
# ---------------------------------------------------------------------------
def _case(**overrides):
    payload = {
        "id": "TC-009",
        "title": "Redeem promo code",
        "category": "Happy Path",
        "priority": "High",
        "video_timestamp": "0:05-0:30",
        "source_basis": "Observed in UI",
        "preconditions": ["Logged in"],
        "steps": [
            {"step_number": 2, "action": "Click redeem", "test_data": "", "expected_result": "Applied"},
            {"step_number": 1, "action": "Open cart", "test_data": "-", "expected_result": "Cart visible"},
        ],
    }
    payload.update(overrides)
    return payload


def test_test_case_normalizes_timestamp_and_orders_steps() -> None:
    case = TestCase.model_validate(_case())
    assert case.video_timestamp == "00:05-00:30"
    assert [step.step_number for step in case.steps] == [1, 2]
    assert [step.action for step in case.steps] == ["Open cart", "Click redeem"]


@pytest.mark.parametrize("field_name", ["category", "priority", "source_basis"])
def test_literals_reject_unknown_values(field_name: str) -> None:
    with pytest.raises(ValidationError):
        TestCase.model_validate(_case(**{field_name: "Not A Valid Value"}))


def test_category_accepts_every_documented_literal() -> None:
    for category in ("Happy Path", "Negative", "Boundary", "Permissions", "UI/UX"):
        assert TestCase.model_validate(_case(category=category)).category == category


def test_priority_accepts_every_documented_literal() -> None:
    for priority in ("High", "Medium", "Low"):
        assert TestCase.model_validate(_case(priority=priority)).priority == priority


def test_source_basis_accepts_every_documented_literal() -> None:
    for source in ("Observed in UI", "Spoken by Presenter", "Inferred Edge Case"):
        assert TestCase.model_validate(_case(source_basis=source)).source_basis == source


def test_steps_must_not_be_empty() -> None:
    with pytest.raises(ValidationError):
        TestCase.model_validate(_case(steps=[]))


def test_step_requires_action_and_expected_result() -> None:
    with pytest.raises(ValidationError):
        TestStep.model_validate({"step_number": 1, "action": "", "expected_result": "x"})
    with pytest.raises(ValidationError):
        TestStep.model_validate({"step_number": 1, "action": "x"})


def test_preconditions_accept_string_or_list() -> None:
    assert TestCase.model_validate(_case(preconditions="Logged in\nCart has item")).preconditions == [
        "Logged in",
        "Cart has item",
    ]
    assert TestCase.model_validate(_case(preconditions=None)).preconditions == []


def test_test_suite_rules_accept_string() -> None:
    suite = TestSuite.model_validate(
        {
            "feature_summary": "  Summary  ",
            "presenter_business_rules": "Codes expire.\n- Codes are case sensitive",
            "test_cases": [_case()],
        }
    )
    assert suite.feature_summary == "Summary"
    assert suite.presenter_business_rules == ["Codes expire.", "Codes are case sensitive"]


# ---------------------------------------------------------------------------
# Suite normalization
# ---------------------------------------------------------------------------
def test_normalize_suite_renumbers_ids_and_dedupes_rules(sample_suite_factory=None) -> None:
    from conftest import VALID_SUITE_JSON

    suite = TestSuite.model_validate(VALID_SUITE_JSON)
    suite.presenter_business_rules = ["Rule A", "Rule A", " Rule B ", ""]
    normalized = normalize_suite(suite)
    assert [case.id for case in normalized.test_cases] == ["TC-001", "TC-002", "TC-003"]
    assert normalized.presenter_business_rules == ["Rule A", "Rule B"]


def test_schema_rejects_empty_test_cases() -> None:
    with pytest.raises(ValidationError):
        TestSuite(feature_summary="x", presenter_business_rules=[], test_cases=[])


def test_schema_rejects_empty_summary() -> None:
    with pytest.raises(ValidationError):
        TestSuite(feature_summary="", presenter_business_rules=[], test_cases=[])


def test_blank_summary_is_rejected_by_the_schema() -> None:
    # whitespace-only summaries pass min_length=1 but fail after cleaning
    with pytest.raises(ValidationError):
        TestSuite.model_validate(
            {"feature_summary": "   ", "presenter_business_rules": [], "test_cases": []}
        )





# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------
def test_system_instruction_covers_required_behaviours() -> None:
    text = SYSTEM_INSTRUCTION.lower()
    assert "visual" in text and "audio" in text
    assert "spoken by presenter" in text
    assert "mm:ss-mm:ss" in text
    assert "never invent" in text


def test_user_prompt_contains_context_and_focus() -> None:
    prompt = build_user_prompt(
        PromptContext(
            feature_name="Checkout - Promo Codes",
            target_roles="Shopper, Admin",
            output_focus="Security & Permissions",
            extra_notes="Codes must not be reusable.\nAudit every redemption.",
        )
    )
    assert "Checkout - Promo Codes" in prompt
    assert "Shopper, Admin" in prompt
    assert FOCUS_DIRECTIVES["Security & Permissions"] in prompt
    assert "Codes must not be reusable." in prompt
    assert "Audit every redemption." in prompt


def test_user_prompt_defaults_when_context_empty() -> None:
    prompt = build_user_prompt(None)
    assert "Infer the module/feature name" in prompt
    assert FOCUS_DIRECTIVES["Balanced"] in prompt


def test_unknown_focus_falls_back_to_balanced() -> None:
    prompt = build_user_prompt(PromptContext(output_focus="Chaos"))
    assert FOCUS_DIRECTIVES["Balanced"] in prompt
