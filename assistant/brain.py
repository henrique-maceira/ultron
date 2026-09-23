"""Cérebro de IA: abstração de provedor + implementação com a API da Anthropic.

A classe base `Brain` define a interface (`chat`). `AnthropicBrain` roda o loop de
uso de ferramentas contra a Messages API do Claude, com:
  - ferramentas customizadas (tarefas/lembretes) executadas localmente via `tools.py`;
  - a ferramenta servidora `web_search` (busca online nativa) resolvida no servidor.

Para trocar de provedor no futuro (Gemini, OpenAI), basta criar outra subclasse de
`Brain` e mapeá-la em `get_brain`.
"""
from __future__ import annotations

import abc
from typing import Any

from anthropic import AsyncAnthropic

from config import Config

from . import db, tools
from .prompts import system_prompt

# Limite de segurança para o número de idas e voltas de ferramentas num único turno.
MAX_TOOL_ITERATIONS = 12
MAX_TOKENS = 4096

# Ferramenta servidora de busca na web (roda na infraestrutura da Anthropic).
WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}


class Brain(abc.ABC):
    """Interface do cérebro de IA."""

    @abc.abstractmethod
    async def chat(self, user_id: int, user_text: str, persist: bool = True) -> str:
        """Recebe uma fala do usuário e devolve a resposta em texto do assistente."""
        raise NotImplementedError

    @abc.abstractmethod
    async def chat_multimodal(
        self,
        user_id: int,
        content: list[dict[str, Any]],
        persist_text: str,
        persist: bool = True,
    ) -> str:
        """Recebe conteúdo multimodal (blocos de texto/imagem/documento) e responde.

        `persist_text` é o resumo textual guardado no histórico (o histórico é só texto),
        já que reenviar imagens/PDFs a cada turno seria caro.
        """
        raise NotImplementedError


class AnthropicBrain(Brain):
    def __init__(self, config: Config):
        self._config = config
        self._client = AsyncAnthropic(api_key=config.anthropic_api_key)
        self._tools = [
            *tools.all_tool_defs(include_calendar=config.google_calendar_enabled),
            WEB_SEARCH_TOOL,
        ]

    async def chat(self, user_id: int, user_text: str, persist: bool = True) -> str:
        return await self._chat(user_id, user_text, user_text, persist)

    async def chat_multimodal(
        self,
        user_id: int,
        content: list[dict[str, Any]],
        persist_text: str,
        persist: bool = True,
    ) -> str:
        return await self._chat(user_id, content, persist_text, persist)

    async def _chat(self, user_id: int, user_content: Any, persist_text: str, persist: bool) -> str:
        # Monta o histórico (texto) + a nova mensagem (texto ou blocos multimodais).
        messages: list[dict] = [
            {"role": m["role"], "content": m["content"]}
            for m in (db.get_recent_messages(user_id) if persist else [])
        ]
        messages.append({"role": "user", "content": user_content})

        reply_text = await self._run_loop(user_id, messages)

        if persist:
            db.add_message(user_id, "user", persist_text)
            db.add_message(user_id, "assistant", reply_text)
        return reply_text

    async def _run_loop(self, user_id: int, messages: list[dict]) -> str:
        system = system_prompt(self._config.tz, self._config.google_calendar_enabled)

        for _ in range(MAX_TOOL_ITERATIONS):
            response = await self._client.messages.create(
                model=self._config.llm_model,
                max_tokens=MAX_TOKENS,
                system=system,
                thinking={"type": "adaptive"},
                tools=self._tools,
                messages=messages,
            )

            if response.stop_reason == "refusal":
                return (
                    "Desculpa, não consigo ajudar com esse pedido específico. "
                    "Podemos tentar de outro jeito?"
                )

            # Preserva a resposta completa (inclui blocos de thinking e de tool_use).
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "pause_turn":
                # A ferramenta servidora (web_search) pausou o turno; reenvia para continuar.
                continue

            if response.stop_reason == "tool_use":
                tool_results = self._handle_tool_calls(user_id, response)
                if not tool_results:
                    # Nenhuma ferramenta local para executar; devolve o que houver de texto.
                    return self._extract_text(response) or "Ok."
                messages.append({"role": "user", "content": tool_results})
                continue

            # end_turn / max_tokens
            return self._extract_text(response) or "Ok."

        return "Isso ficou mais longo que o esperado — pode reformular ou dividir o pedido?"

    @staticmethod
    def _handle_tool_calls(user_id: int, response) -> list[dict]:
        results: list[dict] = []
        for block in response.content:
            if getattr(block, "type", None) == "tool_use":
                output = tools.execute_tool(user_id, block.name, block.input)
                results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": output}
                )
        return results

    @staticmethod
    def _extract_text(response) -> str:
        parts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
        return "\n".join(p for p in parts if p).strip()


def get_brain(config: Config) -> Brain:
    provider = config.llm_provider.lower()
    if provider == "anthropic":
        return AnthropicBrain(config)
    raise NotImplementedError(
        f"Provedor de IA '{provider}' ainda não implementado. "
        "Use LLM_PROVIDER=anthropic ou adicione uma subclasse de Brain."
    )
