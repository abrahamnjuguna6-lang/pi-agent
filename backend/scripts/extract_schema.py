"""Extract the migration SQL from design.md (design §24 DDL, §25 indexes).

design.md is the single source of truth for the schema. Until the first production release, the
initial migration SQL is regenerated from it with:

    uv run python scripts/extract_schema.py

After release, migrations are immutable: schema changes get a new Alembic revision (and design.md
is updated in the same change).
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESIGN = ROOT / "design.md"
OUT = ROOT / "backend/src/lifeos/db/migrations/sql"
HEADER = (
    "-- GENERATED from design.md {} by backend/scripts/extract_schema.py (T1.2). "
    "Edit design.md, then re-extract.\n\n"
)


def sql_blocks(text: str, start: str, end: str) -> list[str]:
    section = text[text.index(start) : text.index(end)]
    return re.findall(r"```sql\n(.*?)```", section, re.S)


def main() -> int:
    text = DESIGN.read_text(encoding="utf-8")
    schema = sql_blocks(text, "## 24. Data Model", "### 24.14")
    indexes = sql_blocks(text, "## 25. Database Indexes", "## 26.")
    (OUT / "0001_schema.sql").write_text(HEADER.format("§24") + "\n".join(schema), encoding="utf-8")
    (OUT / "0002_indexes.sql").write_text(HEADER.format("§25") + "\n".join(indexes), encoding="utf-8")
    print(f"extracted {len(schema)} schema blocks and {len(indexes)} index blocks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
