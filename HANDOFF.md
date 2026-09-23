# HANDOFF — Continuidade do projeto Ultron

Este documento passa o bastão para o **próximo agente** (ex.: Claude Code rodando na
máquina do dono) continuar as alterações de código sem perder contexto.

> Leia primeiro o **`CLAUDE.md`** na raiz — ele tem o guia completo (arquitetura,
> convenções e armadilhas). Este HANDOFF é o resumo de “por onde estou e o que fazer”.

## Onde o projeto está

- Repositório: `henrique-maceira/ultron`. Branch de trabalho:
  **`claude/virtual-task-assistant-mdop7k`**. Trabalhe nela (não em `main`).
- Já pronto e no GitHub:
  1. App do assistente (Telegram + Claude Sonnet 5 + busca web + tarefas/lembretes + jobs).
  2. Deploy Docker (`Dockerfile`, `docker-compose.yml`) para rodar 24/7.
  3. Auto-update (`scripts/update.ps1` / `update.sh`) para os pushes subirem sozinhos na máquina.
- Pendências conhecidas: teste ponta-a-ponta real no Telegram; backlog em `CLAUDE.md`.

## Regras ao continuar

1. Desenvolva na branch `claude/virtual-task-assistant-mdop7k`; `git pull --ff-only` antes.
2. Após mexer em `db.py`/`tools.py`, rode `python scripts/check_db.py`.
3. Antes de qualquer coisa de IA/modelo, carregue a skill `claude-api` (não chute IDs).
4. Commits claros e pequenos; **não** reescreva histórico publicado; PR só se o dono pedir.
5. Ao mudar código que roda em produção, lembre que a máquina reconstrói o container via
   `scripts/update.ps1` — mantenha a branch sempre em estado que builda.
6. Respeite as **armadilhas** listadas no `CLAUDE.md` (commit do SQLite, loop de tools,
   `web_search_20260209`, formato de datas, restrição ao dono).

## Setup rápido na máquina (se ainda não fez)

```bash
git clone https://github.com/henrique-maceira/ultron.git
cd ultron
git checkout claude/virtual-task-assistant-mdop7k
cp .env.example .env    # preencha ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, OWNER_TELEGRAM_ID
pip install -r requirements.txt
python scripts/check_db.py   # sanity offline
# rodar local: python main.py      |  rodar 24/7: docker compose up -d --build
```

## Prompt inicial pronto para colar no outro agente

```
Você vai continuar o desenvolvimento do projeto "Ultron", um assistente pessoal em
Python que conversa pelo Telegram (cérebro Claude Sonnet 5, com busca web nativa,
tarefas e lembretes em SQLite e jobs proativos).

Antes de qualquer alteração:
1. Leia o CLAUDE.md e o HANDOFF.md na raiz do repositório — eles têm a arquitetura,
   as convenções e as armadilhas do projeto.
2. Trabalhe na branch claude/virtual-task-assistant-mdop7k (git pull --ff-only antes).
3. Sempre que mexer em banco/ferramentas, rode: python scripts/check_db.py
4. Ao mexer em qualquer coisa de modelo/API da Anthropic, carregue a skill claude-api
   e não invente IDs de modelo.
5. Commits claros e pequenos; não reescreva histórico já enviado; abra PR só se eu pedir.

Minha próxima tarefa é: <DESCREVA AQUI O QUE QUER MUDAR/ADICIONAR>.
Comece resumindo seu entendimento e o plano antes de editar.
```

## Backlog sugerido

- (a) Discord (reusar o `Brain`, novo adaptador de bot).
- (b) Testes automatizados de `tools.py` e do loop de `brain.py` (API mockada).
- (c) ~~Integração com Google Calendar~~ — **feito** (ver `assistant/gcal.py`, ferramentas de
      agenda em `tools.py`, setup no README seção "Integração com o Google Agenda").
      Próximo: free/busy (sugerir horários livres) e espelhar tarefas com prazo em eventos.
- (d) CI (GitHub Actions): build do Docker + `check_db.py` + lint.
