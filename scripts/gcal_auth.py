"""Bootstrap da autorização do Google Agenda (roda UMA vez, no host, com navegador).

O que faz: abre o consentimento OAuth no navegador, você faz login na conta Google
dona da agenda e autoriza. O refresh token resultante é gravado em `GOOGLE_TOKEN_PATH`
(padrão `data/google_token.json`). Depois disso, o Ultron renova o token sozinho — não
precisa de navegador (funciona no Docker).

Pré-requisitos:
1. No Google Cloud Console: crie um projeto, habilite a "Google Calendar API" e crie uma
   credencial OAuth do tipo "App para computador" (Desktop app). Baixe o JSON e salve em
   `GOOGLE_CREDENTIALS_PATH` (padrão `data/google_credentials.json`).
2. Instale as dependências: `pip install -r requirements.txt`.

Uso:  python scripts/gcal_auth.py
"""
from __future__ import annotations

import os
import sys

# Permite importar o pacote quando executado da raiz do repo.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from assistant.gcal import SCOPES  # noqa: E402
from config import load_config  # noqa: E402


def main() -> None:
    config = load_config()
    creds_path = config.google_credentials_path
    token_path = config.google_token_path

    if not os.path.exists(creds_path):
        raise SystemExit(
            f"Credencial OAuth não encontrada em '{creds_path}'.\n"
            "Baixe o JSON do cliente OAuth (Desktop app) no Google Cloud Console e salve "
            "nesse caminho (ou ajuste GOOGLE_CREDENTIALS_PATH no .env)."
        )

    # Import lazy: só é necessário aqui, no bootstrap.
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
    creds = flow.run_local_server(port=0)

    os.makedirs(os.path.dirname(os.path.abspath(token_path)), exist_ok=True)
    with open(token_path, "w", encoding="utf-8") as fh:
        fh.write(creds.to_json())

    print(f"OK: token salvo em '{token_path}'.")
    print("Agora defina GOOGLE_CALENDAR_ENABLED=true no .env e (re)inicie o Ultron.")


if __name__ == "__main__":
    main()
