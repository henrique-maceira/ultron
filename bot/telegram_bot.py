"""Handlers do bot do Telegram.

Constrói a Application do python-telegram-bot, restringe o acesso ao dono e liga as
mensagens de texto ao cérebro de IA (`Brain`).
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from assistant.brain import Brain
from config import Config

logger = logging.getLogger(__name__)

TELEGRAM_MAX = 4096

WELCOME = (
    "👋 Oi! Eu sou o *Ultron*, seu assistente pessoal.\n\n"
    "Pode falar comigo naturalmente. Eu te ajudo a:\n"
    "• 📝 anotar e organizar tarefas\n"
    "• ⏰ criar lembretes de compromissos (médico, dentista, prazos)\n"
    "• 🎯 priorizar o que fazer hoje\n"
    "• 🔎 pesquisar soluções na web e trazer um resumo pronto\n\n"
    "Experimente: _\"me lembra de ligar pro corretor amanhã 10h\"_ ou "
    "_\"o que eu devo priorizar hoje?\"_"
)

HELP = (
    "*Como usar o Ultron*\n\n"
    "É só conversar em linguagem natural. Alguns exemplos:\n"
    "• \"adiciona tarefa: montar caixas da mudança, prioridade alta\"\n"
    "• \"quais minhas tarefas de casa?\"\n"
    "• \"conclui a tarefa 3\"\n"
    "• \"me lembra de tomar o remédio todo dia às 8h\"\n"
    "• \"pesquisa empresas de mudança em SP e resume as opções\"\n"
    "• \"o que priorizar hoje?\"\n\n"
    "Comandos: /start, /help"
)


def _split_message(text: str, limit: int = TELEGRAM_MAX) -> list[str]:
    """Divide mensagens longas em pedaços respeitando o limite do Telegram."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks


def build_application(config: Config, brain: Brain) -> Application:
    application = Application.builder().token(config.telegram_bot_token).build()

    # Guarda dependências para uso nos handlers e jobs.
    application.bot_data["config"] = config
    application.bot_data["brain"] = brain

    def _is_owner(update: Update) -> bool:
        return bool(update.effective_user and update.effective_user.id == config.owner_telegram_id)

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_owner(update):
            return
        await update.message.reply_text(WELCOME, parse_mode="Markdown")

    async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_owner(update):
            return
        await update.message.reply_text(HELP, parse_mode="Markdown")

    async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_owner(update):
            await update.message.reply_text(
                "Este é um assistente pessoal e privado. 🙏"
            )
            return

        user_id = update.effective_user.id
        text = update.message.text or ""

        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        try:
            reply = await brain.chat(user_id, text)
        except Exception:  # noqa: BLE001
            logger.exception("Erro ao processar mensagem")
            reply = "Ops, tive um problema ao processar isso. Pode tentar de novo?"

        for chunk in _split_message(reply):
            await update.message.reply_text(chunk)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    return application
