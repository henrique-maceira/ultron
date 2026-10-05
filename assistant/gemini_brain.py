"""Cérebro alternativo usando o Google Gemini (tier gratuito).

Implementa a mesma interface `Brain` do `AnthropicBrain`, com loop de uso de ferramentas
(as mesmas de `tools.py`), visão e PDF. A busca web nativa do Claude NÃO existe aqui;
o grounding do Gemini (Google Search) não é combinado com function calling nesta versão.

Os imports do SDK do Google são **lazy** (dentro dos métodos) para não exigir a lib
quando o provedor é a Anthropic, e para `scripts/check_db.py` seguir rodando offline.

Seleção via `LLM_PROVIDER=gemini` + `GEMINI_API_KEY` + `LLM_MODEL` (ex.: gemini-2.5-flash).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from typing import Any, Optional

from config import Config

from . import db, tools
from .brain import EMPTY_REPLY_FALLBACK, MAX_TOOL_ITERATIONS, Brain
from .prompts import system_prompt

logger = logging.getLogger(__name__)


class _Overloaded(Exception):
    """Sinaliza que o modelo gratuito está indisponível após os retries."""


class _QuotaExhausted(Exception):
    """Cota DIÁRIA do tier gratuito esgotada (não adianta tentar de novo agora)."""

    def __init__(self, retry_in: Optional[str] = None):
        super().__init__(retry_in or "")
        self.retry_in = retry_in


_RETRY_IN_RE = re.compile(r"retry in\s+([0-9hms.]+)", re.I)


MAX_OUTPUT_TOKENS = 8192
# O tier gratuito do Gemini às vezes devolve 503 (alta demanda); tenta de novo com backoff.
RETRY_STATUSES = {429, 500, 503}
MAX_API_RETRIES = 4
OVERLOADED_FALLBACK = (
    "O modelo gratuito está congestionado agora (muita demanda). Tenta de novo em alguns "
    "segundos, por favor."
)


class GeminiBrain(Brain):
    def __init__(self, config: Config):
        from google import genai  # lazy

        self._config = config
        self._client = genai.Client(api_key=config.gemini_api_key)
        self._tool = self._build_tool()

    # ------------------------------------------------------------------ #
    # Ferramentas (converte TOOL_DEFS -> FunctionDeclaration do Gemini)
    # ------------------------------------------------------------------ #
    def _build_tool(self):
        from google.genai import types  # lazy

        decls = []
        for t in tools.all_tool_defs(self._config.google_calendar_enabled):
            schema = t.get("input_schema") or {}
            kwargs: dict[str, Any] = {"name": t["name"], "description": t.get("description", "")}
            # Só manda parâmetros quando há propriedades (Gemini rejeita schema vazio).
            if schema.get("properties"):
                kwargs["parameters_json_schema"] = schema
            decls.append(types.FunctionDeclaration(**kwargs))
        return types.Tool(function_declarations=decls)

    # ------------------------------------------------------------------ #
    # Interface Brain
    # ------------------------------------------------------------------ #
    async def chat(self, user_id: int, user_text: str, persist: bool = True) -> str:
        from google.genai import types

        return await self._chat(user_id, [types.Part.from_text(text=user_text)], user_text, persist)

    async def chat_multimodal(
        self,
        user_id: int,
        content: list[dict[str, Any]],
        persist_text: str,
        persist: bool = True,
    ) -> str:
        return await self._chat(user_id, self._blocks_to_parts(content), persist_text, persist)

    def _blocks_to_parts(self, blocks: list[dict[str, Any]]) -> list[Any]:
        from google.genai import types

        parts: list[Any] = []
        for b in blocks:
            btype = b.get("type")
            if btype == "text":
                parts.append(types.Part.from_text(text=b["text"]))
            elif btype in ("image", "document"):
                src = b.get("source", {})
                data = base64.b64decode(src["data"])
                parts.append(types.Part.from_bytes(data=data, mime_type=src["media_type"]))
        return parts

    async def _chat(self, user_id: int, user_parts: list[Any], persist_text: str, persist: bool) -> str:
        from google.genai import types

        contents: list[Any] = [
            types.Content(
                role="model" if m["role"] == "assistant" else "user",
                parts=[types.Part.from_text(text=m["content"])],
            )
            for m in (db.get_recent_messages(user_id) if persist else [])
        ]
        contents.append(types.Content(role="user", parts=user_parts))

        reply = await self._run_loop(user_id, contents, warn_claims=persist)

        if persist:
            db.add_message(user_id, "user", persist_text)
            db.add_message(user_id, "assistant", reply)
        return reply

    async def _run_loop(self, user_id: int, contents: list[Any], warn_claims: bool = True) -> str:
        """Roda o loop e devolve a resposta já com o recibo das gravações do turno."""
        receipts: list[tuple[bool, str]] = []
        text = await self._loop(user_id, contents, receipts)
        return tools.finalize_reply(text, receipts, warn_claims)

    async def _loop(self, user_id: int, contents: list[Any], receipts: list) -> str:
        from google.genai import types

        system = system_prompt(self._config.tz, self._config.google_calendar_enabled)
        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[self._tool],
            max_output_tokens=MAX_OUTPUT_TOKENS,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

        for _ in range(MAX_TOOL_ITERATIONS):
            try:
                response = await self._generate(contents, config)
            except _QuotaExhausted as q:
                # "2h30m24.55s" -> "2h30m" (sem frações de segundo)
                espera = re.sub(r"\d+(\.\d+)?s$", "", (q.retry_in or "").rstrip(".")) or q.retry_in
                quando = f" Volta em ~{espera}." if espera else ""
                return (
                    "⚠️ Acabou a cota DIÁRIA do plano gratuito do Gemini (são só 20 requisições "
                    f"por dia).{quando} Não é congestionamento. Para continuar agora, use o chat "
                    "do Claude Code ou troque o provedor."
                )
            except _Overloaded:
                return OVERLOADED_FALLBACK

            # Preserva o turno do modelo (inclui os function_call) no histórico do turno.
            candidate = response.candidates[0] if response.candidates else None
            if candidate and candidate.content:
                contents.append(candidate.content)

            calls = response.function_calls or []
            if calls:
                tool_parts = []
                for fc in calls:
                    output = tools.execute_tool(user_id, fc.name, dict(fc.args or {}))
                    receipt = tools.receipt_for(fc.name, output)
                    if receipt:
                        receipts.append(receipt)
                    try:
                        payload = json.loads(output)
                    except (ValueError, TypeError):
                        payload = {"result": output}
                    if not isinstance(payload, dict):
                        payload = {"result": payload}
                    tool_parts.append(types.Part.from_function_response(name=fc.name, response=payload))
                contents.append(types.Content(role="user", parts=tool_parts))
                continue

            text = self._extract_text(candidate)
            if not text:
                logger.warning("Turno do Gemini terminou sem texto (finish_reason=%s).",
                               getattr(candidate, "finish_reason", None))
            return text or EMPTY_REPLY_FALLBACK

        return EMPTY_REPLY_FALLBACK

    async def _generate(self, contents: list[Any], config):
        """Chama a API com retry em erros transitórios (429/500/503). Esgotando os
        retries de erro transitório, levanta _Overloaded (vira mensagem amigável)."""
        from google.genai import errors

        for attempt in range(MAX_API_RETRIES):
            try:
                return await self._client.aio.models.generate_content(
                    model=self._config.llm_model, contents=contents, config=config
                )
            except errors.APIError as exc:
                if getattr(exc, "code", None) == 429:
                    detalhe = f"{exc} {getattr(exc, 'details', '')}"
                    if "PerDay" in detalhe:  # cota diária: insistir só gasta mais requisições
                        m = _RETRY_IN_RE.search(detalhe)
                        raise _QuotaExhausted(m.group(1) if m else None) from exc
                if getattr(exc, "code", None) not in RETRY_STATUSES:
                    raise
                if attempt >= MAX_API_RETRIES - 1:
                    logger.warning("Gemini %s: esgotou os retries.", exc.code)
                    raise _Overloaded() from exc
                delay = 2 ** attempt  # 1, 2, 4...
                logger.warning("Gemini %s; retry em %ss (tentativa %d).", exc.code, delay, attempt + 1)
                await asyncio.sleep(delay)
        raise _Overloaded()

    @staticmethod
    def _extract_text(candidate) -> str:
        if not candidate or not candidate.content or not candidate.content.parts:
            return ""
        pieces = [p.text for p in candidate.content.parts if getattr(p, "text", None)]
        return "\n".join(pieces).strip()
