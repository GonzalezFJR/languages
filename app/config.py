from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    app_name: str = "Lextor"
    app_env: str = "development"
    debug: bool = True
    secret_key: str = "change-me-in-production"
    host: str = "0.0.0.0"
    port: int = 8000
    contents_dir: str = "static/contents"

    # Admin credentials
    admin_user: str = "admin"
    admin_password: str = "admin"

    # LLM pipeline
    llm_provider: str = "openai"          # openai | anthropic | google | ...
    llm_model: str = "gpt-4o-mini"
    llm_api_key: str = ""

    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def contents_path(self) -> Path:
        return Path(self.contents_dir)


settings = Settings()
