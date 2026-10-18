import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


class Store:
    def __init__(self, path: Path):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self) -> None:
        statements = [
            """
            CREATE TABLE IF NOT EXISTS chats (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                run_id TEXT,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                user_message_id TEXT NOT NULL REFERENCES messages(id),
                status TEXT NOT NULL,
                model TEXT NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS run_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS steps (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                ordinal INTEGER NOT NULL,
                tool_name TEXT NOT NULL,
                title TEXT NOT NULL,
                status TEXT NOT NULL,
                input_json TEXT NOT NULL,
                output_summary TEXT,
                started_at TEXT NOT NULL,
                completed_at TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                step_id TEXT REFERENCES steps(id) ON DELETE SET NULL,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS experiments (
                id TEXT PRIMARY KEY,
                chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS messages_chat_idx ON messages(chat_id, created_at)",
            "CREATE INDEX IF NOT EXISTS runs_chat_idx ON runs(chat_id, created_at)",
            "CREATE INDEX IF NOT EXISTS events_run_idx ON run_events(run_id, id)",
            "CREATE INDEX IF NOT EXISTS artifacts_chat_idx ON artifacts(chat_id, created_at)",
        ]
        with self.connect() as connection:
            for statement in statements:
                connection.execute(statement)

    def create_chat(self, title: str = "New analysis") -> dict[str, Any]:
        chat_id = new_id("chat")
        timestamp = utc_now()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO chats (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (chat_id, title, timestamp, timestamp),
            )
        return self.get_chat(chat_id)

    def list_chats(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT chats.*,
                       (SELECT content FROM messages WHERE chat_id = chats.id ORDER BY created_at DESC LIMIT 1) AS preview,
                       (SELECT COUNT(*) FROM messages WHERE chat_id = chats.id) AS message_count
                FROM chats
                ORDER BY updated_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_chat(self, chat_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            chat = connection.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
            if chat is None:
                raise KeyError(chat_id)
            messages = connection.execute(
                "SELECT * FROM messages WHERE chat_id = ? ORDER BY created_at, rowid",
                (chat_id,),
            ).fetchall()
            runs = connection.execute(
                "SELECT * FROM runs WHERE chat_id = ? ORDER BY created_at",
                (chat_id,),
            ).fetchall()
            steps = connection.execute(
                """
                SELECT steps.* FROM steps
                JOIN runs ON runs.id = steps.run_id
                WHERE runs.chat_id = ?
                ORDER BY steps.started_at, steps.ordinal
                """,
                (chat_id,),
            ).fetchall()
            artifacts = connection.execute(
                "SELECT * FROM artifacts WHERE chat_id = ? ORDER BY created_at DESC",
                (chat_id,),
            ).fetchall()
        payload = dict(chat)
        payload["messages"] = [dict(row) for row in messages]
        payload["runs"] = [dict(row) for row in runs]
        payload["steps"] = [self._decode_row(row, ("input_json",)) for row in steps]
        payload["artifacts"] = [self._artifact_row(row) for row in artifacts]
        return payload

    def update_chat_title(self, chat_id: str, title: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE chats SET title = ?, updated_at = ? WHERE id = ?",
                (title, utc_now(), chat_id),
            )

    def delete_chat(self, chat_id: str) -> dict[str, list[str]]:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            chat = connection.execute("SELECT id FROM chats WHERE id = ?", (chat_id,)).fetchone()
            if chat is None:
                raise KeyError(chat_id)
            active_run = connection.execute(
                "SELECT id FROM runs WHERE chat_id = ? AND status IN ('queued', 'running') LIMIT 1",
                (chat_id,),
            ).fetchone()
            if active_run is not None:
                raise ValueError("active_run")
            run_ids = [
                row["id"]
                for row in connection.execute("SELECT id FROM runs WHERE chat_id = ?", (chat_id,)).fetchall()
            ]
            experiment_ids = [
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM experiments WHERE chat_id = ?",
                    (chat_id,),
                ).fetchall()
            ]
            connection.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
        return {"run_ids": run_ids, "experiment_ids": experiment_ids}

    def add_message(self, chat_id: str, role: str, content: str, run_id: str | None = None) -> dict[str, Any]:
        message_id = new_id("msg")
        timestamp = utc_now()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO messages (id, chat_id, run_id, role, content, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (message_id, chat_id, run_id, role, content, timestamp),
            )
            connection.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (timestamp, chat_id))
        return {
            "id": message_id,
            "chat_id": chat_id,
            "run_id": run_id,
            "role": role,
            "content": content,
            "created_at": timestamp,
        }

    def message_history(self, chat_id: str, limit: int = 30) -> list[dict[str, str]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT role, content FROM (
                    SELECT role, content, created_at, rowid
                    FROM messages WHERE chat_id = ?
                    ORDER BY created_at DESC, rowid DESC LIMIT ?
                ) ORDER BY created_at, rowid
                """,
                (chat_id, limit),
            ).fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in rows]

    def create_run(self, chat_id: str, user_message_id: str, model: str) -> dict[str, Any]:
        run_id = new_id("run")
        timestamp = utc_now()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO runs (id, chat_id, user_message_id, status, model, created_at) VALUES (?, ?, ?, 'queued', ?, ?)",
                (run_id, chat_id, user_message_id, model, timestamp),
            )
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return dict(row)

    def set_run_status(self, run_id: str, status: str, error: str | None = None) -> None:
        completed_at = utc_now() if status in {"completed", "failed"} else None
        with self.connect() as connection:
            connection.execute(
                "UPDATE runs SET status = ?, error = ?, completed_at = COALESCE(?, completed_at) WHERE id = ?",
                (status, error, completed_at, run_id),
            )

    def emit(self, run_id: str, event_type: str, payload: dict[str, Any]) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO run_events (run_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (run_id, event_type, json.dumps(payload, default=str), utc_now()),
            )
            return int(cursor.lastrowid)

    def events_after(self, run_id: str, event_id: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM run_events WHERE run_id = ? AND id > ? ORDER BY id",
                (run_id, event_id),
            ).fetchall()
        return [self._decode_row(row, ("payload_json",)) for row in rows]

    def create_step(self, run_id: str, tool_name: str, title: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        step_id = new_id("step")
        timestamp = utc_now()
        with self.connect() as connection:
            ordinal = connection.execute(
                "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM steps WHERE run_id = ?",
                (run_id,),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO steps (id, run_id, ordinal, tool_name, title, status, input_json, started_at)
                VALUES (?, ?, ?, ?, ?, 'running', ?, ?)
                """,
                (step_id, run_id, ordinal, tool_name, title, json.dumps(tool_input), timestamp),
            )
        return {
            "id": step_id,
            "run_id": run_id,
            "ordinal": ordinal,
            "tool_name": tool_name,
            "title": title,
            "status": "running",
            "input": tool_input,
            "started_at": timestamp,
        }

    def complete_step(self, step_id: str, status: str, summary: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE steps SET status = ?, output_summary = ?, completed_at = ? WHERE id = ?",
                (status, summary, utc_now(), step_id),
            )

    def add_artifact(
        self,
        run_id: str,
        chat_id: str,
        step_id: str | None,
        name: str,
        kind: str,
        mime_type: str,
        relative_path: str,
        size_bytes: int,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        artifact_id = new_id("artifact")
        timestamp = utc_now()
        metadata = metadata or {}
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO artifacts
                (id, run_id, chat_id, step_id, name, kind, mime_type, relative_path, size_bytes, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    run_id,
                    chat_id,
                    step_id,
                    name,
                    kind,
                    mime_type,
                    relative_path,
                    size_bytes,
                    json.dumps(metadata),
                    timestamp,
                ),
            )
        return {
            "id": artifact_id,
            "run_id": run_id,
            "chat_id": chat_id,
            "step_id": step_id,
            "name": name,
            "kind": kind,
            "mime_type": mime_type,
            "relative_path": relative_path,
            "size_bytes": size_bytes,
            "metadata": metadata,
            "created_at": timestamp,
            "download_url": f"/api/artifacts/{artifact_id}/download",
        }

    def get_artifact(self, artifact_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
        if row is None:
            raise KeyError(artifact_id)
        return self._artifact_row(row)

    def list_run_artifacts(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM artifacts WHERE run_id = ? ORDER BY created_at",
                (run_id,),
            ).fetchall()
        return [self._artifact_row(row) for row in rows]

    def save_experiment(self, chat_id: str, name: str, description: str) -> dict[str, Any]:
        experiment_id = new_id("experiment")
        timestamp = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO experiments (id, chat_id, name, description, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (experiment_id, chat_id, name, description, timestamp, timestamp),
            )
        return self.get_experiment(experiment_id)

    def list_experiments(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT experiments.*, chats.title AS chat_title,
                       (SELECT COUNT(*) FROM artifacts WHERE chat_id = experiments.chat_id) AS artifact_count
                FROM experiments JOIN chats ON chats.id = experiments.chat_id
                ORDER BY experiments.updated_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_experiment(self, experiment_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM experiments WHERE id = ?",
                (experiment_id,),
            ).fetchone()
        if row is None:
            raise KeyError(experiment_id)
        return dict(row)

    @staticmethod
    def _decode_row(row: sqlite3.Row, json_fields: Iterable[str]) -> dict[str, Any]:
        payload = dict(row)
        for field in json_fields:
            raw = payload.pop(field, None)
            payload[field.removesuffix("_json")] = json.loads(raw) if raw else None
        return payload

    def _artifact_row(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = self._decode_row(row, ("metadata_json",))
        payload["download_url"] = f"/api/artifacts/{payload['id']}/download"
        return payload
