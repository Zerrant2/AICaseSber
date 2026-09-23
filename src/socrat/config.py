"""Настройки из переменных окружения / .env. Единая точка — `get_settings()`.

Все модули читают настройки ТОЛЬКО отсюда. Новые переменные добавляйте сюда и в .env.example
в одном PR.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Telegram / доступ ---
    telegram_bot_token: SecretStr = SecretStr("")
    admin_password: SecretStr = SecretStr("")
    # Секрет приложения: перец для HMAC-индекса паролей и хэширования chat_id сессий.
    app_secret: SecretStr = SecretStr("")
    session_ttl_hours: int = 12
    login_max_attempts: int = 5
    login_lockout_minutes: int = 10
    generated_password_length: int = 10

    # --- БД ---
    database_url: str = f"sqlite+aiosqlite:///{ROOT_DIR / 'data' / 'socrat.db'}"

    # --- LLM (OpenAI-совместимый API: OpenRouter / Ollama / vLLM) ---
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = "google/gemma-3-27b-it"
    llm_temperature: float = 0.4
    llm_max_tokens: int = 6000
    llm_timeout_s: int = 180
    llm_json_mode: str = Field("auto", description="auto | json_schema | json_object | prompt")
    llm_max_repairs: int = 2
    llm_price_in_per_1m: float | None = Field(None, description="USD за 1M входных токенов (для учёта)")
    llm_price_out_per_1m: float | None = None
    openrouter_app_name: str = "Socrat SK01"
    openrouter_app_url: str = "https://github.com/Zerrant2/AICaseSber"

    # --- Эмбеддинги (OpenAI-совместимый /embeddings). Пусто → только BM25 ---
    embeddings_base_url: str = ""
    embeddings_api_key: SecretStr = SecretStr("")
    embeddings_model: str = ""

    # --- Нормативная база ---
    knowledge_dir: Path = ROOT_DIR / "data" / "knowledge"
    sources_file: Path = ROOT_DIR / "src" / "socrat" / "knowledge" / "sources.yaml"
    case_reference_dir: Path = ROOT_DIR / "data" / "reference" / "sk01"

    # --- Предметные ограничения работы ---
    work_minutes_min: int = 15
    work_minutes_max: int = 20
    max_tasks: int = 8
    max_concurrent_generations: int = 3

    # --- Прочее ---
    use_fakes: bool = Field(False, description="True — бот работает на фейках (без LLM и базы)")
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
