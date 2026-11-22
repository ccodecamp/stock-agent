"""Regression tests for per-check evaluation reporting."""

from evals.run_evals import format_check_summary, summarize_checks


def test_summarize_checks_counts_individual_grades() -> None:
    records = [
        {"graded": {"grounded": {"passed": True}, "tool_used": {"passed": False}}},
        {"graded": {"grounded": {"passed": False}, "tool_used": {"passed": True}}},
        {"graded": {"grounded": {"passed": True}}},
    ]

    assert summarize_checks(records) == {"grounded": (2, 3), "tool_used": (1, 2)}


def test_format_check_summary_orders_weakest_first() -> None:
    records = [
        {"graded": {"strong": {"passed": True}, "weak": {"passed": False}}},
        {"graded": {"strong": {"passed": True}, "weak": {"passed": True}}},
    ]

    output = format_check_summary(records)

    assert output.startswith("By check (individual grades):")
    assert "weak" in output and "1/2" in output and "(50%)" in output
    assert "strong" in output and "2/2" in output and "(100%)" in output
    assert output.index("weak") < output.index("strong")


def test_empty_summary() -> None:
    assert summarize_checks([]) == {}
    assert format_check_summary([]) == "By check (individual grades):"
