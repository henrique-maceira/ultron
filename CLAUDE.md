# CLAUDE.md — Guia do projeto Ultron

> Este arquivo é carregado automaticamente pelo Claude Code ao abrir o repositório.
> Leia-o inteiro antes de alterar código. Ele resume o propósito, as decisões, as
> convenções e as armadilhas do projeto.

## O que é

**Ultron** é um assistente pessoal com quem o dono conversa pelo **Telegram** para
**delegar, priorizar e ser apoiado** nas demandas do dia a dia (mudança, trabalho,
estudos, casa, compromissos como médico/dentista). É **conversacional**, **proativo**
(lembretes + resumo diário) e sabe **pesquisar na web** e trazer um resumo semi-pronto.

Idioma de todo texto voltado ao usuário e dos comentários: **português (BR)**.

## Estado atual

- Projeto **funcional** e versionado. Branch de desenvolvimento: `claude/virtual-task-assistant-mdop7k`.
- Já implementado: bot Telegram, cérebro de IA com uso de ferramentas + busca web,
  tarefas e lembretes em SQLite, jobs proativos (lembretes + resumo diário), deploy Docker
  e scripts de auto-update.
- Validado: `scripts/check_db.py` passa; imports/wiring OK; `docker compose config` OK.
  **Ainda não houve** uma chamada real à API da Anthropic em produção (depende das
  credenciais do dono) nem teste ponta-a-ponta no Telegram.

## Decisões de arquitetura (não reverter sem motivo)

- **Plataforma:** Telegram via long-polling (sem porta/URL pública).
- **Cérebro:** Claude **Sonnet 5** (`claude-sonnet-5`), atrás de uma **abstração de
  provedor** (`Brain` em `assistant/brain.py`) para permitir trocar de IA por config.
- **Busca online:** ferramenta servidora nativa do Claude `web_search_20260209` (sem API
  de busca externa).
- **Google Agenda (opcional):** OAuth "Desktop app" com token persistido (não service
  account — precisa acessar agenda pessoal `@gmail`). Ligado por `GOOGLE_CALENDAR_ENABLED`.
  Ferramentas só entram no set quando habilitado (`tools.all_tool_defs`). Imports do Google
  são **lazy** em `gcal.py` (não quebrar `check_db.py` offline).
- **Agente estratégico (Fase 1):** além de tarefas/lembretes, há **metas** (`goals`) e
  **etapas** (`steps`) como entidades de 1ª classe. O fluxo é: objetivo → `create_goal` →
  decompor em `add_step` datadas (com marcos) → agendar blocos na agenda → acompanhar via
  progresso (`get_goal_plan`, `get_agenda`). Persona = chefe de gabinete estratégico
  (`prompts.py`). Cadências recorrentes usam `reminders`.
- **Persistência:** SQLite (`assistant/db.py`). Migração de schema é implícita
  (`CREATE TABLE IF NOT EXISTS` em `init_db`); não há ferramenta de migração — evolua com cuidado.
- **Proatividade:** `JobQueue` do `python-telegram-bot` (um só event loop, sem agendador
  extra).
- **Fuso:** `America/Sao_Paulo`, via `zoneinfo` + pacote `tzdata`.

## Mapa de arquivos

```
main.py                 # entrypoint: liga banco, cérebro, bot e jobs; roda long-polling
config.py               # Config a partir do .env (load_config)
assistant/
  brain.py              # Brain (abstrata) + AnthropicBrain (loop de tool-use + web_search); get_brain()
  tools.py              # TOOL_DEFS (tarefas/lembretes/metas/etapas) + CALENDAR_TOOL_DEFS + all_tool_defs()
  gcal.py               # cliente Google Agenda (OAuth token + eventos); imports do Google são lazy
  db.py                 # SQLite: tasks, reminders, goals, steps, messages, settings + CRUD; configure()
  prompts.py            # system_prompt(tz, calendar_enabled): persona estratégica PT-BR + regras
bot/
  telegram_bot.py       # build_application(config, brain): handlers /start,/help,texto; restrito ao dono
  jobs.py               # register_jobs(): lembretes (60s) + briefing diário + revisão semanal (dom 19h)
scripts/
  check_db.py           # teste offline do banco (sem rede/API)
  gcal_auth.py          # bootstrap OAuth do Google Agenda (roda uma vez, com navegador)
  update.ps1 / update.sh# auto-update: git pull + rebuild do container só se HEAD mudou
Dockerfile, docker-compose.yml, .dockerignore, data/.gitkeep   # deploy 24/7
```

## Como rodar (dev)

1. `cp .env.example .env` e preencha `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`,
   `OWNER_TELEGRAM_ID` (demais variáveis têm padrão).
2. `pip install -r requirements.txt`
3. `python main.py` (fica em long-polling; `/start` no Telegram).

## Como testar

- **Offline (sempre rode isto após mexer no db/tools):** `python scripts/check_db.py`
- **Imports/wiring:** rodar um `python -c` que faz `load_config()` (com env dummy),
  `db.configure(':memory:')`, `get_brain`, `build_application`, `register_jobs`.
- **Ponta-a-ponta:** com `.env` real, `python main.py` e conversar no Telegram
  (criar tarefa, listar, concluir, criar lembrete de 2 min, pedir priorização).

## Deploy (produção — máquina Windows do dono)

- Docker Desktop (WSL2), com “Start when you log in”.
- `docker compose up -d --build`; logs `docker compose logs -f ultron`.
- Banco persiste em `./data/ultron.db` (volume). `DB_PATH` é injetado pelo compose.
- Auto-update: `scripts/update.ps1` no Agendador de Tarefas (a cada 10 min). Detalhes no
  README, seção “Atualizações automáticas”.

## Convenções de código

- Python 3.11+; `from __future__ import annotations`; type hints; funções pequenas.
- Comentários e textos ao usuário em **PT-BR**. Identificadores em inglês/curtos.
- Datas de negócio (`due_date`, `remind_at`) são **ISO 8601 local ingênuo**
  (`YYYY-MM-DDTHH:MM:SS`), sem offset. `created_at/updated_at` são UTC ISO.
- Toda mudança de estado passa pelas ferramentas (`tools.py` -> `db.py`); o modelo não
  “finge” que salvou.

## Git / branch

- Desenvolva na branch **`claude/virtual-task-assistant-mdop7k`**.
- Commits claros e pequenos; `git pull --ff-only` antes de trabalhar.
- **Nunca** reescreva histórico já enviado (sem `--force`/rebase de commits publicados).
- Abra Pull Request **apenas se o dono pedir**.

## Armadilhas (gotchas) — não reintroduza bugs

- **SQLite + commit:** em `db.py`, ao inserir e já retornar a linha, faça o `SELECT` na
  **mesma conexão** dentro do `with _conn()` (o commit só ocorre ao sair do bloco; uma
  conexão nova não enxerga o insert e retorna `None`). Foi assim que `create_task` quebrou.
- **Loop de ferramentas:** reanexe `response.content` **inteiro** ao histórico (inclui
  blocos de *thinking*), pois o thinking adaptivo está ligado. Trate `stop_reason`:
  `tool_use` (executa tools locais), `pause_turn` (reenvia p/ o web_search continuar),
  `refusal` (resposta educada), senão extrai texto.
- **Busca web:** o `type` é `web_search_20260209` e exige Sonnet 5+/Opus recente. Se
  trocar `LLM_MODEL` para um modelo antigo, ajuste o `type` (veja a skill `claude-api`).
- **Lembretes:** `due_reminders()` compara strings ISO locais lexicalmente; mantenha o
  mesmo formato (`bot/jobs.py::LOCAL_FMT`).
- **Privacidade:** o bot só responde ao `OWNER_TELEGRAM_ID`. Não relaxe isso.
- **Google Agenda:** datas de negócio continuam ISO local ingênuo; a conversão p/ RFC3339
  com fuso acontece só na borda em `gcal.py`. Não importe libs do Google no topo dos módulos
  (mantenha lazy). Credenciais/token ficam em `data/` e **não** são versionados.
- **LLM/Anthropic:** ao mexer em qualquer coisa de modelo/SDK, **carregue a skill
  `claude-api`** e não invente IDs de modelo nem parâmetros.

## Próximos passos sugeridos (backlog)

- (a) Suporte a **Discord** (nova subclasse não é preciso; reusar `Brain`, novo adaptador de bot).
- (b) **Testes automatizados** de `tools.py` e do loop de `brain.py` (com API mockada).
- (c) ~~Integração com **Google Calendar**~~ — **feito** (`gcal.py` + ferramentas de agenda).
- (e) ~~**Agente estratégico** (metas/etapas/cronograma)~~ — **Fase 1 feita**. Próximas fases:
      Fase 2 = acompanhamento de progresso mais rico + replanejamento automático quando atrasa;
      Fase 3 = dependências entre etapas, sugestão de horários livres (free/busy) e
      **registro de gastos** (tabela de despesas + orçamento) para a meta financeira validar
      de verdade (hoje o dono envia gastos como mensagem; ainda não há persistência estruturada).
- (d) **CI** (GitHub Actions) validando `build` do Docker + `check_db.py` + lint.
