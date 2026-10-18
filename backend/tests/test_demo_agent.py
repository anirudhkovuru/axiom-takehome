from pathlib import Path

import pytest

from backend.app.agent import AgentRunner
from backend.app.artifacts import ArtifactManager
from backend.app.config import Settings
from backend.app.data_access import DataAccess
from backend.app.python_runtime import PythonRuntime
from backend.app.store import Store


def test_demo_agent_creates_reproducible_scatterplot(tmp_path) -> None:
    database_path = Path(__file__).resolve().parents[2] / ".context" / "data" / "data.duckdb"
    if not database_path.exists():
        pytest.skip("Take-home dataset is not downloaded")

    settings = Settings(
        app_database_path=tmp_path / "app.sqlite3",
        artifacts_path=tmp_path / "artifacts",
        data_path=database_path,
        demo_mode=True,
    )
    store = Store(settings.app_database_path)
    store.initialize()
    data = DataAccess(database_path)
    artifacts = ArtifactManager(settings.artifacts_path, store)
    runner = AgentRunner(settings, store, data, artifacts, PythonRuntime(database_path))
    chat = store.create_chat()
    user_message = store.add_message(chat["id"], "user", "Plot bioactivity versus viability")
    run = store.create_run(chat["id"], user_message["id"], "local-demo")

    runner.run(run["id"])

    assert store.get_run(run["id"])["status"] == "completed"
    generated = store.list_run_artifacts(run["id"])
    assert {artifact["kind"] for artifact in generated} >= {"figure", "table", "code"}
    table = next(artifact for artifact in generated if artifact["kind"] == "table")
    table_path = settings.artifacts_path / table["relative_path"]
    assert len(table_path.read_text(encoding="utf-8").splitlines()) == 34
    assistant = store.get_chat(chat["id"])["messages"][-1]
    assert "n=33" in assistant["content"]
    assert "Pearson" in assistant["content"]
