from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ACTI_", extra="ignore")
    database_url: SecretStr
    redis_url: SecretStr = SecretStr("redis://redis:6379/0")
    session_secret: SecretStr
    origin: str = "https://localhost"
    storage_root: str = "/documents"
    secure_cookie: bool = True
    source_sha: str = "development"
