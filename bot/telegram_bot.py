"""Handlers do bot do Telegram.

Constrói a Application do python-telegram-bot, restringe o acesso ao dono e liga as
mensagens de texto ao cérebro de IA (`Brain`).
"""
from __future__ import annotations

import base64
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

# Limites de anexos. Imagens: limite prático da API (~5 MB). PDF: 20 MB (limite de
# download do Bot API do Telegram). Texto: 1 MB para não estourar o contexto.
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_TEXT_BYTES = 1 * 1024 * 1024
SUPPORTED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}

# Instrução padrão quando o usuário manda um anexo sem legenda.
DEFAULT_ATTACHMENT_PROMPT = (
    "Analise este anexo e me ajude a entender o assunto e a organizar o que for "
    "relevante para minhas metas, tarefas e compromissos."
)

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
    "📎 Você também pode me *enviar imagens, PDFs e arquivos de texto* — eu leio o conteúdo "
    "(boleto, contrato, print, ementa) e te ajudo a organizar em cima disso.\n\n"
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

    async def _download(file_id: str) -> bytearray:
        tg_file = await application.bot.get_file(file_id)
        return await tg_file.download_as_bytearray()

    async def _run_multimodal(update: Update, content: list[dict], persist_text: str) -> None:
        user_id = update.effective_user.id
        await update.message.reply_chat_action(ChatAction.TYPING)
        try:
            reply = await brain.chat_multimodal(user_id, content, persist_text)
        except Exception:  # noqa: BLE001
            logger.exception("Erro ao processar anexo")
            reply = "Ops, não consegui processar esse anexo. Pode tentar de novo?"
        for chunk in _split_message(reply):
            await update.message.reply_text(chunk)

    async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_owner(update):
            return
        photo = update.message.photo[-1]  # maior resolução disponível
        if photo.file_size and photo.file_size > MAX_IMAGE_BYTES:
            await update.message.reply_text("Essa imagem é grande demais (máx. ~5 MB).")
            return
        data = await _download(photo.file_id)
        b64 = base64.standard_b64encode(bytes(data)).decode("utf-8")
        caption = (update.message.caption or "").strip()
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
            {"type": "text", "text": caption or DEFAULT_ATTACHMENT_PROMPT},
        ]
        persist_text = "[Imagem enviada]" + (f" Legenda: {caption}" if caption else "")
        await _run_multimodal(update, content, persist_text)

    async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_owner(update):
            return
        doc = update.message.document
        mime = (doc.mime_type or "").lower()
        caption = (update.message.caption or "").strip()
        instruction = caption or DEFAULT_ATTACHMENT_PROMPT
        name = doc.file_name or "arquivo"

        if mime in SUPPORTED_IMAGE_TYPES:
            if doc.file_size and doc.file_size > MAX_IMAGE_BYTES:
                await update.message.reply_text("Essa imagem é grande demais (máx. ~5 MB).")
                return
            b64 = base64.standard_b64encode(bytes(await _download(doc.file_id))).decode("utf-8")
            content = [
                {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
                {"type": "text", "text": instruction},
            ]
            persist_text = f"[Imagem enviada: {name}]" + (f" Legenda: {caption}" if caption else "")

        elif mime == "application/pdf":
            if doc.file_size and doc.file_size > MAX_PDF_BYTES:
                await update.message.reply_text("Esse PDF é grande demais (máx. 20 MB).")
                return
            b64 = base64.standard_b64encode(bytes(await _download(doc.file_id))).decode("utf-8")
            content = [
                {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": b64}},
                {"type": "text", "text": instruction},
            ]
            persist_text = f"[PDF enviado: {name}]" + (f" Legenda: {caption}" if caption else "")

        elif mime.startswith("text/") or mime in ("application/json", "application/csv"):
            if doc.file_size and doc.file_size > MAX_TEXT_BYTES:
                await update.message.reply_text("Esse arquivo de texto é grande demais (máx. 1 MB).")
                return
            raw = bytes(await _download(doc.file_id))
            try:
                texto = raw.decode("utf-8")
            except UnicodeDecodeError:
                texto = raw.decode("latin-1", errors="replace")
            content = [
                {"type": "text", "text": f"{instruction}\n\nConteúdo do arquivo '{name}':\n\n{texto}"},
            ]
            persist_text = f"[Arquivo de texto enviado: {name}]" + (f" Legenda: {caption}" if caption else "")

        else:
            await update.message.reply_text(
                f"Ainda não consigo ler arquivos do tipo '{mime or 'desconhecido'}'. "
                "Envie uma imagem, um PDF ou um arquivo de texto. 🙏"
            )
            return

        await _run_multimodal(update, content, persist_text)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    application.add_handler(MessageHandler(filters.PHOTO, on_photo))
    application.add_handler(MessageHandler(filters.Document.ALL, on_document))

    return application
