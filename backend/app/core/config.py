import math

from pydantic import Field, SecretStr, field_validator
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
    sync_rollout_percentage: int = Field(default=100, ge=0, le=100)
    sync_rollout_sha256: str = ""
    sync_legacy_fallback_tag: str = ""
    trusted_proxy_cidrs: str = ""
    oidc_enabled: bool = False
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: SecretStr = SecretStr("")
    oidc_ca_file: str = ""
    oidc_label: str = "Корпоративная учётная запись"
    search_algorithm_version: str = "lexical-2"
    search_weights: dict[str, float] | None = {
        "number": 1.0,
        "title_exact": 0.95,
        "title_partial": 0.8,
        "metadata": 0.6,
        "content": 0.4,
        "fuzzy": 0.15,
    }
    search_synonyms: list[list[str]] | None = Field(
        default=[["ЖКХ", "жилищно-коммунальное хозяйство"], ["НПА", "нормативный правовой акт"]],
        max_length=32,
    )

    @field_validator("search_weights")
    @classmethod
    def valid_weights(cls, value):
        if value is not None and (
            set(value) - {"number", "title_exact", "title_partial", "metadata", "content", "fuzzy"}
            or any(not math.isfinite(v) or not 0 <= v <= 1 for v in value.values())
        ):
            raise ValueError("Search weights must be known finite values between zero and one")
        return value

    @field_validator("search_synonyms")
    @classmethod
    def bounded_synonyms(cls, value):
        if value is not None and any(
            not 2 <= len(group) <= 8 or any(not item.strip() or len(item) > 100 for item in group)
            for group in value
        ):
            raise ValueError("Synonym groups must contain 2–8 bounded nonempty expressions")
        return value
