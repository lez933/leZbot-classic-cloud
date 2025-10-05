FROM python:3.11-slim

# Eviter les prompts
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dépendances système minimales
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Dossier data pour uploads/exports
RUN mkdir -p /app/data/input /app/data/staging

# Les variables BOT_TOKEN et ADMIN_ID seront passées par le provider
CMD ["python", "bot.py"]
