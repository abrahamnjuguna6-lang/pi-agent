"""Coverage gate (tasks.md T0.2, R24.4).

Reads a `coverage json` report and enforces:
  * >= 90% line coverage across `lifeos/domain` (aggregate), and
  * 100% branch coverage for every module listed in STRICT_BRANCH_MODULES — the
    status-transition logic named in tasks T5.4, T6.1, T6.4 (and other pure policy modules).

Usage: uv run python scripts/check_coverage.py coverage.json
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DOMAIN_PREFIX = "src/lifeos/domain/"
DOMAIN_MIN_PERCENT = 90.0

# Modules requiring 100% branch coverage. Add entries as the tasks that create them land.
STRICT_BRANCH_MODULES: tuple[str, ...] = (
    "src/lifeos/domain/checkins.py",  # T5.4
    "src/lifeos/domain/commitments.py",  # T6.1
    "src/lifeos/domain/accountability.py",  # T6.4
    "src/lifeos/domain/timeutil.py",  # T3.4
    "src/lifeos/domain/progress.py",  # T4.3
    "src/lifeos/domain/analytics/completion.py",  # T5.8
    "src/lifeos/notifications/policy.py",  # T9.1
)


@dataclass(frozen=True)
class Failure:
    path: str
    reason: str


def _normalize(path: str) -> str:
    """Coverage may record absolute or relative paths; compare on the `src/lifeos/...` suffix."""
    marker = "src/lifeos/"
    idx = path.replace("\\", "/").find(marker)
    return path[idx:] if idx >= 0 else path


def evaluate(
    report: dict[str, Any], strict_modules: tuple[str, ...] = STRICT_BRANCH_MODULES
) -> list[Failure]:
    files = {_normalize(p): data for p, data in report.get("files", {}).items()}
    failures: list[Failure] = []

    for module in strict_modules:
        data = files.get(module)
        if data is None:
            continue  # module not implemented yet; enforced once it exists
        summary = data["summary"]
        missing_branches = summary.get("missing_branches", 0)
        partial = summary.get("num_partial_branches", 0)
        if missing_branches or partial or summary.get("missing_lines", 0):
            failures.append(
                Failure(
                    module,
                    f"requires 100% branch coverage; missing_branches={missing_branches}, "
                    f"partial={partial}, missing_lines={summary.get('missing_lines', 0)}",
                )
            )

    statements = covered = 0
    for path, data in files.items():
        if path.startswith(DOMAIN_PREFIX):
            statements += data["summary"]["num_statements"]
            covered += data["summary"]["covered_lines"]
    if statements:
        percent = 100.0 * covered / statements
        if percent < DOMAIN_MIN_PERCENT:
            failures.append(
                Failure(DOMAIN_PREFIX, f"domain line coverage {percent:.1f}% < {DOMAIN_MIN_PERCENT}%")
            )
    return failures


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    report = json.loads(Path(argv[1]).read_text())
    failures = evaluate(report)
    for failure in failures:
        print(f"COVERAGE GATE FAILED: {failure.path}: {failure.reason}")
    if not failures:
        print("coverage gate passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
