"""Teste rápido do banco de dados, sem rede nem API.

Exercita o schema e as operações de tarefas e lembretes num banco temporário.
Uso:  python scripts/check_db.py
"""
from __future__ import annotations

import os
import sys
import tempfile

# Permite importar o pacote quando executado da raiz do repo.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from assistant import db  # noqa: E402

USER = 42


def main() -> None:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db.configure(tmp.name)

    # --- Tarefas ---
    t = db.create_task(USER, "Agendar dentista", category="saude", priority="alta")
    assert t["id"] and t["status"] == "pendente", t
    db.create_task(USER, "Montar caixas", category="mudanca", priority="media")

    pendentes = db.list_tasks(USER, status="pendente")
    assert len(pendentes) == 2, pendentes

    db.complete_task(USER, t["id"])
    assert db.get_task(USER, t["id"])["status"] == "concluida"
    assert len(db.list_tasks(USER, status="pendente")) == 1

    updated = db.update_task(USER, t["id"], notes="ligar de manhã")
    assert updated["notes"] == "ligar de manhã"

    # --- Lembretes ---
    r = db.create_reminder(USER, "Consulta médica", "2020-01-01T09:00:00")
    assert r["id"] and r["sent"] == 0
    assert len(db.list_reminders(USER)) == 1

    vencidos = db.due_reminders("2025-01-01T00:00:00")
    assert any(x["id"] == r["id"] for x in vencidos), vencidos

    db.mark_reminder_sent(r["id"])
    assert db.due_reminders("2025-01-01T00:00:00") == []
    assert db.list_reminders(USER) == []  # não inclui enviados

    # --- Metas e etapas ---
    g = db.create_goal(USER, "Tirar certificação AWS", category="estudos", target_date="2026-12-01T00:00:00")
    assert g["id"] and g["status"] == "ativo", g
    s1 = db.create_step(USER, g["id"], "Escolher a certificação e a data", due_date="2026-10-01T00:00:00", is_milestone=True)
    s2 = db.create_step(USER, g["id"], "Fazer 10 simulados", due_date="2026-11-20T00:00:00")
    assert s1["order_index"] < s2["order_index"], (s1, s2)

    metas = db.list_goals(USER)
    assert len(metas) == 1 and metas[0]["etapas_total"] == 2 and metas[0]["etapas_feitas"] == 0
    assert metas[0]["proxima_etapa"]["id"] == s1["id"], metas[0]["proxima_etapa"]

    db.complete_step(USER, s1["id"])
    plano = db.get_goal_plan(USER, g["id"])
    assert plano["progresso_pct"] == 50 and plano["etapas_feitas"] == 1, plano
    assert db.list_goals(USER)[0]["proxima_etapa"]["id"] == s2["id"]

    prox = db.upcoming_steps(USER, "2026-10-15T00:00:00")
    assert prox == [], prox  # s1 concluída; s2 vence em 20/11 (fora da janela)
    prox2 = db.upcoming_steps(USER, "2026-12-31T00:00:00")
    assert any(x["id"] == s2["id"] and x["goal_title"] == g["title"] for x in prox2), prox2

    # --- Saúde das metas (Fase 2): etapa atrasada + meta parada ---
    # Cenário: s1 concluída; s2 pendente (venceu 20/11); prazo da meta 01/12.
    saude = db.goals_health(
        USER,
        now_local_iso="2026-11-25T12:00:00",       # depois do prazo de s2 (20/11)
        now_utc_iso="2026-11-25T15:00:00+00:00",   # bem depois do último progresso real
        stalled_days=5,
    )
    h = next(x for x in saude if x["id"] == g["id"])
    assert h["dias_ate_prazo"] == 6, h["dias_ate_prazo"]  # 01/12 - 25/11
    assert [a["id"] for a in h["etapas_atrasadas"]] == [s2["id"]], h["etapas_atrasadas"]
    assert h["parada"] is True, h  # sem progresso recente, ainda há etapa pendente

    # --- Dependências entre etapas (Fase 3) ---
    g2 = db.create_goal(USER, "Projeto X", category="projeto")
    base = db.create_step(USER, g2["id"], "Fazer A")
    dependente = db.create_step(USER, g2["id"], "Fazer B (depende de A)", depends_on=base["id"])
    plano = db.get_goal_plan(USER, g2["id"])
    by_id = {s["id"]: s for s in plano["etapas"]}
    assert by_id[dependente["id"]]["bloqueada"] is True, plano["etapas"]
    db.complete_step(USER, base["id"])
    plano = db.get_goal_plan(USER, g2["id"])
    by_id = {s["id"]: s for s in plano["etapas"]}
    assert by_id[dependente["id"]]["bloqueada"] is False  # dependência concluída, liberou

    db.update_goal(USER, g["id"], status="concluido")
    db.update_goal(USER, g2["id"], status="concluido")
    assert db.list_goals(USER) == []  # só ativas por padrão
    assert len(db.list_goals(USER, status=None)) == 2  # todas
    assert db.goals_health(USER, "2026-11-25T12:00:00", "2026-11-25T15:00:00+00:00") == []

    # --- Finanças: gastos e orçamento (Fase 3) ---
    db.set_budget(USER, "mercado", 1000.0)
    db.add_expense(USER, 250.0, category="mercado", description="compra do mês", spent_on="2026-09-10")
    db.add_expense(USER, 800.0, category="mercado", spent_on="2026-09-20")
    db.add_expense(USER, 50.0, category="transporte", spent_on="2026-09-20")
    status = db.budget_status(USER, "2026-09")
    assert status["total_gasto"] == 1100.0, status
    merc = next(c for c in status["por_categoria"] if c["categoria"] == "mercado")
    assert merc["gasto"] == 1050.0 and merc["limite"] == 1000.0 and merc["estourou"] is True, merc
    assert merc["restante"] == -50.0 and merc["pct"] == 105, merc
    trans = next(c for c in status["por_categoria"] if c["categoria"] == "transporte")
    assert trans["gasto"] == 50.0 and trans["limite"] is None, trans
    assert db.budget_status(USER, "2026-10")["total_gasto"] == 0.0  # outro mês

    lst = db.list_expenses(USER, since="2026-09-01", until="2026-09-30")
    assert len(lst) == 3, lst
    assert db.delete_expense(USER, lst[0]["id"]) is True
    assert len(db.list_expenses(USER, since="2026-09-01", until="2026-09-30")) == 2

    # --- Histórico ---
    db.add_message(USER, "user", "oi")
    db.add_message(USER, "assistant", "olá!")
    hist = db.get_recent_messages(USER)
    assert [m["role"] for m in hist] == ["user", "assistant"], hist

    try:
        os.unlink(tmp.name)
    except OSError:
        # No Windows o arquivo pode ficar travado por conexões já fechadas do SQLite;
        # a limpeza do temporário não é crítica para o teste.
        pass
    print("OK: todas as verificações do banco passaram.")


if __name__ == "__main__":
    main()
