"""Jobs proativos: disparo de lembretes e resumo diário de prioridades.

Usa o JobQueue embutido do python-telegram-bot (mesmo event loop do bot).
"""
from __future__ import annotations

import datetime as dt
import logging

from telegram.ext import Application, ContextTypes

from assistant import db
from assistant.brain import Brain
from config import Config

logger = logging.getLogger(__name__)

REMINDER_CHECK_INTERVAL = 60  # segundos
LOCAL_FMT = "%Y-%m-%dT%H:%M:%S"

BRIEFING_PROMPT = (
    "Gere agora o meu resumo diário ESTRATÉGICO. Chame get_agenda e get_goal_health para ver minhas "
    "metas (progresso, próxima etapa), o que está ATRASADO/PARADO/em risco, etapas com prazo próximo, "
    "tarefas, lembretes e compromissos. Depois me diga, de forma curta e motivadora: (1) em qual meta "
    "focar hoje e por quê, (2) as 1–3 etapas/tarefas mais importantes do dia, em ordem, (3) o que está "
    "atrasado ou em risco — e, se for o caso, PROPONHA um replanejamento (sem alterar nada ainda; peça "
    "meu ok), (4) os horários da agenda de hoje, e (5) um único próximo passo para começar agora. "
    "Se eu tiver orçamento definido, inclua uma linha curta do status financeiro do mês (get_expense_summary)."
)

WEEKLY_REVIEW_PROMPT = (
    "É a revisão semanal. Chame get_agenda e get_goal_health e, para cada meta ativa, avalie o progresso "
    "da semana: o que avançou, o que ficou parado e se o cronograma ainda é realista frente ao prazo. "
    "Se algo atrasou ou está em risco, PROPONHA replanejar com opções concretas (empurrar e comprimir "
    "etapas, ou mover o prazo da meta) — mas NÃO altere nada sem eu confirmar. Feche com o foco e os "
    "marcos da próxima semana, em poucas linhas."
)


def _now_local_str(config: Config) -> str:
    return dt.datetime.now(config.tz).strftime(LOCAL_FMT)


def _next_occurrence(remind_at: str, recurrence: str) -> str:
    base = dt.datetime.strptime(remind_at, LOCAL_FMT)
    delta = dt.timedelta(days=1) if recurrence == "daily" else dt.timedelta(weeks=1)
    # Avança até ficar no futuro (evita repetir várias vezes se o bot ficou offline).
    now = dt.datetime.now().replace(microsecond=0)
    nxt = base + delta
    while nxt <= now:
        nxt += delta
    return nxt.strftime(LOCAL_FMT)


def register_jobs(application: Application, config: Config, brain: Brain) -> None:
    async def check_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            due = db.due_reminders(_now_local_str(config))
        except Exception:  # noqa: BLE001
            logger.exception("Falha ao consultar lembretes")
            return

        for reminder in due:
            try:
                await context.bot.send_message(
                    chat_id=config.owner_telegram_id,
                    text=f"⏰ *Lembrete:* {reminder['text']}",
                    parse_mode="Markdown",
                )
            except Exception:  # noqa: BLE001
                logger.exception("Falha ao enviar lembrete %s", reminder["id"])
                continue

            recurrence = reminder.get("recurrence")
            if recurrence in ("daily", "weekly"):
                db.reschedule_reminder(reminder["id"], _next_occurrence(reminder["remind_at"], recurrence))
            else:
                db.mark_reminder_sent(reminder["id"])

    async def daily_briefing(context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            summary = await brain.chat(config.owner_telegram_id, BRIEFING_PROMPT, persist=False)
        except Exception:  # noqa: BLE001
            logger.exception("Falha ao gerar resumo diário")
            return
        await context.bot.send_message(
            chat_id=config.owner_telegram_id,
            text=f"☀️ *Bom dia! Seu resumo de hoje:*\n\n{summary}",
            parse_mode="Markdown",
        )

    async def weekly_review(context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            summary = await brain.chat(config.owner_telegram_id, WEEKLY_REVIEW_PROMPT, persist=False)
        except Exception:  # noqa: BLE001
            logger.exception("Falha ao gerar a revisão semanal")
            return
        await context.bot.send_message(
            chat_id=config.owner_telegram_id,
            text=f"🧭 *Revisão semanal — metas e cronograma:*\n\n{summary}",
            parse_mode="Markdown",
        )

    job_queue = application.job_queue
    job_queue.run_repeating(check_reminders, interval=REMINDER_CHECK_INTERVAL, first=10, name="reminders")

    hour, minute = config.briefing_hour_minute
    job_queue.run_daily(
        daily_briefing,
        time=dt.time(hour=hour, minute=minute, tzinfo=config.tz),
        name="daily_briefing",
    )
    # Revisão estratégica semanal: domingo à noite. No PTB v20+, days 0-6 = domingo-sábado,
    # então domingo = 0.
    job_queue.run_daily(
        weekly_review,
        time=dt.time(hour=19, minute=0, tzinfo=config.tz),
        days=(0,),
        name="weekly_review",
    )
    logger.info(
        "Jobs registrados: lembretes (a cada %ss), briefing diário às %02d:%02d e revisão semanal (dom 19:00)",
        REMINDER_CHECK_INTERVAL, hour, minute,
    )
