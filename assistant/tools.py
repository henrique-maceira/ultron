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
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from . import db, gcal

# Fuso usado para calcular "agora" nos handlers (datas de negócio são locais). É
# configurado em main.py via configure(); o padrão evita quebrar testes offline.
_TZ = ZoneInfo("America/Sao_Paulo")


def configure(tz: ZoneInfo) -> None:
    global _TZ
    _TZ = tz


def _now_local_iso() -> str:
    return datetime.now(_TZ).strftime("%Y-%m-%dT%H:%M:%S")

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
    # ----------------------------- Metas e etapas --------------------------- #
    {
        "name": "create_goal",
        "description": (
            "Cria uma META/objetivo maior do usuário (ex.: 'tirar certificação AWS', 'organizar a "
            "mudança'). Use quando ele definir um objetivo de médio/longo prazo. Depois de criar, "
            "decomponha em etapas com add_step, trabalhando de trás pra frente a partir do prazo."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Nome curto e claro da meta."},
                "description": {"type": "string", "description": "Contexto, critério de sucesso, o que 'pronto' significa."},
                "category": {"type": "string", "description": "Área: trabalho, financas, estudos, casa, mudanca, projeto, pessoal."},
                "target_date": {"type": "string", "description": "Prazo-alvo em ISO 8601 local, se houver."},
            },
            "required": ["title"],
        },
    },
    {
        "name": "list_goals",
        "description": (
            "Lista as metas do usuário com progresso (% e etapas feitas/total) e a próxima etapa de "
            "cada uma. Use para ter a visão estratégica antes de planejar ou priorizar."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["ativo", "concluido", "pausado"],
                    "description": "Filtra por status. Padrão: só ativas. Passe null explicitamente p/ todas.",
                },
            },
        },
    },
    {
        "name": "update_goal",
        "description": "Atualiza uma meta (título, descrição, categoria, prazo, status). Use para concluir/pausar/replanejar.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "ID da meta."},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "category": {"type": "string"},
                "target_date": {"type": "string", "description": "Novo prazo em ISO 8601 local."},
                "status": {"type": "string", "enum": ["ativo", "concluido", "pausado"]},
            },
            "required": ["id"],
        },
    },
    {
        "name": "add_step",
        "description": (
            "Adiciona uma ETAPA a uma meta (um passo do cronograma). Defina prazos concretos e marque "
            "como marco (is_milestone) os pontos de verificação importantes. Distribua as etapas ao "
            "longo do tempo até o prazo da meta."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "goal_id": {"type": "integer", "description": "ID da meta a que a etapa pertence."},
                "title": {"type": "string", "description": "O que fazer nesta etapa."},
                "notes": {"type": "string", "description": "Detalhes/como fazer."},
                "due_date": {"type": "string", "description": "Prazo da etapa em ISO 8601 local."},
                "is_milestone": {"type": "boolean", "description": "True se for um marco/checkpoint."},
                "order_index": {"type": "integer", "description": "Ordem manual (opcional; senão vai pro fim)."},
                "depends_on": {"type": "integer", "description": "ID de outra etapa que precisa terminar antes desta (dependência)."},
            },
            "required": ["goal_id", "title"],
        },
    },
    {
        "name": "list_steps",
        "description": "Lista etapas, opcionalmente de uma meta específica e/ou por status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "goal_id": {"type": "integer", "description": "Filtra por meta. Omitir para todas."},
                "status": {"type": "string", "enum": ["pendente", "concluida"]},
            },
        },
    },
    {
        "name": "update_step",
        "description": "Atualiza uma etapa (título, notas, prazo, marco, ordem, status). Use para remarcar ou reordenar.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "ID da etapa."},
                "title": {"type": "string"},
                "notes": {"type": "string"},
                "due_date": {"type": "string", "description": "Novo prazo em ISO 8601 local."},
                "is_milestone": {"type": "boolean"},
                "order_index": {"type": "integer"},
                "status": {"type": "string", "enum": ["pendente", "concluida"]},
            },
            "required": ["id"],
        },
    },
    {
        "name": "complete_step",
        "description": "Marca uma etapa como concluída (avança o progresso da meta).",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "integer", "description": "ID da etapa concluída."}},
            "required": ["id"],
        },
    },
    {
        "name": "get_goal_plan",
        "description": "Retorna uma meta com TODAS as suas etapas e o progresso. Use para revisar/ajustar o cronograma de uma meta.",
        "input_schema": {
            "type": "object",
            "properties": {"goal_id": {"type": "integer", "description": "ID da meta."}},
            "required": ["goal_id"],
        },
    },
    {
        "name": "get_goal_health",
        "description": (
            "Diagnóstico das metas ativas: etapas ATRASADAS, metas PARADAS (sem progresso há dias) "
            "e dias até o prazo. Use para acompanhar e detectar o que precisa de replanejamento. "
            "Com base nisso, PROPONHA ajustes ao usuário e só altere (update_step/update_goal) "
            "depois que ele confirmar."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    # ----------------------------- Finanças --------------------------------- #
    {
        "name": "log_expense",
        "description": (
            "Registra um gasto do usuário (parte da organização financeira). Use quando ele informar "
            "um gasto por texto ('gastei 80 no mercado') ou ao ler um comprovante/boleto. Uma mensagem "
            "com vários gastos vira várias chamadas."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "amount": {"type": "number", "description": "Valor em reais (número, ex.: 80.50)."},
                "category": {"type": "string", "description": "Categoria: mercado, transporte, lazer, moradia, saude, contas, outros."},
                "description": {"type": "string", "description": "Descrição curta do gasto."},
                "spent_on": {"type": "string", "description": "Data do gasto (YYYY-MM-DD). Omitir = hoje."},
            },
            "required": ["amount"],
        },
    },
    {
        "name": "get_expense_summary",
        "description": (
            "Resumo financeiro do mês: total gasto e, por categoria, gasto vs. orçamento (restante e %). "
            "Use para validar se o usuário está no caminho e no briefing financeiro."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "month": {"type": "string", "description": "Mês YYYY-MM. Omitir = mês atual."},
            },
        },
    },
    {
        "name": "list_expenses",
        "description": (
            "Lista os gastos individuais num período (para revisar, achar os maiores, montar relatório "
            "ou corrigir/apagar um lançamento). Sem período, usa os últimos 30 dias."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "since": {"type": "string", "description": "Data inicial YYYY-MM-DD (inclusive)."},
                "until": {"type": "string", "description": "Data final YYYY-MM-DD (inclusive)."},
            },
        },
    },
    {
        "name": "delete_expense",
        "description": "Remove um gasto lançado por engano, pelo ID.",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "integer", "description": "ID do gasto."}},
            "required": ["id"],
        },
    },
    {
        "name": "set_budget",
        "description": "Define/atualiza o teto mensal de gastos de uma categoria (ex.: mercado = 1200). É o 'contra o quê' validar os gastos.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Categoria do orçamento."},
                "monthly_limit": {"type": "number", "description": "Teto mensal em reais."},
            },
            "required": ["category", "monthly_limit"],
        },
    },
    {
        "name": "list_budgets",
        "description": "Lista os orçamentos (tetos mensais) definidos por categoria.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_agenda",
        "description": (
            "Retorna um panorama estratégico consolidado: metas ativas (com progresso e próxima etapa), "
            "etapas com prazo próximo, tarefas pendentes, lembretes futuros e compromissos da agenda "
            "quando disponível. Use antes de responder 'o que faço hoje?' ou ao montar o resumo."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
]


# Ferramentas do Google Agenda. Só são expostas ao modelo quando a integração está
# habilitada (ver `all_tool_defs`). Datas em ISO 8601 local (YYYY-MM-DDTHH:MM:SS).
CALENDAR_TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "list_calendar_events",
        "description": (
            "Lista os compromissos da agenda (Google Agenda) num intervalo. Use para saber o que já "
            "está marcado, checar disponibilidade e montar planos de ação em cima dos horários reais. "
            "Sem intervalo, retorna de agora até 7 dias à frente."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "time_min": {"type": "string", "description": "Início do intervalo em ISO 8601 local."},
                "time_max": {"type": "string", "description": "Fim do intervalo em ISO 8601 local."},
            },
        },
    },
    {
        "name": "create_calendar_event",
        "description": (
            "Cria um compromisso na Google Agenda (reunião, consulta, bloco de foco). Use quando o "
            "usuário quiser marcar algo com data e hora, ou ao propor um plano com horários concretos."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Título do compromisso."},
                "start": {"type": "string", "description": "Início em ISO 8601 local (YYYY-MM-DDTHH:MM:SS)."},
                "end": {"type": "string", "description": "Fim em ISO 8601 local. Se omitido, dura 1 hora."},
                "description": {"type": "string", "description": "Detalhes/anotações do evento."},
                "location": {"type": "string", "description": "Local do compromisso."},
                "all_day": {"type": "boolean", "description": "True para evento de dia inteiro."},
            },
            "required": ["summary", "start"],
        },
    },
    {
        "name": "update_calendar_event",
        "description": "Atualiza um compromisso existente da agenda (remarcar horário, mudar título/local/detalhes).",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "ID do evento (obtido em list_calendar_events)."},
                "summary": {"type": "string"},
                "start": {"type": "string", "description": "Novo início em ISO 8601 local."},
                "end": {"type": "string", "description": "Novo fim em ISO 8601 local."},
                "description": {"type": "string"},
                "location": {"type": "string"},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "delete_calendar_event",
        "description": "Cancela/remove um compromisso da agenda pelo ID.",
        "input_schema": {
            "type": "object",
            "properties": {"event_id": {"type": "string", "description": "ID do evento."}},
            "required": ["event_id"],
        },
    },
    {
        "name": "find_free_slots",
        "description": (
            "Encontra janelas LIVRES na agenda para blocos de foco/estudo/execução, dentro do horário "
            "de trabalho. Use antes de propor horários para não sugerir algo em cima do que já existe. "
            "Depois de o usuário escolher, crie o compromisso com create_calendar_event."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "duration_minutes": {"type": "integer", "description": "Duração do bloco desejado (min). Padrão 60."},
                "days_ahead": {"type": "integer", "description": "Quantos dias à frente procurar. Padrão 7."},
            },
        },
    },
]


def all_tool_defs(include_calendar: bool) -> list[dict[str, Any]]:
    """Conjunto de ferramentas expostas ao modelo, com ou sem as da agenda."""
    if include_calendar:
        return [*TOOL_DEFS, *CALENDAR_TOOL_DEFS]
    return list(TOOL_DEFS)


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
    # --- Metas e etapas ---
    if name == "create_goal":
        return db.create_goal(
            user_id,
            title=args["title"],
            description=args.get("description"),
            category=args.get("category"),
            target_date=args.get("target_date"),
        )
    if name == "list_goals":
        # Se a chave 'status' vier explicitamente como null, lista todas.
        status = args.get("status", "ativo") if "status" in args else "ativo"
        return db.list_goals(user_id, status=status)
    if name == "update_goal":
        return db.update_goal(
            user_id,
            args["id"],
            title=args.get("title"),
            description=args.get("description"),
            category=args.get("category"),
            target_date=args.get("target_date"),
            status=args.get("status"),
        )
    if name == "add_step":
        return db.create_step(
            user_id,
            goal_id=args["goal_id"],
            title=args["title"],
            notes=args.get("notes"),
            due_date=args.get("due_date"),
            is_milestone=bool(args.get("is_milestone", False)),
            order_index=args.get("order_index"),
            depends_on=args.get("depends_on"),
        )
    if name == "list_steps":
        return db.list_steps(user_id, goal_id=args.get("goal_id"), status=args.get("status"))
    if name == "update_step":
        return db.update_step(
            user_id,
            args["id"],
            title=args.get("title"),
            notes=args.get("notes"),
            due_date=args.get("due_date"),
            is_milestone=args.get("is_milestone"),
            order_index=args.get("order_index"),
            status=args.get("status"),
        )
    if name == "complete_step":
        return db.complete_step(user_id, args["id"])
    if name == "get_goal_plan":
        return db.get_goal_plan(user_id, args["goal_id"])
    if name == "get_goal_health":
        return db.goals_health(
            user_id,
            now_local_iso=_now_local_iso(),
            now_utc_iso=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    # --- Finanças ---
    if name == "log_expense":
        return db.add_expense(
            user_id,
            amount=args["amount"],
            category=args.get("category"),
            description=args.get("description"),
            spent_on=args.get("spent_on"),
        )
    if name == "get_expense_summary":
        month = args.get("month") or _now_local_iso()[:7]
        return db.budget_status(user_id, month)
    if name == "list_expenses":
        since = args.get("since")
        until = args.get("until")
        if not since and not until:
            since = (datetime.now(_TZ) - timedelta(days=30)).strftime("%Y-%m-%d")
        return db.list_expenses(user_id, since=since, until=until)
    if name == "delete_expense":
        return {"deleted": db.delete_expense(user_id, args["id"])}
    if name == "set_budget":
        return db.set_budget(user_id, category=args["category"], monthly_limit=args["monthly_limit"])
    if name == "list_budgets":
        return db.list_budgets(user_id)

    if name == "get_agenda":
        until = (datetime.now(_TZ) + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
        agenda: dict[str, Any] = {
            "metas_ativas": db.list_goals(user_id, status="ativo"),
            "etapas_proximos_7_dias": db.upcoming_steps(user_id, until),
            "tarefas_pendentes": db.list_tasks(user_id, status="pendente"),
            "lembretes_futuros": db.list_reminders(user_id),
        }
        # Inclui compromissos da agenda se a integração estiver disponível.
        if gcal.has_token():
            try:
                agenda["compromissos_agenda"] = gcal.list_events()
            except Exception as exc:  # não deixa a agenda derrubar o panorama
                agenda["compromissos_agenda_erro"] = str(exc)
        return agenda

    # --- Google Agenda ---
    if name == "list_calendar_events":
        return gcal.list_events(time_min=args.get("time_min"), time_max=args.get("time_max"))
    if name == "create_calendar_event":
        return gcal.create_event(
            summary=args["summary"],
            start=args["start"],
            end=args.get("end"),
            description=args.get("description"),
            location=args.get("location"),
            all_day=bool(args.get("all_day", False)),
        )
    if name == "update_calendar_event":
        return gcal.update_event(
            args["event_id"],
            summary=args.get("summary"),
            start=args.get("start"),
            end=args.get("end"),
            description=args.get("description"),
            location=args.get("location"),
        )
    if name == "delete_calendar_event":
        return {"deleted": gcal.delete_event(args["event_id"])}
    if name == "find_free_slots":
        return gcal.find_free_slots(
            days_ahead=int(args.get("days_ahead", 7)),
            duration_minutes=int(args.get("duration_minutes", 60)),
        )

    raise ValueError(f"Ferramenta desconhecida: {name}")
