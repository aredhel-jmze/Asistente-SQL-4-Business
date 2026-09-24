"""SQLite inspection, schema retrieval and read-only SQL execution."""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


FORBIDDEN_SQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|PRAGMA|"
    r"REPLACE|UPSERT|VACUUM|REINDEX|GRANT|REVOKE)\b",
    re.IGNORECASE,
)


class SQLValidationError(ValueError):
    """Raised when a generated statement is not a safe read-only query."""


class SQLExecutionError(RuntimeError):
    """Raised when a read-only query cannot be executed."""


@dataclass
class QueryExecution:
    sql: str
    rows: list[tuple[Any, ...]]
    error: str | None = None


def normalize_text(value: str) -> str:
    """Normalize accents and punctuation for lightweight schema retrieval."""

    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9_]+", " ", value.lower()).strip()


def clean_generated_sql(text: str) -> str:
    """Remove a markdown fence and surrounding whitespace from one statement."""

    cleaned = re.sub(r"```(?:sql)?", "", text, flags=re.IGNORECASE).strip()
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].rstrip()
    return cleaned


def extract_sql_statements(text: str) -> list[str]:
    """Extract SELECT/WITH statements from model output in a stable order."""

    cleaned = re.sub(r"```(?:sql)?|```", "", text, flags=re.IGNORECASE)
    statements = []
    for candidate in cleaned.split(";"):
        candidate = candidate.strip()
        if candidate:
            statements.append(candidate)
    return statements


def validate_read_only(sql: str) -> str:
    """Validate one generated statement before it reaches SQLite."""

    statement = clean_generated_sql(sql).strip().rstrip(";").strip()
    if not statement:
        raise SQLValidationError("The generated SQL is empty.")
    if ";" in statement:
        raise SQLValidationError("Only one SQL statement is allowed per step.")
    if FORBIDDEN_SQL.search(statement):
        raise SQLValidationError("Only read-only SQL is allowed.")
    if "%Q" in statement:
        raise SQLValidationError(
            "SQLite strftime does not support %Q. Use explicit date ranges."
        )
    if re.search(r"\bFROM\s+(step_\d+|quarterly_sales)\b", statement, re.IGNORECASE):
        raise SQLValidationError(
            "A SQL step cannot reference a result or CTE from another step; "
            "each step must be self-contained."
        )
    if not re.match(r"^(SELECT|WITH)\b", statement, flags=re.IGNORECASE):
        raise SQLValidationError("The statement must start with SELECT or WITH.")
    return statement


def connect_read_only(db_path: str | Path) -> sqlite3.Connection:
    """Open SQLite in query-only mode and return rows as ordinary tuples."""

    path = Path(db_path).resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA query_only = ON")
    return conn


def execute_read_only(conn: sqlite3.Connection, sql: str) -> QueryExecution:
    """Validate and execute a single SELECT, preserving errors for retries."""

    statement = validate_read_only(sql)
    _validate_date_literals(conn, statement)
    try:
        rows = [tuple(row) for row in conn.execute(statement).fetchall()]
    except sqlite3.Error as exc:
        raise SQLExecutionError(str(exc)) from exc
    return QueryExecution(sql=statement, rows=rows)


def _validate_date_literals(conn: sqlite3.Connection, sql: str) -> None:
    """Reject date filters outside the dates actually present in the database."""

    literals = re.findall(r"\b20\d{2}-\d{2}(?:-\d{2})?\b", sql)
    if not literals:
        return
    try:
        minimum, maximum = conn.execute(
            "SELECT MIN(sale_date), MAX(sale_date) FROM sales"
        ).fetchone()
    except sqlite3.Error:
        return
    if not minimum or not maximum:
        return
    for literal in literals:
        width = len(literal)
        lower = minimum[:width]
        upper = maximum[:width]
        if not lower <= literal <= upper:
            raise SQLExecutionError(
                f"Date literal {literal} is outside the available sales range "
                f"{minimum} to {maximum}. Use dates from that range."
            )


def _table_schema(conn: sqlite3.Connection, table: str) -> str:
    columns = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    rendered = ", ".join(f"{row[1]} {row[2]}" for row in columns)
    return f"CREATE TABLE {table} ({rendered});"


def _table_values(conn: sqlite3.Connection, table: str) -> list[str]:
    """Collect small, safe examples to ground names and categories."""

    columns = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    text_columns = [row[1] for row in columns if row[2].upper() in {"TEXT", ""}]
    values: list[str] = []
    for column in text_columns:
        rows = conn.execute(
            f'SELECT DISTINCT "{column}" FROM "{table}" '
            f'WHERE "{column}" IS NOT NULL LIMIT 20'
        ).fetchall()
        values.extend(str(row[0]) for row in rows)
    return values


def _table_value_hints(conn: sqlite3.Connection, table: str) -> str:
    """Render exact text values so the model does not translate database labels."""

    columns = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    hints: list[str] = []
    for column in columns:
        name, kind = column[1], column[2].upper()
        if kind not in {"TEXT", ""}:
            continue
        values = conn.execute(
            f'SELECT DISTINCT "{name}" FROM "{table}" '
            f'WHERE "{name}" IS NOT NULL LIMIT 20'
        ).fetchall()
        if values:
            rendered = ", ".join(repr(str(row[0])) for row in values)
            hints.append(f"{table}.{name} values: {rendered}")
    return "Known exact values: " + "; ".join(hints) if hints else ""


def retrieve_relevant_schema(db_path: str | Path, question: str) -> str:
    """Return only tables likely relevant to a question and their joins."""

    conn = connect_read_only(db_path)
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            ).fetchall()
        ]
        normalized_question = normalize_text(question)
        words = set(normalized_question.split())
        aliases = {
            "sales": {"venta", "ventas", "ingreso", "ingresos", "unidad", "unidades", "crecimiento", "trimestre", "semestre", "mes", "periodo"},
            "products": {"producto", "productos", "categoria", "precio", "electronica", "hogar", "ropa", "alimentos", "notebook", "ollas"},
            "inventory": {"stock", "reorden", "inventario", "bajo"},
        }
        selected: list[str] = []
        for table in tables:
            table_words = set(normalize_text(table).split())
            values = set(normalize_text(" ".join(_table_values(conn, table))).split())
            if words & (table_words | aliases.get(table, set()) | values):
                selected.append(table)

        if not selected:
            selected = tables
        if "sales" in selected and "products" in tables and "products" not in selected:
            selected.append("products")
        if "inventory" in selected and "products" in tables and "products" not in selected:
            selected.append("products")
        selected = [table for table in tables if table in selected]

        definitions = [_table_schema(conn, table) for table in selected]
        value_hints = [_table_value_hints(conn, table) for table in selected]
        join_hint = (
            "Join sales.product_id = products.product_id and "
            "inventory.product_id = products.product_id when needed."
        )
        date_hint = ""
        if "sales" in selected:
            minimum, maximum = conn.execute(
                "SELECT MIN(sale_date), MAX(sale_date) FROM sales"
            ).fetchone()
            date_hint = (
                f"Available sales date range: {minimum} to {maximum}. "
                "Do not invent a different year."
            )
        return "\n".join(definitions + value_hints + [join_hint, date_hint])
    finally:
        conn.close()


def rows_to_json(rows: Iterable[tuple[Any, ...]]) -> list[list[Any]]:
    """Convert SQLite tuples into JSON-compatible rows."""

    return [list(row) for row in rows]
