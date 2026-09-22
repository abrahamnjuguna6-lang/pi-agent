"""Loader for raw SQL migration files shipped inside the package."""

from importlib.resources import files


def read_sql(name: str) -> str:
    return (files("lifeos.db.migrations") / "sql" / name).read_text(encoding="utf-8")
