from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FLEET_", extra="ignore")

    admin_token: SecretStr = Field(min_length=32)
    data_dir: Path = Path("/var/lib/sage-fleet-authority")
    public_base_url: str = "https://fleet-api.sage.delivery"
    enrollment_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    client_cert_days: int = Field(default=90, ge=1, le=365)

    @property
    def database_path(self) -> Path:
        return self.data_dir / "fleet-authority.sqlite3"

