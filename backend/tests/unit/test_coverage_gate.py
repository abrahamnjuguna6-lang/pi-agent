"""T0.2: the coverage gate fails on uncovered branches in strict modules and low domain coverage."""

from typing import Any

from scripts.check_coverage import DOMAIN_MIN_PERCENT, evaluate


def file_entry(statements: int, covered: int, missing_branches: int = 0, partial: int = 0) -> dict[str, Any]:
    return {
        "summary": {
            "num_statements": statements,
            "covered_lines": covered,
            "missing_lines": statements - covered,
            "missing_branches": missing_branches,
            "num_partial_branches": partial,
        }
    }


STRICT = ("src/lifeos/domain/checkins.py",)


def test_fully_covered_strict_module_passes() -> None:
    report = {"files": {"/abs/path/src/lifeos/domain/checkins.py": file_entry(50, 50)}}
    assert evaluate(report, STRICT) == []


def test_uncovered_branch_in_strict_module_fails() -> None:
    report = {"files": {"src/lifeos/domain/checkins.py": file_entry(50, 50, missing_branches=1, partial=1)}}
    failures = evaluate(report, STRICT)
    assert len(failures) == 1
    assert failures[0].path == "src/lifeos/domain/checkins.py"
    assert "100% branch" in failures[0].reason


def test_missing_strict_module_is_not_yet_enforced() -> None:
    assert evaluate({"files": {}}, STRICT) == []


def test_low_domain_coverage_fails() -> None:
    report = {"files": {"src/lifeos/domain/goals.py": file_entry(100, 80)}}
    failures = evaluate(report, ())
    assert [f.path for f in failures] == ["src/lifeos/domain/"]
    assert str(DOMAIN_MIN_PERCENT) in failures[0].reason


def test_non_domain_files_do_not_count_toward_domain_threshold() -> None:
    report = {
        "files": {
            "src/lifeos/domain/goals.py": file_entry(100, 95),
            "src/lifeos/api/routers/goals.py": file_entry(100, 10),
        }
    }
    assert evaluate(report, ()) == []
