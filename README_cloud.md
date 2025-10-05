# Hébergement 24/7 (PC éteint)

## Option A — Render.com (très simple)
1. Crée un dépôt GitHub avec ces fichiers.
2. Sur Render → New → **Background Worker** → Connecte ton repo.
3. Build command: `pip install -r requirements.txt`
4. Start command: `python bot.py`
5. Variables d'env: **BOT_TOKEN**, **ADMIN_ID**
Le worker tourne 24/7, même PC éteint.

## Option B — Docker (VPS, OVH/Scaleway/Contabo...)
```
docker build -t leZbot .
docker run -d --name leZbot   -e BOT_TOKEN=123456:ABC   -e ADMIN_ID=123456789   -v $(pwd)/data:/app/data   leZbot
```
Le conteneur tourne en arrière-plan H24.

## Option C — Replit/Glitch (simple, moins stable)
Créer un repl Python, coller `bot.py`, installer requirements, ajouter les secrets BOT_TOKEN/ADMIN_ID, et lancer.
