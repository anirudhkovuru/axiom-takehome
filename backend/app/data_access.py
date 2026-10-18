import re
from pathlib import Path
from typing import Any

import duckdb


READ_ONLY_PREFIXES = {"select", "with", "show", "describe", "summarize", "explain"}


class DataAccess:
    def __init__(self, database_path: Path):
        self.database_path = database_path

    def connect(self) -> duckdb.DuckDBPyConnection:
        if not self.database_path.exists():
            raise FileNotFoundError(
                f"DuckDB file not found at {self.database_path}. Run scripts/download-data.sh first."
            )
        return duckdb.connect(str(self.database_path), read_only=True)

    def summary(self) -> dict[str, Any]:
        with self.connect() as connection:
            metadata = dict(connection.execute("SELECT key, value FROM dataset_metadata").fetchall())
            tables = [
                dict(zip([column[0] for column in connection.description], row, strict=True))
                for row in connection.execute(
                    """
                    SELECT table_name, modality, grain, primary_measure, row_count, compound_count, interpretation
                    FROM table_documentation ORDER BY row_count DESC
                    """
                ).fetchall()
            ]
            modality_rows = connection.execute(
                """
                SELECT modality, SUM(row_count) AS row_count, MAX(compound_count) AS compound_count
                FROM table_documentation GROUP BY modality ORDER BY row_count DESC
                """
            ).fetchall()
        return {
            "name": "Scientific Agent Takehome Data",
            "compound_count": int(metadata.get("compound_count", 33)),
            "generated_at": metadata.get("generated_at"),
            "anonymization": metadata.get("anonymization"),
            "tables": tables,
            "modalities": [
                {"name": row[0], "row_count": row[1], "compound_count": row[2]}
                for row in modality_rows
            ],
        }

    def inspect(self, topic: str) -> dict[str, Any]:
        normalized = topic.lower().strip()
        with self.connect() as connection:
            if normalized in {"all", "overview", "dataset", "schema"}:
                docs = connection.execute(
                    "SELECT * FROM table_documentation ORDER BY table_name"
                ).fetchdf()
                features = connection.execute(
                    "SELECT * FROM feature_inventory ORDER BY table_name, feature"
                ).fetchdf()
            else:
                terms = [term for term in re.findall(r"[a-z0-9_]+", normalized) if len(term) > 2]
                terms = terms or [normalized]
                doc_clause = " OR ".join(
                    ["(lower(table_name) LIKE ? OR lower(modality) LIKE ? OR lower(interpretation) LIKE ?)"]
                    * len(terms)
                )
                feature_clause = " OR ".join(
                    ["(lower(table_name) LIKE ? OR lower(feature) LIKE ? OR lower(notes) LIKE ?)"]
                    * len(terms)
                )
                parameters = [value for term in terms for value in (f"%{term}%",) * 3]
                docs = connection.execute(
                    f"""
                    SELECT * FROM table_documentation
                    WHERE {doc_clause}
                    ORDER BY table_name
                    """,
                    parameters,
                ).fetchdf()
                features = connection.execute(
                    f"""
                    SELECT * FROM feature_inventory
                    WHERE {feature_clause}
                    ORDER BY table_name, feature
                    """,
                    parameters,
                ).fetchdf()
        return {
            "topic": topic,
            "tables": docs.where(docs.notna(), None).to_dict(orient="records"),
            "features": features.where(features.notna(), None).to_dict(orient="records"),
        }

    def query(self, sql: str, max_rows: int = 500) -> dict[str, Any]:
        self._validate_sql(sql)
        with self.connect() as connection:
            relation = connection.sql(sql)
            columns = list(relation.columns)
            rows = relation.fetchmany(max_rows + 1)
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
        return {
            "columns": columns,
            "rows": [list(row) for row in rows],
            "row_count": len(rows),
            "truncated": truncated,
            "sql": sql,
        }

    @staticmethod
    def _validate_sql(sql: str) -> None:
        stripped = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
        stripped = re.sub(r"--.*?$", "", stripped, flags=re.MULTILINE).strip()
        if not stripped:
            raise ValueError("SQL query is empty")
        statements = [statement for statement in stripped.split(";") if statement.strip()]
        if len(statements) != 1:
            raise ValueError("Only one read-only SQL statement is allowed")
        first_word = statements[0].lstrip().split(None, 1)[0].lower()
        if first_word not in READ_ONLY_PREFIXES:
            raise ValueError("Only read-only SELECT, WITH, SHOW, DESCRIBE, SUMMARIZE, or EXPLAIN queries are allowed")
