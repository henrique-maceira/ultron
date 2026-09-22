"""Ponto de entrada do Ultron.

Monta o banco, o cérebro de IA, o bot do Telegram e os jobs proativos, e roda o bot
em long-polling até ser interrompido (Ctrl+C).
"""
from __future__ import annotations

import logging

from assistant import db
from assistant.brain import get_brain
from bot.jobs import register_jobs
from bot.telegram_bot import build_application
from config import load_config


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger("ultron")

    config = load_config()
    db.configure(config.db_path)

    brain = get_brain(config)
    application = build_application(config, brain)
    register_jobs(application, config, brain)

    logger.info("Ultron no ar. Provedor=%s, modelo=%s, fuso=%s",
                config.llm_provider, config.llm_model, config.timezone)
    application.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
