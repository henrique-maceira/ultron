"""Definição das ferramentas do assistente e execução dos handlers.

Cada ferramenta é um schema JSON exposto ao modelo + um handler que opera no SQLite
(`db.py`). O modelo decide quando chamar cada uma; o loop em `brain.py` executa os
handlers e devolve os resultados.

Datas (`due_date`, `remind_at`) chegam como ISO 8601 no fuso local, ex.:
"2026-09-22T14:30:00". O modelo recebe a data/hora atual no system prompt e é
responsável por converter expressões relativas ("amanhã 9h", "em 2 horas") no
horário absoluto correto.
"""
from __future__ import annotations

import json
from typing import Any

from . import db

# --------------------------------------------------------------------------- #
# Schemas expostos ao modelo
# --------------------------------------------------------------------------- #
TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "create_task",
        "description": (
            "Cria uma nova tarefa/afazer do usuário. Use quando ele pedir para anotar, "
            "lembrar de fazer algo, ou delegar uma pendência (ex.: 'preciso agendar dentista')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Título curto e claro da tarefa."},
                "notes": {"type": "string", "description": "Detalhes ou passos adicionais."},
                "category": {
                    "type": "string",
                    "description": "Área da vida: trabalho, casa, estudos, saude, mudanca, pessoal.",
                },
                "priority": {
                    "type": "string",
                    "enum": ["alta", "media", "baixa"],
                    "description": "Prioridade da tarefa.",
                },
                "due_date": {
                    "type": "string",
                    "description": "Prazo em ISO 8601 local (YYYY-MM-DDTHH:MM:SS), se houver.",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "list_tasks",
        "description": "Lista as tarefas do usuário, opcionalmente filtrando por status ou categoria.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["pendente", "concluida"],
                    "description": "Filtra por status. Omitir para ver todas.",
                },
                "category": {"type": "string", "description": "Filtra por categoria."},
            },
        },
    },
    {
        "name": "update_task",
        "description": "Atualiza campos de uma tarefa existente (título, notas, categoria, prioridade, status, prazo).",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "ID da tarefa a atualizar."},
                "title": {"type": "string"},
                "notes": {"type": "string"},
                "category": {"type": "string"},
                "priority": {"type": "string", "enum": ["alta", "media", "baixa"]},
                "status": {"type": "string", "enum": ["pendente", "concluida"]},
                "due_date": {"type": "string", "description": "Novo prazo em ISO 8601 local."},
            },
            "required": ["id"],
        },
    },
    {
        "name": "complete_task",
        "description": "Marca uma tarefa como concluída.",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "integer", "description": "ID da tarefa concluída."}},
            "required": ["id"],
        },
    },
    {
        "name": "create_reminder",
        "description": (
            "Agenda um lembrete que será enviado proativamente ao usuário no horário indicado. "
            "Use para compromissos (médico, dentista), prazos, ou 'me lembra de X às Y'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Mensagem do lembrete."},
                "remind_at": {
                    "type": "string",
                    "description": "Quando disparar, em ISO 8601 local (YYYY-MM-DDTHH:MM:SS).",
                },
                "recurrence": {
                    "type": "string",
                    "enum": ["none", "daily", "weekly"],
                    "description": "Repetição do lembrete. 'none' para único.",
                },
            },
            "required": ["text", "remind_at"],
        },
    },
    {
        "name": "list_reminders",
        "description": "Lista os lembretes pendentes (ainda não disparados) do usuário.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "delete_reminder",
        "description": "Remove/cancela um lembrete pelo ID.",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "integer", "description": "ID do lembrete."}},
            "required": ["id"],
        },
    },
    {
        "name": "get_agenda",
        "description": (
            "Retorna um panorama consolidado (tarefas pendentes + lembretes futuros) para ajudar "
            "a priorizar. Use antes de responder perguntas do tipo 'o que devo fazer hoje?'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
]


# --------------------------------------------------------------------------- #
# Execução dos handlers
# --------------------------------------------------------------------------- #
def execute_tool(user_id: int, name: str, tool_input: dict[str, Any]) -> str:
    """Executa a ferramenta e devolve um resultado serializado (JSON) para o modelo."""
    try:
        result = _dispatch(user_id, name, tool_input or {})
    except Exception as exc:  # devolve o erro ao modelo em vez de derrubar o loop
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
    return json.dumps({"ok": True, "result": result}, ensure_ascii=False, default=str)


def _dispatch(user_id: int, name: str, args: dict[str, Any]) -> Any:
    if name == "create_task":
        return db.create_task(
            user_id,
            title=args["title"],
            notes=args.get("notes"),
            category=args.get("category"),
            priority=args.get("priority", "media"),
            due_date=args.get("due_date"),
        )
    if name == "list_tasks":
        return db.list_tasks(user_id, status=args.get("status"), category=args.get("category"))
    if name == "update_task":
        return db.update_task(
            user_id,
            args["id"],
            title=args.get("title"),
            notes=args.get("notes"),
            category=args.get("category"),
            priority=args.get("priority"),
            status=args.get("status"),
            due_date=args.get("due_date"),
        )
    if name == "complete_task":
        return db.complete_task(user_id, args["id"])
    if name == "create_reminder":
        rec = args.get("recurrence")
        if rec == "none":
            rec = None
        return db.create_reminder(user_id, text=args["text"], remind_at=args["remind_at"], recurrence=rec)
    if name == "list_reminders":
        return db.list_reminders(user_id)
    if name == "delete_reminder":
        return {"deleted": db.delete_reminder(user_id, args["id"])}
    if name == "get_agenda":
        return {
            "tarefas_pendentes": db.list_tasks(user_id, status="pendente"),
            "lembretes_futuros": db.list_reminders(user_id),
        }
    raise ValueError(f"Ferramenta desconhecida: {name}")
