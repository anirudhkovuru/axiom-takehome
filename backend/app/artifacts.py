import csv
import json
import mimetypes
import shutil
import zipfile
from pathlib import Path
from typing import Any

from .store import Store


KIND_BY_SUFFIX = {
    ".csv": "table",
    ".json": "data",
    ".png": "figure",
    ".jpg": "figure",
    ".jpeg": "figure",
    ".html": "figure",
    ".py": "code",
    ".sql": "code",
    ".md": "report",
    ".txt": "text",
}


class ArtifactManager:
    def __init__(self, root: Path, store: Store):
        self.root = root.resolve()
        self.store = store
        self.root.mkdir(parents=True, exist_ok=True)

    def run_directory(self, run_id: str) -> Path:
        directory = self.root / run_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def register(
        self,
        path: Path,
        run_id: str,
        chat_id: str,
        step_id: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved = path.resolve()
        if self.root not in resolved.parents:
            raise ValueError("Artifact must live inside the artifact directory")
        suffix = resolved.suffix.lower()
        kind = KIND_BY_SUFFIX.get(suffix, "file")
        mime_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        return self.store.add_artifact(
            run_id=run_id,
            chat_id=chat_id,
            step_id=step_id,
            name=resolved.name,
            kind=kind,
            mime_type=mime_type,
            relative_path=str(resolved.relative_to(self.root)),
            size_bytes=resolved.stat().st_size,
            metadata=metadata,
        )

    def write_sql(
        self,
        run_id: str,
        chat_id: str,
        step_id: str,
        sql: str,
        filename: str = "query.sql",
    ) -> dict[str, Any]:
        path = self._available_path(self.run_directory(run_id), filename)
        path.write_text(sql.strip() + "\n", encoding="utf-8")
        return self.register(path, run_id, chat_id, step_id)

    def write_csv(
        self,
        run_id: str,
        chat_id: str,
        step_id: str,
        filename: str,
        columns: list[str],
        rows: list[list[Any]],
    ) -> dict[str, Any]:
        safe_name = Path(filename).name
        if not safe_name.lower().endswith(".csv"):
            safe_name += ".csv"
        path = self._available_path(self.run_directory(run_id), safe_name)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            writer.writerows(rows)
        return self.register(
            path,
            run_id,
            chat_id,
            step_id,
            metadata={"columns": columns, "row_count": len(rows)},
        )

    def build_experiment_export(self, experiment: dict[str, Any], chat: dict[str, Any]) -> Path:
        export_dir = self.root / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        export_path = export_dir / f"{experiment['id']}.zip"
        manifest = {
            "experiment": experiment,
            "chat": {key: chat[key] for key in ("id", "title", "created_at", "updated_at")},
            "messages": chat["messages"],
            "runs": chat["runs"],
            "steps": chat["steps"],
            "artifacts": chat["artifacts"],
        }
        report_lines = [
            f"# {experiment['name']}",
            "",
            experiment.get("description", ""),
            "",
            f"Conversation: {chat['title']}",
            "",
            "## Transcript",
            "",
        ]
        for message in chat["messages"]:
            report_lines.extend([f"### {message['role'].title()}", "", message["content"], ""])
        with zipfile.ZipFile(export_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, indent=2, default=str))
            archive.writestr("report.md", "\n".join(report_lines))
            for artifact in chat["artifacts"]:
                source = (self.root / artifact["relative_path"]).resolve()
                if source.exists() and self.root in source.parents:
                    archive.write(source, arcname=f"artifacts/{artifact['run_id']}/{source.name}")
        return export_path

    def resolve(self, relative_path: str) -> Path:
        path = (self.root / relative_path).resolve()
        if self.root not in path.parents:
            raise ValueError("Invalid artifact path")
        return path

    def delete_chat_files(self, run_ids: list[str], experiment_ids: list[str]) -> None:
        for run_id in run_ids:
            run_path = (self.root / run_id).resolve()
            if self.root in run_path.parents:
                shutil.rmtree(run_path, ignore_errors=True)
        for experiment_id in experiment_ids:
            export_path = (self.root / "exports" / f"{experiment_id}.zip").resolve()
            if self.root in export_path.parents:
                export_path.unlink(missing_ok=True)

    @staticmethod
    def _available_path(directory: Path, filename: str) -> Path:
        safe_name = Path(filename).name
        candidate = directory / safe_name
        counter = 2
        while candidate.exists():
            candidate = directory / f"{Path(safe_name).stem}-{counter}{Path(safe_name).suffix}"
            counter += 1
        return candidate


def copy_database_readme(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
