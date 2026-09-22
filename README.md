# Ultron — Assistente pessoal no Telegram

Ultron é um assistente virtual pessoal com quem você conversa pelo **Telegram** para
**delegar, priorizar e ser apoiado** nas suas demandas do dia a dia (mudança, trabalho,
estudos, casa, compromissos como médico e dentista).

Ele é **conversacional**, **proativo** e sabe **pesquisar soluções na web** e trazer um
resumo semi-pronto.

## O que ele faz

- 📝 **Tarefas**: cria, lista, atualiza e conclui afazeres por categoria e prioridade.
- ⏰ **Lembretes proativos**: agenda lembretes (únicos ou recorrentes) e te avisa na hora.
- ☀️ **Resumo diário**: toda manhã manda o que priorizar hoje, em que ordem e o próximo passo.
- 🎯 **Priorização em tempo real**: pergunte "o que faço agora?" e ele decide com base na sua agenda.
- 🔎 **Busca na web**: pesquisa opções (ex.: empresas de mudança) e traz um comparativo resumido.

O "cérebro" é o **Claude Sonnet 5** (Anthropic), com uma camada de abstração de provedor
(`assistant/brain.py`) que permite trocar de IA depois só via configuração.

## Arquitetura

```
main.py                 # entrypoint: liga banco, cérebro, bot e jobs; roda long-polling
config.py               # carrega variáveis do .env
assistant/
  brain.py              # abstração de IA + AnthropicBrain (loop de ferramentas + busca web)
  tools.py              # ferramentas (tarefas/lembretes) expostas ao modelo
  db.py                 # SQLite: tasks, reminders, messages, settings
  prompts.py            # persona e regras (system prompt) em PT-BR
bot/
  telegram_bot.py       # handlers do Telegram (restrito ao dono)
  jobs.py               # jobs proativos: lembretes e resumo diário
scripts/
  check_db.py           # teste rápido do banco, sem rede/API
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

## Exemplos de conversa

- "adiciona tarefa: montar as caixas da mudança, prioridade alta, categoria mudança"
- "quais minhas tarefas pendentes?"
- "conclui a tarefa 2"
- "me lembra de tomar o remédio todo dia às 8h"
- "me lembra de ligar pro corretor amanhã 10h"
- "pesquisa empresas de mudança em São Paulo e me resume as melhores opções"
- "o que eu devo priorizar hoje?"

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
