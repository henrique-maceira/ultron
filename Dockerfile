# Imagem para rodar o Ultron 24/7 como serviço em segundo plano.
FROM python:3.12-slim

# Evita .pyc e garante logs sem buffer (aparecem no `docker compose logs`).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Instala dependências primeiro (melhor cache de build).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código da aplicação.
COPY . .

# Diretório do banco SQLite (montado como volume no docker-compose para persistir).
RUN mkdir -p /app/data

# Roda como usuário não-root por segurança.
RUN useradd --create-home --uid 1000 ultron && chown -R ultron:ultron /app
USER ultron

# O fuso vem do pacote Python `tzdata` (via zoneinfo), não do SO do host.
CMD ["python", "main.py"]
