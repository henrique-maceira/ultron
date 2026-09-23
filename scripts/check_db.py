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

    db.update_goal(USER, g["id"], status="concluido")
    assert db.list_goals(USER) == []  # só ativas por padrão
    assert len(db.list_goals(USER, status=None)) == 1  # todas

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
