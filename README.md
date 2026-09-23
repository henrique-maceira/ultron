# Ultron — Assistente pessoal no Telegram

Ultron é um assistente virtual pessoal com quem você conversa pelo **Telegram** para
**delegar, priorizar e ser apoiado** nas suas demandas do dia a dia (mudança, trabalho,
estudos, casa, compromissos como médico e dentista).

Ele é **conversacional**, **proativo** e sabe **pesquisar soluções na web** e trazer um
resumo semi-pronto.

## O que ele faz

- 🧭 **Metas e cronogramas**: você passa um objetivo maior e ele quebra em etapas datadas, com marcos, e acompanha o progresso.
- 📝 **Tarefas**: cria, lista, atualiza e conclui afazeres por categoria e prioridade.
- ⏰ **Lembretes proativos**: agenda lembretes (únicos ou recorrentes) e te avisa na hora.
- ☀️ **Resumo diário**: toda manhã manda o que priorizar hoje, em que ordem e o próximo passo.
- 🎯 **Priorização em tempo real**: pergunte "o que faço agora?" e ele decide com base na sua agenda.
- 📅 **Google Agenda** (opcional): lê seus compromissos, evita conflitos e cria/remarca eventos ao montar planos.
- 🔎 **Busca na web**: pesquisa opções (ex.: empresas de mudança) e traz um comparativo resumido.

O "cérebro" é o **Claude Sonnet 5** (Anthropic), com uma camada de abstração de provedor
(`assistant/brain.py`) que permite trocar de IA depois só via configuração.

## Arquitetura

```
main.py                 # entrypoint: liga banco, cérebro, bot e jobs; roda long-polling
config.py               # carrega variáveis do .env
assistant/
  brain.py              # abstração de IA + AnthropicBrain (loop de ferramentas + busca web)
  tools.py              # ferramentas (tarefas/lembretes/metas/etapas/agenda) expostas ao modelo
  gcal.py               # cliente do Google Agenda (OAuth + eventos)
  db.py                 # SQLite: tasks, reminders, goals, steps, messages, settings
  prompts.py            # persona e regras (system prompt) em PT-BR
bot/
  telegram_bot.py       # handlers do Telegram (restrito ao dono)
  jobs.py               # jobs proativos: lembretes e resumo diário
scripts/
  check_db.py           # teste rápido do banco, sem rede/API
  gcal_auth.py          # bootstrap OAuth do Google Agenda (roda uma vez)
```

## Pré-requisitos

- Python 3.10+
- Uma **chave de API da Anthropic** — https://console.anthropic.com/
- Um **bot do Telegram** criado com o [@BotFather](https://t.me/BotFather) (você recebe um token)
- Seu **ID numérico do Telegram** — descubra falando com o [@userinfobot](https://t.me/userinfobot)

## Instalação

```bash
git clone <este-repo>
cd ultron
python -m venv .venv && source .venv/bin/activate   # opcional, recomendado
pip install -r requirements.txt

cp .env.example .env
# edite o .env e preencha ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN e OWNER_TELEGRAM_ID
```

Variáveis do `.env` (as opcionais têm padrão):

| Variável | Obrigatória | Padrão | Descrição |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | sim | — | Chave da API da Anthropic |
| `TELEGRAM_BOT_TOKEN` | sim | — | Token do bot (BotFather) |
| `OWNER_TELEGRAM_ID` | sim | — | Seu ID no Telegram (só você conversa com o bot) |
| `LLM_PROVIDER` | não | `anthropic` | Provedor de IA |
| `LLM_MODEL` | não | `claude-sonnet-5` | Modelo do cérebro |
| `TIMEZONE` | não | `America/Sao_Paulo` | Fuso horário (IANA) |
| `BRIEFING_TIME` | não | `08:00` | Horário do resumo diário (HH:MM) |
| `DB_PATH` | não | `ultron.db` | Caminho do banco SQLite |
| `GOOGLE_CALENDAR_ENABLED` | não | `false` | Liga as ferramentas de agenda (ver seção abaixo) |
| `GOOGLE_CREDENTIALS_PATH` | não | `data/google_credentials.json` | JSON do cliente OAuth (Desktop app) |
| `GOOGLE_TOKEN_PATH` | não | `data/google_token.json` | Token gerado no bootstrap OAuth |
| `GOOGLE_CALENDAR_ID` | não | `primary` | Qual agenda usar |

## Como rodar

```bash
python main.py
```

O bot fica em *long-polling* (não precisa de URL pública). Abra a conversa com o seu bot
no Telegram e mande `/start`. **O processo precisa ficar rodando** para os lembretes e o
resumo diário funcionarem — na sua máquina, numa VPS, ou como serviço (systemd, Docker etc.).

## Rodar 24/7 com Docker (recomendado no Windows)

Para o Ultron ficar sempre no ar (os lembretes e o resumo diário dependem disso), o mais
prático é rodar em container, que **reinicia sozinho** se cair ou se a máquina reiniciar.

**Pré-requisito:** [Docker Desktop](https://www.docker.com/products/docker-desktop/)
instalado. No Windows, use o backend WSL2 e, em *Settings → General*, marque
**"Start Docker Desktop when you log in"** para o container voltar após um reboot.

```bash
# 1. configure as credenciais (uma vez)
copy .env.example .env        # no PowerShell/CMD; use "cp" no Git Bash/WSL
# edite o .env: ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, OWNER_TELEGRAM_ID

# 2. suba em segundo plano (constrói a imagem na primeira vez)
docker compose up -d --build

# 3. acompanhe os logs
docker compose logs -f ultron
```

Comandos úteis:

| Ação | Comando |
|---|---|
| Ver logs | `docker compose logs -f ultron` |
| Parar | `docker compose down` |
| Reiniciar | `docker compose restart ultron` |
| Atualizar após mudanças | `git pull` e depois `docker compose up -d --build` |

O banco fica persistido em `./data/ultron.db` (volume), então suas tarefas e lembretes
sobrevivem a reinícios e rebuilds. Não é preciso abrir nenhuma porta — o bot usa
long-polling.

## Atualizações automáticas (fazer os ajustes subirem direto)

A "ponte" entre o desenvolvimento e a sua máquina é o **GitHub**: as mudanças são enviadas
(`push`) para a branch, e a sua máquina **puxa** (`pull`) e reconstrói o container sozinha.
Não há acesso remoto direto à máquina — só o GitHub no meio, o que é seguro.

**Atualização manual** (quando quiser):

```powershell
# Windows (PowerShell), na pasta do projeto:
./scripts/update.ps1
```
```bash
# Linux / macOS / WSL:
bash scripts/update.sh
```
O script puxa a branch e **só reconstrói se houver mudança** de código.

**Atualização automática (Windows — Agendador de Tarefas):** faça a máquina rodar o
`update.ps1` periodicamente, para que os ajustes subam sem você fazer nada.

1. Abra o **Agendador de Tarefas** (Task Scheduler) → *Criar Tarefa…* (não "tarefa básica").
2. **Geral:** nome `Ultron Update`; marque *Executar somente quando o usuário estiver
   conectado* (o Docker Desktop roda na sua sessão).
3. **Disparadores → Novo:** *Ao fazer logon* e marque *Repetir a cada 10 minutos* por
   *Indefinidamente*.
4. **Ações → Novo:** *Iniciar um programa*
   - Programa: `powershell.exe`
   - Argumentos: `-ExecutionPolicy Bypass -NoProfile -File "C:\caminho\para\ultron\scripts\update.ps1"`
     (troque pelo caminho real da pasta do projeto)
5. **Condições:** desmarque *Iniciar a tarefa somente se o computador estiver...* se quiser
   que rode em bateria também. Salve.

Pronto: a cada 10 minutos a máquina verifica o GitHub e, se houver algo novo, reconstrói o
container automaticamente. (No Linux/macOS, o equivalente é um `cron` chamando
`scripts/update.sh`.)

> Requisito: a máquina precisa conseguir dar `git pull` do repositório (repositório público,
> ou credenciais/PAT do GitHub configuradas no `git` da máquina).

## Integração com o Google Agenda (opcional)

Com isso o Ultron passa a **ler e escrever na sua agenda**: consulta o que já está marcado
para não sugerir horários em conflito e cria/remarca compromissos (consultas, reuniões,
blocos de foco) quando vocês montam um plano.

A autorização usa OAuth com um token salvo. Você faz o consentimento no navegador **uma
única vez**; depois o token se renova sozinho (funciona no Docker, sem navegador).

**1. Crie a credencial OAuth (uma vez, no Google Cloud Console):**

1. Acesse https://console.cloud.google.com/ e crie (ou escolha) um projeto.
2. Em *APIs e serviços → Biblioteca*, habilite a **Google Calendar API**.
3. Em *APIs e serviços → Tela de permissão OAuth*, configure como *Externo* e adicione seu
   e-mail Google em **Usuários de teste** (senão o consentimento é bloqueado).
4. Em *APIs e serviços → Credenciais → Criar credenciais → ID do cliente OAuth*, tipo
   **App para computador** (Desktop app). Baixe o JSON e salve em
   `data/google_credentials.json`.

**2. Autorize (uma vez, na sua máquina com navegador):**

```bash
python scripts/gcal_auth.py
```
Isso abre o navegador para você logar e consentir; ao final, grava
`data/google_token.json` (o refresh token).

**3. Ligue a integração** no `.env`:

```
GOOGLE_CALENDAR_ENABLED=true
```
Reinicie o Ultron (`python main.py` ou `docker compose up -d --build`). No Docker, os
arquivos em `./data` já são montados no container, então o token é reaproveitado.

> ⚠️ `data/google_credentials.json` e `data/google_token.json` **não** são versionados
> (estão no `.gitignore`). Trate-os como segredos.

## Exemplos de conversa

- "adiciona tarefa: montar as caixas da mudança, prioridade alta, categoria mudança"
- "quais minhas tarefas pendentes?"
- "conclui a tarefa 2"
- "me lembra de tomar o remédio todo dia às 8h"
- "me lembra de ligar pro corretor amanhã 10h"
- "pesquisa empresas de mudança em São Paulo e me resume as melhores opções"
- "o que eu devo priorizar hoje?"
- "o que tenho na agenda essa semana?"
- "marca dentista quinta 15h e me lembra 1h antes"
- "remarca a reunião de amanhã para as 16h"
- "minha meta é tirar a certificação AWS até dezembro — monta o cronograma"
- "como estão minhas metas?"
- "conclui a etapa 3 da mudança"

## Verificação rápida (sem API)

Testa o banco e as operações de tarefas/lembretes isoladamente:

```bash
python scripts/check_db.py
```

## Trocar de IA no futuro

Crie uma nova subclasse de `Brain` em `assistant/brain.py` (ex.: `GeminiBrain`) e mapeie-a
em `get_brain()`. O restante do sistema (bot, jobs, banco) não muda.

## Privacidade

O assistente responde **apenas** ao `OWNER_TELEGRAM_ID` configurado. Os dados ficam num
SQLite local (`ultron.db`, fora do controle de versão). A conversa é enviada à API do
provedor de IA escolhido para gerar as respostas.
