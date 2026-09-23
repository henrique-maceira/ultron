"""Cliente fino do Google Calendar.

Encapsula autenticação OAuth (com token persistido) e as operações de leitura/escrita
de eventos usadas pelas ferramentas do assistente (`tools.py`).

Decisões importantes:
- Os imports das bibliotecas do Google são **lazy** (feitos dentro das funções), para
  que importar este módulo não exija as dependências nem rede. Assim `scripts/check_db.py`
  e o carregamento offline do pacote continuam funcionando sem o Google instalado.
- Convenção de datas do projeto: datas de negócio são **ISO 8601 no fuso local ingênuo**
  (`YYYY-MM-DDTHH:MM:SS`, sem offset). O Google Calendar exige RFC3339 com fuso; a
  conversão acontece só aqui, na borda. Eventos retornados também voltam nesse formato
  local ingênuo, para o resto do app não precisar saber de fusos.

Bootstrap do token: rode `python scripts/gcal_auth.py` uma vez (abre o navegador e
consente); ele grava o refresh token em `GOOGLE_TOKEN_PATH`. Depois o refresh é
automático e headless (funciona no Docker).
"""
from __future__ import annotations

import datetime as dt
import os
from typing import Any, Optional
from zoneinfo import ZoneInfo

# Escopo de leitura/escrita da agenda.
SCOPES = ["https://www.googleapis.com/auth/calendar"]

_LOCAL_FMT = "%Y-%m-%dT%H:%M:%S"

# Estado do módulo (configurado em `configure`).
_CREDENTIALS_PATH: Optional[str] = None
_TOKEN_PATH: Optional[str] = None
_CALENDAR_ID: str = "primary"
_TZ: ZoneInfo = ZoneInfo("America/Sao_Paulo")
_service: Any = None  # serviço da API, construído sob demanda e cacheado


class CalendarNotConfigured(RuntimeError):
    """Levantada quando a agenda não foi habilitada/configurada."""


def configure(credentials_path: str, token_path: str, calendar_id: str, tz: ZoneInfo) -> None:
    """Define os caminhos de credenciais/token, o calendário-alvo e o fuso."""
    global _CREDENTIALS_PATH, _TOKEN_PATH, _CALENDAR_ID, _TZ, _service
    _CREDENTIALS_PATH = credentials_path
    _TOKEN_PATH = token_path
    _CALENDAR_ID = calendar_id or "primary"
    _TZ = tz
    _service = None  # invalida cache ao reconfigurar


def has_token() -> bool:
    """True se já existe um token salvo (ou seja, o bootstrap OAuth foi feito)."""
    return bool(_TOKEN_PATH and os.path.exists(_TOKEN_PATH))


# --------------------------------------------------------------------------- #
# Autenticação / serviço
# --------------------------------------------------------------------------- #
def _load_credentials():
    """Carrega credenciais do token salvo, renovando com o refresh token se preciso."""
    from google.auth.transport.requests import Request  # lazy
    from google.oauth2.credentials import Credentials  # lazy

    if not (_TOKEN_PATH and os.path.exists(_TOKEN_PATH)):
        raise CalendarNotConfigured(
            "Google Agenda ainda não autorizado. Rode `python scripts/gcal_auth.py` "
            "uma vez para autenticar e gerar o token."
        )

    creds = Credentials.from_authorized_user_file(_TOKEN_PATH, SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        # Persiste o token renovado para próximos usos.
        with open(_TOKEN_PATH, "w", encoding="utf-8") as fh:
            fh.write(creds.to_json())
        return creds
    raise CalendarNotConfigured(
        "Token do Google inválido ou sem refresh token. Rode `python scripts/gcal_auth.py` "
        "novamente para reautorizar."
    )


def _get_service():
    global _service
    if _service is None:
        from googleapiclient.discovery import build  # lazy

        creds = _load_credentials()
        _service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    return _service


# --------------------------------------------------------------------------- #
# Conversão de datas (borda local ingênuo <-> RFC3339 com fuso)
# --------------------------------------------------------------------------- #
def _to_rfc3339(local_iso: str) -> str:
    """'2026-09-22T14:30:00' (local ingênuo) -> RFC3339 com offset do fuso configurado."""
    naive = dt.datetime.strptime(local_iso, _LOCAL_FMT)
    return naive.replace(tzinfo=_TZ).isoformat()


def _from_rfc3339(value: str) -> str:
    """RFC3339 (com offset ou 'Z') -> ISO local ingênuo no fuso configurado.

    Datas de dia inteiro chegam como 'YYYY-MM-DD' e são devolvidas como estão.
    """
    if len(value) == 10 and value.count("-") == 2:  # 'YYYY-MM-DD' (dia inteiro)
        return value
    aware = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return aware.astimezone(_TZ).strftime(_LOCAL_FMT)


def _default_range() -> tuple[str, str]:
    """Janela padrão: de agora até o fim do 7º dia, em local ingênuo."""
    now = dt.datetime.now(_TZ)
    end = (now + dt.timedelta(days=7)).replace(hour=23, minute=59, second=59, microsecond=0)
    return now.strftime(_LOCAL_FMT), end.strftime(_LOCAL_FMT)


def _simplify_event(ev: dict[str, Any]) -> dict[str, Any]:
    start = ev.get("start", {})
    end = ev.get("end", {})
    return {
        "id": ev.get("id"),
        "summary": ev.get("summary", "(sem título)"),
        "start": _from_rfc3339(start.get("dateTime") or start.get("date", "")),
        "end": _from_rfc3339(end.get("dateTime") or end.get("date", "")),
        "all_day": "date" in start,
        "location": ev.get("location"),
        "description": ev.get("description"),
        "link": ev.get("htmlLink"),
    }


# --------------------------------------------------------------------------- #
# Operações
# --------------------------------------------------------------------------- #
def list_events(
    time_min: Optional[str] = None,
    time_max: Optional[str] = None,
    max_results: int = 20,
) -> list[dict[str, Any]]:
    """Lista eventos entre `time_min` e `time_max` (ISO local ingênuo). Sem intervalo,
    usa de agora até 7 dias à frente."""
    if not time_min or not time_max:
        d_min, d_max = _default_range()
        time_min = time_min or d_min
        time_max = time_max or d_max
    service = _get_service()
    resp = (
        service.events()
        .list(
            calendarId=_CALENDAR_ID,
            timeMin=_to_rfc3339(time_min),
            timeMax=_to_rfc3339(time_max),
            singleEvents=True,
            orderBy="startTime",
            maxResults=max(1, min(max_results, 100)),
        )
        .execute()
    )
    return [_simplify_event(ev) for ev in resp.get("items", [])]


def create_event(
    summary: str,
    start: str,
    end: Optional[str] = None,
    description: Optional[str] = None,
    location: Optional[str] = None,
    all_day: bool = False,
) -> dict[str, Any]:
    """Cria um evento. `start`/`end` em ISO local ingênuo. Sem `end`, dura 1 hora."""
    body: dict[str, Any] = {"summary": summary}
    if description:
        body["description"] = description
    if location:
        body["location"] = location

    if all_day:
        day = start[:10]
        end_day = (end or start)[:10]
        # Google trata o fim do dia inteiro como exclusivo; soma 1 dia se for o mesmo.
        if end_day == day:
            end_day = (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()
        body["start"] = {"date": day}
        body["end"] = {"date": end_day}
    else:
        if not end:
            end_dt = dt.datetime.strptime(start, _LOCAL_FMT) + dt.timedelta(hours=1)
            end = end_dt.strftime(_LOCAL_FMT)
        body["start"] = {"dateTime": _to_rfc3339(start), "timeZone": _TZ.key}
        body["end"] = {"dateTime": _to_rfc3339(end), "timeZone": _TZ.key}

    service = _get_service()
    ev = service.events().insert(calendarId=_CALENDAR_ID, body=body).execute()
    return _simplify_event(ev)


def update_event(
    event_id: str,
    summary: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    description: Optional[str] = None,
    location: Optional[str] = None,
) -> dict[str, Any]:
    """Atualiza campos de um evento existente (patch parcial)."""
    body: dict[str, Any] = {}
    if summary is not None:
        body["summary"] = summary
    if description is not None:
        body["description"] = description
    if location is not None:
        body["location"] = location
    if start is not None:
        body["start"] = {"dateTime": _to_rfc3339(start), "timeZone": _TZ.key}
    if end is not None:
        body["end"] = {"dateTime": _to_rfc3339(end), "timeZone": _TZ.key}
    if not body:
        raise ValueError("Nada para atualizar no evento.")

    service = _get_service()
    ev = (
        service.events()
        .patch(calendarId=_CALENDAR_ID, eventId=event_id, body=body)
        .execute()
    )
    return _simplify_event(ev)


def delete_event(event_id: str) -> bool:
    service = _get_service()
    service.events().delete(calendarId=_CALENDAR_ID, eventId=event_id).execute()
    return True
