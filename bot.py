import asyncio, os, re, json
from aiogram import Bot, Dispatcher
from aiogram.types import Message, FSInputFile
from aiogram.filters import Command
from dotenv import load_dotenv
import phonenumbers
import xlsxwriter

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

FICHES = []

def to_plus33(num: str):
    try:
        n = phonenumbers.parse(num, "FR")
        if not phonenumbers.is_valid_number(n):
            return None
        return phonenumbers.format_number(n, phonenumbers.PhoneNumberFormat.E164)
    except Exception:
        return None

def clean_name(s: str):
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s.title()

def normalize(rec: dict):
    prenom = clean_name(rec.get("prenom") or rec.get("firstname") or rec.get("first_name") or "")
    nom    = clean_name(rec.get("nom") or rec.get("lastname") or rec.get("last_name") or "")
    mobile = rec.get("mobile") or rec.get("phone") or rec.get("telephone")
    fixe   = rec.get("fixe") or rec.get("landline")
    num = to_plus33(str(mobile)) or to_plus33(str(fixe)) or ""
    return {"nom": nom, "prenom": prenom, "nom_prenom": (nom + " " + prenom).strip(), "numero": num}

def parse_txt(path: str):
    out = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            s = line.strip()
            if not s: continue
            if ";" in s and s.count(";") >= 2:
                parts = [p.strip() for p in s.split(";")]
                rec = {"nom": parts[0], "prenom": parts[1], "phone": parts[2]}
                out.append(normalize(rec))
            elif ":" in s:
                k, v = s.split(":", 1)
                if re.search(r"\d{6,}", v):
                    out.append(normalize({"nom": k, "phone": v}))
            elif s.startswith("{") and s.endswith("}"):
                try:
                    obj = json.loads(s)
                    out.append(normalize(obj))
                except Exception:
                    pass
    return out

def parse_jsonl(path: str):
    out = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try:
                obj = json.loads(line)
                out.append(normalize(obj))
            except Exception:
                continue
    return out

def export_xlsx(path: str, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    wb = xlsxwriter.Workbook(path)
    ws = wb.add_worksheet("fiches")
    ws.write(0, 0, "Nom Prénom")
    ws.write(0, 1, "Numéro (+33)")
    r = 1
    for x in rows:
        ws.write(r, 0, x.get("nom_prenom",""))
        ws.write(r, 1, x.get("numero",""))
        r += 1
    wb.close()

@dp.message(Command("start"))
async def start(m: Message):
    await m.answer("leZbot-classic (cloud) prêt. Commandes: /load /count /export /num")

@dp.message(Command("load"))
async def load_cmd(m: Message):
    if ADMIN_ID and m.from_user and m.from_user.id != ADMIN_ID:
        return await m.answer("Seul l’admin peut /load")
    root = os.path.join("data","input")
    os.makedirs(root, exist_ok=True)
    added = 0
    for fn in os.listdir(root):
        p = os.path.join(root, fn)
        if not os.path.isfile(p): continue
        if fn.lower().endswith(".jsonl"):
            rows = parse_jsonl(p)
        else:
            rows = parse_txt(p)
        valid = [x for x in rows if x.get("numero")]
        FICHES.extend(valid)
        added += len(valid)
    await m.answer(f"Chargé ✅ {added} fiches (+33) — total {len(FICHES)}")

@dp.message(Command("count"))
async def count_cmd(m: Message):
    await m.answer(f"{len(FICHES)} fiches en mémoire")

@dp.message(Command("num"))
async def num_cmd(m: Message):
    parts = (m.text or "").split(maxsplit=1)
    if len(parts) < 2: return await m.answer("Ex: /num +33612345678")
    q = parts[1].strip()
    res = [x for x in FICHES if x.get("numero")==q]
    if not res: return await m.answer("Aucune fiche")
    txt = "\n".join(f"- {x['nom_prenom']} | {x['numero']}" for x in res[:10])
    await m.answer(txt)

@dp.message(Command("export"))
async def export_cmd(m: Message):
    args = (m.text or "")[7:].strip()
    kv = {}
    for part in args.split():
        if "=" in part:
            k, v = part.split("=",1)
            kv[k]=v
    size = int(kv.get("size","500"))
    fmt = kv.get("format","xlsx").lower()
    rows = FICHES[:size]
    if not rows:
        return await m.answer("Aucune fiche chargée. Fais /load d’abord.")
    out_dir = os.path.join("data","staging")
    os.makedirs(out_dir, exist_ok=True)
    if fmt == "xlsx":
        out = os.path.join(out_dir, "export.xlsx")
        export_xlsx(out, rows)
        await m.answer_document(FSInputFile(out), caption=f"XLSX — {len(rows)} lignes")
    else:
        out = os.path.join(out_dir, "export.txt")
        with open(out,"w",encoding="utf-8") as f:
            for x in rows:
                f.write(f"{x['nom_prenom']}|{x['numero']}\n")
        await m.answer_document(FSInputFile(out), caption=f"TXT — {len(rows)} lignes")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
