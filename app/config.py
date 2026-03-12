from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    app_name: str = "Xlan Language Platform"
    app_env: str = "development"
    debug: bool = True
    secret_key: str = "change-me-in-production"
    host: str = "0.0.0.0"
    port: int = 8000
    contents_dir: str = "static/contents"

    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def contents_path(self) -> Path:
        return Path(self.contents_dir)


settings = Settings()
