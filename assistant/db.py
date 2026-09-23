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

            CREATE TABLE IF NOT EXISTS goals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                title       TEXT    NOT NULL,
                description TEXT,
                category    TEXT,
                target_date TEXT,
                status      TEXT    NOT NULL DEFAULT 'ativo',
                created_at  TEXT    NOT NULL,
                updated_at  TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS steps (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                goal_id      INTEGER NOT NULL,
                user_id      INTEGER NOT NULL,
                title        TEXT    NOT NULL,
                notes        TEXT,
                due_date     TEXT,
                is_milestone INTEGER NOT NULL DEFAULT 0,
                order_index  INTEGER NOT NULL DEFAULT 0,
                status       TEXT    NOT NULL DEFAULT 'pendente',
                created_at   TEXT    NOT NULL,
                updated_at   TEXT    NOT NULL,
                FOREIGN KEY (goal_id) REFERENCES goals(id) ON DELETE CASCADE
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

            CREATE TABLE IF NOT EXISTS expenses (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                amount      REAL    NOT NULL,
                category    TEXT,
                description TEXT,
                spent_on    TEXT    NOT NULL,   -- data local YYYY-MM-DD
                created_at  TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS budgets (
                user_id       INTEGER NOT NULL,
                category      TEXT    NOT NULL,
                monthly_limit REAL    NOT NULL,
                updated_at    TEXT    NOT NULL,
                PRIMARY KEY (user_id, category)
            );
            """
        )
        _ensure_column(conn, "steps", "depends_on", "INTEGER")


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    """Adiciona uma coluna se ainda não existir (migração leve; init_db usa IF NOT EXISTS
    para tabelas, mas colunas novas em tabelas já criadas precisam de ALTER TABLE)."""
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


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
# Metas (goals) e etapas (steps)
# --------------------------------------------------------------------------- #
def create_goal(
    user_id: int,
    title: str,
    description: Optional[str] = None,
    category: Optional[str] = None,
    target_date: Optional[str] = None,
) -> dict[str, Any]:
    now = _utc_now()
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO goals (user_id, title, description, category, target_date,
                                  status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'ativo', ?, ?)""",
            (user_id, title, description, category, target_date, now, now),
        )
        row = conn.execute("SELECT * FROM goals WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)


def get_goal(user_id: int, goal_id: int) -> Optional[dict[str, Any]]:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM goals WHERE id = ? AND user_id = ?", (goal_id, user_id)
        ).fetchone()
        return dict(row) if row else None


def _goal_progress(conn: sqlite3.Connection, goal_id: int) -> dict[str, int]:
    row = conn.execute(
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN status = 'concluida' THEN 1 ELSE 0 END) AS feitas
           FROM steps WHERE goal_id = ?""",
        (goal_id,),
    ).fetchone()
    total = row["total"] or 0
    feitas = row["feitas"] or 0
    pct = round(100 * feitas / total) if total else 0
    return {"etapas_total": total, "etapas_feitas": feitas, "progresso_pct": pct}


def list_goals(user_id: int, status: Optional[str] = "ativo") -> list[dict[str, Any]]:
    """Lista metas (por padrão só as ativas), cada uma com progresso e próxima etapa."""
    query = "SELECT * FROM goals WHERE user_id = ?"
    params: list[Any] = [user_id]
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY (target_date IS NULL), target_date, id"
    with _conn() as conn:
        goals = [dict(r) for r in conn.execute(query, params).fetchall()]
        for g in goals:
            g.update(_goal_progress(conn, g["id"]))
            nxt = conn.execute(
                """SELECT id, title, due_date, is_milestone FROM steps
                   WHERE goal_id = ? AND status = 'pendente'
                   ORDER BY (due_date IS NULL), due_date, order_index, id LIMIT 1""",
                (g["id"],),
            ).fetchone()
            g["proxima_etapa"] = dict(nxt) if nxt else None
        return goals


def update_goal(user_id: int, goal_id: int, **fields: Any) -> Optional[dict[str, Any]]:
    allowed = {"title", "description", "category", "target_date", "status"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return get_goal(user_id, goal_id)
    updates["updated_at"] = _utc_now()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    params = list(updates.values()) + [goal_id, user_id]
    with _conn() as conn:
        conn.execute(f"UPDATE goals SET {set_clause} WHERE id = ? AND user_id = ?", params)
    return get_goal(user_id, goal_id)


def delete_goal(user_id: int, goal_id: int) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "DELETE FROM goals WHERE id = ? AND user_id = ?", (goal_id, user_id)
        )
        return cur.rowcount > 0


def create_step(
    user_id: int,
    goal_id: int,
    title: str,
    notes: Optional[str] = None,
    due_date: Optional[str] = None,
    is_milestone: bool = False,
    order_index: Optional[int] = None,
    depends_on: Optional[int] = None,
) -> dict[str, Any]:
    now = _utc_now()
    with _conn() as conn:
        # Valida que a meta pertence ao usuário.
        owner = conn.execute(
            "SELECT 1 FROM goals WHERE id = ? AND user_id = ?", (goal_id, user_id)
        ).fetchone()
        if not owner:
            raise ValueError(f"Meta {goal_id} não encontrada para o usuário.")
        if depends_on is not None:
            dep = conn.execute(
                "SELECT 1 FROM steps WHERE id = ? AND user_id = ?", (depends_on, user_id)
            ).fetchone()
            if not dep:
                raise ValueError(f"Etapa dependência {depends_on} não encontrada.")
        if order_index is None:
            row = conn.execute(
                "SELECT COALESCE(MAX(order_index), 0) + 1 AS nxt FROM steps WHERE goal_id = ?",
                (goal_id,),
            ).fetchone()
            order_index = row["nxt"]
        cur = conn.execute(
            """INSERT INTO steps (goal_id, user_id, title, notes, due_date, is_milestone,
                                  order_index, status, depends_on, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pendente', ?, ?, ?)""",
            (goal_id, user_id, title, notes, due_date, int(bool(is_milestone)),
             order_index, depends_on, now, now),
        )
        row = conn.execute("SELECT * FROM steps WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)


def list_steps(
    user_id: int,
    goal_id: Optional[int] = None,
    status: Optional[str] = None,
) -> list[dict[str, Any]]:
    query = "SELECT * FROM steps WHERE user_id = ?"
    params: list[Any] = [user_id]
    if goal_id is not None:
        query += " AND goal_id = ?"
        params.append(goal_id)
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY (due_date IS NULL), due_date, order_index, id"
    with _conn() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]


def get_step(user_id: int, step_id: int) -> Optional[dict[str, Any]]:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM steps WHERE id = ? AND user_id = ?", (step_id, user_id)
        ).fetchone()
        return dict(row) if row else None


def update_step(user_id: int, step_id: int, **fields: Any) -> Optional[dict[str, Any]]:
    allowed = {"title", "notes", "due_date", "is_milestone", "order_index", "status", "depends_on"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if "is_milestone" in updates:
        updates["is_milestone"] = int(bool(updates["is_milestone"]))
    if not updates:
        return get_step(user_id, step_id)
    updates["updated_at"] = _utc_now()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    params = list(updates.values()) + [step_id, user_id]
    with _conn() as conn:
        conn.execute(f"UPDATE steps SET {set_clause} WHERE id = ? AND user_id = ?", params)
    return get_step(user_id, step_id)


def complete_step(user_id: int, step_id: int) -> Optional[dict[str, Any]]:
    return update_step(user_id, step_id, status="concluida")


def get_goal_plan(user_id: int, goal_id: int) -> Optional[dict[str, Any]]:
    """Meta + suas etapas + progresso, para revisar/atualizar o cronograma.

    Cada etapa recebe `bloqueada` = True quando depende de outra ainda não concluída.
    """
    goal = get_goal(user_id, goal_id)
    if not goal:
        return None
    with _conn() as conn:
        goal.update(_goal_progress(conn, goal_id))
    etapas = list_steps(user_id, goal_id=goal_id)
    por_id = {s["id"]: s for s in etapas}
    for s in etapas:
        dep = s.get("depends_on")
        s["bloqueada"] = bool(dep and por_id.get(dep) and por_id[dep]["status"] != "concluida")
    goal["etapas"] = etapas
    return goal


def upcoming_steps(user_id: int, until_iso: str) -> list[dict[str, Any]]:
    """Etapas pendentes com prazo até `until_iso` (ISO local), para briefings."""
    with _conn() as conn:
        rows = conn.execute(
            """SELECT s.*, g.title AS goal_title FROM steps s
               JOIN goals g ON g.id = s.goal_id
               WHERE s.user_id = ? AND s.status = 'pendente'
                     AND s.due_date IS NOT NULL AND s.due_date <= ?
                     AND g.status = 'ativo'
               ORDER BY s.due_date, s.order_index""",
            (user_id, until_iso),
        ).fetchall()
        return [dict(r) for r in rows]


def goals_health(
    user_id: int,
    now_local_iso: str,
    now_utc_iso: str,
    stalled_days: int = 5,
) -> list[dict[str, Any]]:
    """Diagnóstico por meta ativa: etapas atrasadas, meta parada, dias até o prazo.

    Sinais crus para o modelo interpretar e PROPOR replanejamento (nunca alterar sem
    confirmação). `now_local_iso` é o agora no fuso local (compara com due_date/target_date,
    que são locais); `now_utc_iso` compara com updated_at (UTC).
    """
    from datetime import datetime, timedelta

    cutoff_utc = (datetime.fromisoformat(now_utc_iso) - timedelta(days=stalled_days)).isoformat()
    try:
        today = datetime.fromisoformat(now_local_iso).date()
    except ValueError:
        today = datetime.now().date()

    out: list[dict[str, Any]] = []
    for g in list_goals(user_id, status="ativo"):
        steps = list_steps(user_id, goal_id=g["id"])
        pend = [s for s in steps if s["status"] == "pendente"]
        done = [s for s in steps if s["status"] == "concluida"]
        atrasadas = [
            {"id": s["id"], "title": s["title"], "due_date": s["due_date"], "is_milestone": s["is_milestone"]}
            for s in pend
            if s["due_date"] and s["due_date"] < now_local_iso
        ]
        ultimo_progresso = max((s["updated_at"] for s in done), default=g["created_at"])
        parada = bool(pend) and ultimo_progresso < cutoff_utc

        dias_ate_prazo = None
        if g["target_date"]:
            try:
                dias_ate_prazo = (datetime.fromisoformat(g["target_date"]).date() - today).days
            except ValueError:
                dias_ate_prazo = None

        out.append(
            {
                "id": g["id"],
                "title": g["title"],
                "category": g["category"],
                "target_date": g["target_date"],
                "dias_ate_prazo": dias_ate_prazo,
                "progresso_pct": g["progresso_pct"],
                "etapas_pendentes": len(pend),
                "etapas_atrasadas": atrasadas,
                "parada": parada,
                "ultimo_progresso": ultimo_progresso,
                "proxima_etapa": g["proxima_etapa"],
            }
        )
    return out


# --------------------------------------------------------------------------- #
# Finanças: gastos (expenses) e orçamento (budgets)
# --------------------------------------------------------------------------- #
def add_expense(
    user_id: int,
    amount: float,
    category: Optional[str] = None,
    description: Optional[str] = None,
    spent_on: Optional[str] = None,
) -> dict[str, Any]:
    """Registra um gasto. `spent_on` é a data local (YYYY-MM-DD); sem ela, usa hoje UTC."""
    now = _utc_now()
    if not spent_on:
        spent_on = now[:10]
    spent_on = spent_on[:10]  # normaliza para só a data
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO expenses (user_id, amount, category, description, spent_on, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, float(amount), category, description, spent_on, now),
        )
        row = conn.execute("SELECT * FROM expenses WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)


def list_expenses(
    user_id: int,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> list[dict[str, Any]]:
    query = "SELECT * FROM expenses WHERE user_id = ?"
    params: list[Any] = [user_id]
    if since:
        query += " AND spent_on >= ?"
        params.append(since[:10])
    if until:
        query += " AND spent_on <= ?"
        params.append(until[:10])
    query += " ORDER BY spent_on DESC, id DESC"
    with _conn() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]


def delete_expense(user_id: int, expense_id: int) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "DELETE FROM expenses WHERE id = ? AND user_id = ?", (expense_id, user_id)
        )
        return cur.rowcount > 0


def set_budget(user_id: int, category: str, monthly_limit: float) -> dict[str, Any]:
    """Define/atualiza o teto mensal de gastos de uma categoria."""
    now = _utc_now()
    with _conn() as conn:
        conn.execute(
            """INSERT INTO budgets (user_id, category, monthly_limit, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id, category)
               DO UPDATE SET monthly_limit = excluded.monthly_limit, updated_at = excluded.updated_at""",
            (user_id, category, float(monthly_limit), now),
        )
        row = conn.execute(
            "SELECT * FROM budgets WHERE user_id = ? AND category = ?", (user_id, category)
        ).fetchone()
        return dict(row)


def list_budgets(user_id: int) -> list[dict[str, Any]]:
    with _conn() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM budgets WHERE user_id = ? ORDER BY category", (user_id,)
            ).fetchall()
        ]


def budget_status(user_id: int, year_month: str) -> dict[str, Any]:
    """Compara gastos do mês (YYYY-MM) com o orçamento por categoria.

    Retorna: total gasto, por categoria (gasto/limite/restante/pct) e o dia do mês.
    """
    ym = year_month[:7]
    with _conn() as conn:
        gastos = conn.execute(
            """SELECT COALESCE(category, 'sem_categoria') AS category, SUM(amount) AS gasto
               FROM expenses WHERE user_id = ? AND substr(spent_on, 1, 7) = ?
               GROUP BY COALESCE(category, 'sem_categoria')""",
            (user_id, ym),
        ).fetchall()
        limites = {b["category"]: b["monthly_limit"] for b in list_budgets(user_id)}

    gasto_por_cat = {g["category"]: g["gasto"] for g in gastos}
    categorias = sorted(set(gasto_por_cat) | set(limites))
    linhas = []
    total_gasto = 0.0
    total_limite = 0.0
    for cat in categorias:
        gasto = round(gasto_por_cat.get(cat, 0.0), 2)
        limite = limites.get(cat)
        total_gasto += gasto
        item: dict[str, Any] = {"categoria": cat, "gasto": gasto, "limite": limite}
        if limite:
            total_limite += limite
            item["restante"] = round(limite - gasto, 2)
            item["pct"] = round(100 * gasto / limite) if limite else None
            item["estourou"] = gasto > limite
        linhas.append(item)

    return {
        "mes": ym,
        "total_gasto": round(total_gasto, 2),
        "total_orcamento": round(total_limite, 2) if total_limite else None,
        "por_categoria": linhas,
    }


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
