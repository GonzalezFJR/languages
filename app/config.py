from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Lextor"
    app_env: str = "development"
    debug: bool = False
    secret_key: str = "change-me-in-production"
    host: str = "0.0.0.0"
    port: int = 8000
    contents_dir: str = "static/contents"

    # Admin credentials
    admin_user: str = "admin"
    admin_password: str = "admin"

    # Public absolute URL (e.g. https://lextor.example.com). Used for MCP links; derived from the request if empty.
    public_url: str = ""
    # Comma-separated list of allowed Origin headers for the MCP endpoint ("*" = any)
    mcp_allowed_origins: str = "*"

    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def contents_path(self) -> Path:
        return Path(self.contents_dir)


settings = Settings()
