from pathlib import Path

import pytest

from backend.app.data_access import DataAccess


@pytest.fixture
def data_access() -> DataAccess:
    path = Path(__file__).resolve().parents[2] / ".context" / "data" / "data.duckdb"
    if not path.exists():
        pytest.skip("Take-home dataset is not downloaded")
    return DataAccess(path)


def test_dataset_summary_matches_documentation(data_access: DataAccess) -> None:
    summary = data_access.summary()
    assert summary["compound_count"] == 33
    assert len(summary["tables"]) == 15
    assert {item["name"] for item in summary["modalities"]} >= {"RNA-seq", "2D PHH", "ADME"}


def test_queries_are_read_only(data_access: DataAccess) -> None:
    result = data_access.query("SELECT COUNT(*) AS compounds FROM compounds")
    assert result["rows"] == [[33]]
    with pytest.raises(ValueError, match="read-only"):
        data_access.query("DELETE FROM compounds")


def test_catalog_search_handles_multiple_terms(data_access: DataAccess) -> None:
    result = data_access.inspect("2d phh bioactivity viability")
    assert result["tables"]
    assert any("bioactivity" in item["feature"] for item in result["features"])
