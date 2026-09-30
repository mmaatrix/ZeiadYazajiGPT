from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Zeiad English Coach"
    app_host: str = "127.0.0.1"
    app_port: int = 8765

    native_language: str = "ar"
    target_language: str = "en"
    target_accent: str = "en-US"

    llm_provider: str = "mock"
    stt_provider: str = "local"
    tts_provider: str = "local"
    pronunciation_provider: str = "disabled"

    openai_api_key: str | None = None
    google_api_key: str | None = None

    local_llm_base_url: str = "http://127.0.0.1:8080"
    local_llm_model: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
