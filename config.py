"""Carregamento de configuração a partir de variáveis de ambiente (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    anthropic_api_key: str
    telegram_bot_token: str
    owner_telegram_id: int
    llm_provider: str
    llm_model: str
    timezone: str
    briefing_time: str  # "HH:MM"
    db_path: str
    google_calendar_enabled: bool
    google_credentials_path: str
    google_token_path: str
    google_calendar_id: str

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def briefing_hour_minute(self) -> tuple[int, int]:
        hour, minute = self.briefing_time.split(":")
        return int(hour), int(minute)


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Variável de ambiente obrigatória ausente: {name}. "
            "Copie .env.example para .env e preencha os valores."
        )
    return value


def _as_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "sim", "on")


def load_config() -> Config:
    return Config(
        anthropic_api_key=_require("ANTHROPIC_API_KEY"),
        telegram_bot_token=_require("TELEGRAM_BOT_TOKEN"),
        owner_telegram_id=int(_require("OWNER_TELEGRAM_ID")),
        llm_provider=os.getenv("LLM_PROVIDER", "anthropic"),
        llm_model=os.getenv("LLM_MODEL", "claude-sonnet-5"),
        timezone=os.getenv("TIMEZONE", "America/Sao_Paulo"),
        briefing_time=os.getenv("BRIEFING_TIME", "08:00"),
        db_path=os.getenv("DB_PATH", "ultron.db"),
        google_calendar_enabled=_as_bool("GOOGLE_CALENDAR_ENABLED", False),
        google_credentials_path=os.getenv("GOOGLE_CREDENTIALS_PATH", "data/google_credentials.json"),
        google_token_path=os.getenv("GOOGLE_TOKEN_PATH", "data/google_token.json"),
        google_calendar_id=os.getenv("GOOGLE_CALENDAR_ID", "primary"),
    )
