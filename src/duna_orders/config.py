from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    app_env: str = "dev"
    log_level: str = "INFO"
    default_timezone: str = "America/Bogota"
    default_currency: str = "COP"
    duna_storage_backend: str = "memory"
    dashboard_target: str = "runtime"

    # LLM
    llm_provider: str = "anthropic"
    llm_model: str = "claude-sonnet-4-5"
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    llm_max_tokens: int = 1024
    llm_temperature: float = 0.0

    # Google Sheets
    google_sheets_credentials_path: Path = Path("./credentials/service_account.json")
    google_sheets_spreadsheet_id: str | None = None
    google_sheets_test_spreadsheet_id: str | None = None
    google_sheets_demo_spreadsheet_id: str | None = None
    active_client_sheet_id: str | None = None
    active_client_name: str = "demo"

    @property
    def resolved_dashboard_target(self) -> str:
        if self.dashboard_target.strip().lower() == "demo":
            return "demo"

        return "runtime"

    @property
    def is_dashboard_demo_target(self) -> bool:
        return self.resolved_dashboard_target == "demo"

    @property
    def dashboard_spreadsheet_id(self) -> str | None:
        if self.is_dashboard_demo_target:
            return self.google_sheets_demo_spreadsheet_id

        return self.google_sheets_spreadsheet_id


settings = Settings()