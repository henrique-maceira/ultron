"""Camada de persistência em SQLite.

Guarda tarefas, lembretes, histórico de conversa e configurações. As funções aqui
são chamadas tanto pelas ferramentas do assistente (`tools.py`) quanto pelos jobs
proativos (`bot/jobs.py`).

Convenção de datas: campos de data/hora (`due_date`, `remind_at`) são armazenados
como strings ISO 8601 no fuso local configurado, ex.: "2026-09-22T14:30:00".
Timestamps internos (`created_at`, `updated_at`) usam UTC ISO.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

# Quantas mensagens recentes de conversa manter como contexto para a IA.
MAX_HISTORY_MESSAGES = 30

_DB_PATH: str = "ultron.db"


def configure(db_path: str) -> None:
    """Define o caminho do arquivo do banco e garante o schema."""
    global _DB_PATH
    _DB_PATH = db_path
    init_db()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db() -> None:
    with _conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                title      TEXT    NOT NULL,
                notes      TEXT,
                category   TEXT,
                priority   TEXT    NOT NULL DEFAULT 'media',
                status     TEXT    NOT NULL DEFAULT 'pendente',
                due_date   TEXT,
                created_at TEXT    NOT NULL,
                updated_at TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reminders (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                text       TEXT    NOT NULL,
                remind_at  TEXT    NOT NULL,
                recurrence TEXT,
                sent       INTEGER NOT NULL DEFAULT 0,
                created_at TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                role       TEXT    NOT NULL,
                content    TEXT    NOT NULL,
                created_at TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                user_id      INTEGER PRIMARY KEY,
                timezone     TEXT,
                briefing_time TEXT
            );
            """
        )


# --------------------------------------------------------------------------- #
# Tarefas
# --------------------------------------------------------------------------- #
def create_task(
    user_id: int,
    title: str,
    notes: Optional[str] = None,
    category: Optional[str] = None,
    priority: str = "media",
    due_date: Optional[str] = None,
) -> dict[str, Any]:
    now = _utc_now()
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO tasks (user_id, title, notes, category, priority, status,
                                  due_date, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'pendente', ?, ?, ?)""",
            (user_id, title, notes, category, priority, due_date, now, now),
        )
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)


def get_task(user_id: int, task_id: int) -> Optional[dict[str, Any]]:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM tasks WHERE id = ? AND user_id = ?", (task_id, user_id)
        ).fetchone()
        return dict(row) if row else None


def list_tasks(
    user_id: int,
    status: Optional[str] = None,
    category: Optional[str] = None,
) -> list[dict[str, Any]]:
    query = "SELECT * FROM tasks WHERE user_id = ?"
    params: list[Any] = [user_id]
    if status:
        query += " AND status = ?"
        params.append(status)
    if category:
        query += " AND category = ?"
        params.append(category)
    # Ordena: pendentes primeiro, depois por prioridade e prazo.
    query += (
        " ORDER BY CASE status WHEN 'pendente' THEN 0 ELSE 1 END,"
        " CASE priority WHEN 'alta' THEN 0 WHEN 'media' THEN 1 ELSE 2 END,"
        " (due_date IS NULL), due_date"
    )
    with _conn() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]


def update_task(user_id: int, task_id: int, **fields: Any) -> Optional[dict[str, Any]]:
    allowed = {"title", "notes", "category", "priority", "status", "due_date"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return get_task(user_id, task_id)
    updates["updated_at"] = _utc_now()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    params = list(updates.values()) + [task_id, user_id]
    with _conn() as conn:
        conn.execute(
            f"UPDATE tasks SET {set_clause} WHERE id = ? AND user_id = ?", params
        )
    return get_task(user_id, task_id)


def complete_task(user_id: int, task_id: int) -> Optional[dict[str, Any]]:
    return update_task(user_id, task_id, status="concluida")


# --------------------------------------------------------------------------- #
# Lembretes
# --------------------------------------------------------------------------- #
def create_reminder(
    user_id: int,
    text: str,
    remind_at: str,
    recurrence: Optional[str] = None,
) -> dict[str, Any]:
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO reminders (user_id, text, remind_at, recurrence, sent, created_at)
               VALUES (?, ?, ?, ?, 0, ?)""",
            (user_id, text, remind_at, recurrence, _utc_now()),
        )
        row = conn.execute(
            "SELECT * FROM reminders WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return dict(row)


def list_reminders(user_id: int, include_sent: bool = False) -> list[dict[str, Any]]:
    query = "SELECT * FROM reminders WHERE user_id = ?"
    if not include_sent:
        query += " AND sent = 0"
    query += " ORDER BY remind_at"
    with _conn() as conn:
        return [dict(r) for r in conn.execute(query, (user_id,)).fetchall()]


def delete_reminder(user_id: int, reminder_id: int) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "DELETE FROM reminders WHERE id = ? AND user_id = ?", (reminder_id, user_id)
        )
        return cur.rowcount > 0


def due_reminders(now_iso: str) -> list[dict[str, Any]]:
    """Lembretes ainda não enviados cujo horário já chegou (comparação ISO lexical
    válida porque as strings estão no mesmo fuso e formato)."""
    with _conn() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM reminders WHERE sent = 0 AND remind_at <= ? ORDER BY remind_at",
                (now_iso,),
            ).fetchall()
        ]


def mark_reminder_sent(reminder_id: int) -> None:
    with _conn() as conn:
        conn.execute("UPDATE reminders SET sent = 1 WHERE id = ?", (reminder_id,))


def reschedule_reminder(reminder_id: int, next_remind_at: str) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE reminders SET remind_at = ?, sent = 0 WHERE id = ?",
            (next_remind_at, reminder_id),
        )


# --------------------------------------------------------------------------- #
# Histórico de conversa
# --------------------------------------------------------------------------- #
def add_message(user_id: int, role: str, content: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO messages (user_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (user_id, role, content, _utc_now()),
        )


def get_recent_messages(user_id: int, limit: int = MAX_HISTORY_MESSAGES) -> list[dict[str, str]]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    # Retorna em ordem cronológica (mais antigo primeiro).
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
