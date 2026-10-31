"""SQLite schema grounding and read-only SQL execution."""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


# Statement keywords that write or change the database. REPLACE is only
# rejected as a statement (REPLACE INTO), not as the string function REPLACE().
FORBIDDEN_SQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|PRAGMA|"
    r"UPSERT|VACUUM|REINDEX|GRANT|REVOKE)\b|\bREPLACE\b(?!\s*\()",
    re.IGNORECASE,
)
STRING_LITERAL = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"")
DATE_LITERAL = re.compile(r"\b((?:19|20)\d{2})-\d{2}(?:-\d{2})?\b")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class SQLValidationError(ValueError):
    """Raised when a generated statement is not a safe single read-only query."""


class SQLExecutionError(RuntimeError):
    """Raised when a read-only query cannot be executed or returns unusable data."""


@dataclass
class QueryExecution:
    sql: str
    rows: list[tuple[Any, ...]]
    error: str | None = None


def normalize_text(value: str) -> str:
    """Lowercase, remove accents and punctuation (for text comparisons)."""

    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9_]+", " ", value.lower()).strip()


def strip_literals(sql: str) -> str:
    """Blank out string literals so keyword checks ignore quoted text."""

    return STRING_LITERAL.sub("''", sql)


def strip_fences(text: str) -> str:
    return re.sub(r"```(?:sql)?", "", text, flags=re.IGNORECASE).strip()


def validate_read_only(sql: str) -> str:
    """Return the statement if it is one read-only SELECT/WITH query."""

    statement = strip_fences(sql).rstrip(";").strip()
    if not statement:
        raise SQLValidationError("The generated SQL is empty.")
    bare = strip_literals(statement)
    if ";" in bare:
        raise SQLValidationError("Only one SQL statement is allowed per step.")
    if FORBIDDEN_SQL.search(bare):
        raise SQLValidationError("Only read-only SQL is allowed.")
    if "%Q" in statement:
        raise SQLValidationError(
            "SQLite strftime does not support %Q; use explicit date ranges."
        )
    if not re.match(r"^(SELECT|WITH)\b", statement, flags=re.IGNORECASE):
        raise SQLValidationError("The statement must start with SELECT or WITH.")
    return statement


TRUNCATED_DATE = re.compile(r"strftime\s*\(\s*'(%Y-%m|%Y)'", re.IGNORECASE)
FULL_DATE_LITERAL = re.compile(r"'\d{4}-\d{2}-\d{2}'")


def lint_sql(sql: str) -> list[str]:
    """Problems SQLite accepts silently but that change the result (Deliverable 3 linter).

    Comparing a truncated date such as strftime('%Y-%m', sale_date) = '2026-03' with a
    full date '2026-03-01' is valid SQL, yet the string comparison drops whole months
    (the Deliverable 2 failure case q09). No error is raised, so only a lint finds it.
    """

    problems = []
    if TRUNCATED_DATE.search(sql) and FULL_DATE_LITERAL.search(sql):
        problems.append(
            "The query compares a truncated date (strftime('%Y-%m', ...) or strftime('%Y', ...)) "
            "with full dates 'YYYY-MM-DD'; the string comparison silently drops rows. Filter the "
            "date column directly, for example sale_date BETWEEN '2026-03-01' AND '2026-05-31'."
        )
    return problems


def connect_read_only(db_path: str | Path) -> sqlite3.Connection:
    """Open the database file in SQLite read-only mode."""

    path = Path(db_path).resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    return sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)


def sales_date_range(conn: sqlite3.Connection) -> tuple[str, str] | None:
    try:
        minimum, maximum = conn.execute(
            "SELECT MIN(sale_date), MAX(sale_date) FROM sales"
        ).fetchone()
    except sqlite3.Error:
        return None
    return (minimum, maximum) if minimum and maximum else None


def check_date_years(conn: sqlite3.Connection, sql: str) -> None:
    """Reject date literals from years without data (invented years)."""

    date_range = sales_date_range(conn)
    if date_range is None:
        return
    first_year, last_year = int(date_range[0][:4]), int(date_range[1][:4])
    for year in DATE_LITERAL.findall(sql):
        if not first_year <= int(year) <= last_year:
            raise SQLExecutionError(
                f"Date literal year {year} has no data; available sales dates are "
                f"{date_range[0]} to {date_range[1]}."
            )


def execute_read_only(conn: sqlite3.Connection, sql: str) -> QueryExecution:
    """Validate and execute one statement of the structured assistant."""

    statement = validate_read_only(sql)
    check_date_years(conn, statement)
    try:
        rows = [tuple(row) for row in conn.execute(statement).fetchall()]
    except sqlite3.Error as exc:
        raise SQLExecutionError(str(exc)) from exc
    return QueryExecution(sql=statement, rows=rows)


def extract_select_statements(text: str) -> list[str]:
    """Deliverable 1 baseline extraction: SELECT statements split on ';'."""

    text = re.sub(r"```sql|```", "", text, flags=re.IGNORECASE)
    parts = [part.strip() for part in text.split(";")]
    return [part for part in parts if part and part.upper().startswith("SELECT")]


def execute_baseline(conn: sqlite3.Connection, raw_output: str) -> list[QueryExecution]:
    """Execute direct-prompting output as in Deliverable 1, on a read-only connection."""

    executions = []
    for statement in extract_select_statements(raw_output):
        try:
            rows = [tuple(row) for row in conn.execute(statement).fetchall()]
            executions.append(QueryExecution(statement, rows))
        except sqlite3.Error as exc:  # Baseline errors are evidence, not crashes.
            executions.append(QueryExecution(statement, [], str(exc)))
    return executions


def plain_schema(conn: sqlite3.Connection) -> str:
    """CREATE TABLE lines for every user table, in name order."""

    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    ]
    lines = []
    for table in tables:
        columns = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        rendered = ", ".join(f"{column[1]} {column[2]}" for column in columns)
        lines.append(f"CREATE TABLE {table} ({rendered});")
    return "\n".join(lines)


def grounded_schema(db_path: str | Path) -> str:
    """Schema plus exact text values, date range and join keys.

    This grounding is what the direct prompt lacks: it prevents translated
    labels ("Electronics") and invented years, the two most frequent baseline
    errors in Deliverable 1.
    """

    conn = connect_read_only(db_path)
    try:
        lines = [plain_schema(conn)]
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]
        for table in tables:
            for column in conn.execute(f'PRAGMA table_info("{table}")').fetchall():
                name, kind = column[1], (column[2] or "").upper()
                if kind != "TEXT":
                    continue
                values = [
                    str(row[0])
                    for row in conn.execute(
                        f'SELECT DISTINCT "{name}" FROM "{table}" '
                        f'WHERE "{name}" IS NOT NULL ORDER BY 1 LIMIT 50'
                    ).fetchall()
                ]
                if values and all(ISO_DATE.match(value) for value in values):
                    low, high = conn.execute(
                        f'SELECT MIN("{name}"), MAX("{name}") FROM "{table}"'
                    ).fetchone()
                    lines.append(
                        f"{table}.{name}: ISO dates from {low} to {high}; "
                        "there is no data outside this range."
                    )
                elif values:
                    rendered = ", ".join(repr(value) for value in values)
                    lines.append(f"{table}.{name} exact values: {rendered}")
        lines.append(
            "Joins: sales.product_id = products.product_id; "
            "inventory.product_id = products.product_id."
        )
        return "\n".join(lines)
    finally:
        conn.close()


def rows_to_json(rows: Iterable[tuple[Any, ...]]) -> list[list[Any]]:
    return [list(row) for row in rows]
