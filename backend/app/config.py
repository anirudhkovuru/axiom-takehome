from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Axiom Workbench"
    app_database_path: Path = ROOT_DIR / ".context" / "axiom-workbench.sqlite3"
    artifacts_path: Path = ROOT_DIR / ".context" / "artifacts"
    data_path: Path = ROOT_DIR / "data" / "data.duckdb"
    openai_api_key: str | None = None
    openai_model: str = "gpt-6-astra"
    demo_mode: bool = False
    allowed_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    @property
    def resolved_data_path(self) -> Path:
        if self.data_path.exists():
            return self.data_path
        workspace_copy = ROOT_DIR / ".context" / "data" / "data.duckdb"
        if workspace_copy.exists():
            return workspace_copy
        return self.data_path

    @property
    def use_demo_agent(self) -> bool:
        return self.demo_mode or not self.openai_api_key

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.app_database_path.parent.mkdir(parents=True, exist_ok=True)
    settings.artifacts_path.mkdir(parents=True, exist_ok=True)
    return settings
