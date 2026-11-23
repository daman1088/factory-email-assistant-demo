from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://fea:fea_dev_password@localhost:5432/email_assistant"
    test_database_url: str = "postgresql+psycopg://fea:fea_dev_password@localhost:5432/email_assistant_test"

    session_secret: str = "dev-insecure-session-secret"
    session_ttl_hours: int = 24 * 7
    token_encryption_key: str = ""
    cookie_secure: bool = False
    frontend_origin: str = "http://localhost:3000"

    llm_provider: Literal["anthropic", "fake"] = "fake"
    anthropic_api_key: str = Field(default="", repr=False)
    classify_model: str = "claude-haiku-5-5"
    summary_model: str = "claude-haiku-5-5"
    chat_model: str = "claude-sonnet-5-5"
    chat_effort: Literal["low", "medium", "high"] = "low"
    classify_batch_threshold: int = 50

    embedding_provider: Literal["fastembed", "fake"] = "fastembed"
    embedding_model: str = "BAAI/bge-small-en-v1.5"

    mail_provider: Literal["mock", "gmail"] = "mock"
    google_client_id: str = ""
    google_client_secret: str = Field(default="", repr=False)
    google_redirect_uri: str = "http://localhost:3000/api/gmail/callback"
    sync_interval_seconds: int = 300

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
