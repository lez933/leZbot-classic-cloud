import asyncio, os, re, json
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, FSInputFile
from aiogram.filters import Command
from dotenv import load_dotenv
import phonenumbers
import xlsxwriter

# --------- ENV ---------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

# Mémoire : toutes les fiches nettoyées (format standard)
FICHES = []

# --------- Utils / Normalisation ---------
def to_plus33(num: str):
    if not num:
        return None
    try:
        n = phonenumbers.parse(num, "FR")
        if not phonenumbers.is_valid_number(n):
            return None
        return phonenumbers.format_number(n, phonenumbers.PhoneNumberFormat.E164)  # +336...
    except Exception:
        return None

def clean_name(s: str):
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s.title()

def make_fiche(d: dict):
    """Retourne une fiche standard 'propre'."""
    prenom = clean_name(d.get("prenom") or d.get("firstname") or d.get("first_name"))
    nom    = clean_name(d.get("nom") or d.get("lastname") or d.get("last_name"))
    email  = (d.get("email") or "").strip()
    # On accepte plein de clés possibles pour le numéro
    mobile = (
        d.get("mobile") or d.get("phone") or d.get("telephone") or d.get("numero")
        or d.get("portable") or d.get("téléphone") or d.get("telephone(s)")
        or d.get("téléphone(s)")
    )
    fixe   = d.get("fixe") or d.get("landline") or d.get("tel_fixe")
    cp     = (d.get("code_postal") or d.get("postalCode") or d.get("cp") or "").strip()
    ville  = clean_name(d.get("ville") or d.get("city"))
    adresse= (d.get("adresse") or d.get("address") or "").strip()
    iban   = (d.get("iban") or "").replace(" ", "")
    bic    = (d.get("bic") or d.get("swift") or "").replace(" ", "").upper()
    civil  = (d.get("civilite") or d.get("civility") or "").strip()
    birth  = (d.get("date_naissance") or d.get("birthDate") or "").strip()

    # Téléphones -> +33
    num = to_plus33(str(mobile)) or to_plus33(str(fixe)) or ""

    # Si nom_prenom fourni d'un coup, on le répartit pas: juste le garder en libellé
    libelle = d.get("nom_prenom")
    if libelle and not (nom or prenom):
        nom_prenom = libelle
    else:
        nom_prenom = (f"{nom} {prenom}".strip())

    return {
        "civilite": civil,
        "prenom": prenom,
        "nom": nom,
        "email": email,
        "mobile": num,            # on ne garde qu'un numéro propre
        "fixe": "",               # version simple
        "code_postal": cp,
        "ville": ville,
        "adresse": adresse,
        "iban": iban,
        "bic": bic,
        "date_naissance": birth,
        "nom_prenom": nom_prenom,
    }

# --------- Parsers ---------
SEP_RE = re.compile(r"^\s*[-_=]{5,}\s*$")

def parse_fiche_blocks(lines):
    """Fichiers 'fiche' : lignes 'Champ: valeur' + séparateurs -----"""
    out, block = [], []
    def flush():
        nonlocal block
        if not block:
            return
        m = {}
        for ln in block:
            ln = ln.strip()
            if not ln:
                continue
            if ":" in ln:
                k, v = ln.split(":", 1)
                m[k.strip().lower()] = v.strip()

        # mapping robuste des clés téléphone
        mobile_raw = (
            m.get("téléphone mobile") or m.get("telephone mobile") or
            m.get("téléphone portable") or m.get("telephone portable") or
            m.get("portable") or m.get("mobile") or
            m.get("téléphone(s)") or m.get("telephone(s)") or
            m.get("téléphone") or m.get("telephone")
        )
        fixe_raw = (
            m.get("téléphone fixe") or m.get("telephone fixe") or
            m.get("fixe")
        )

        fiche = make_fiche({
            "civilite": m.get("civilité") or m.get("civilite"),
            "prenom": m.get("prénom") or m.get("prenom"),
            "nom": m.get("nom"),
            "date_naissance": m.get("date de naissance") or m.get("date_naissance"),
            "email": m.get("email"),
            "mobile": mobile_raw,
            "fixe": fixe_raw,
            "code_postal": m.get("code postal") or m.get("code_postal"),
            "ville": m.get("ville"),
            "adresse": m.get("adresse"),
            "iban": m.get("iban"),
            "bic": m.get("bic") or m.get("swift"),
        })

        # Dernier recours : cherche un numéro dans le bloc
        if not fiche.get("mobile"):
            raw = "\n".join(block)
            cand = re.findall(r"(?:\+33|0)\s*[1-9](?:[ .-]?\d){8}", raw)
            if cand:
                fiche["mobile"] = to_plus33(cand[0]) or cand[0]

        out.append(fiche)
        block = []

    for ln in lines:
        if SEP_RE.match(ln):
            flush()
        else:
            block.append(ln)
    flush()
    return out

def parse_line_styles(lines):
    """Lignes brutes : 'Nom;Prenom;Tel' | 'Nom Prenom: 06..' | 'Nom Prénom | +33..' | JSONL"""
    out = []
    for s in lines:
        s = s.strip()
        if not s: 
            continue
        # JSON objet sur une ligne
        if s.startswith("{") and s.endswith("}"):
            try:
                obj = json.loads(s)
                out.append(make_fiche(obj))
                continue
            except Exception:
                pass
        # Nom;Prenom;Tel
        if ";" in s and s.count(";") >= 2:
            parts = [p.strip() for p in s.split(";")]
            rec = {"nom": parts[0], "prenom": parts[1], "phone": parts[2]}
            out.append(make_fiche(rec))
            continue
        # Nom Prenom: 06...
        if ":" in s and re.search(r"\d{6,}", s):
            k, v = s.split(":", 1)
            out.append(make_fiche({"nom_prenom": k, "phone": v}))
            continue
        # Nom Prenom | +33...
        if "|" in s and re.search(r"\+?\d{6,}", s):
            left, right = [x.strip() for x in s.split("|", 1)]
            out.append(make_fiche({"nom_prenom": left, "numero": right}))
            continue
    return out

def parse_txt(path: str):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    # d'abord : blocs 'fiche'
    fiches = parse_fiche_blocks(lines)
    if fiches:
        return fiches
    # sinon : lignes brutes
    return parse_line_styles(lines)

def parse_jsonl(path: str):
    out = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
                out.append(make_fiche(obj))
            except Exception:
                continue
    return out

# --------- Export ---------
def export_xlsx(path: str, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    wb = xlsxwriter.Workbook(path)
    ws = wb.add_worksheet("fiches")
    ws.write(0, 0, "Nom Prénom")
    ws.write(0, 1, "Numéro (+33)")
    r = 1
    for x in rows:
        ws.write(r, 0, x.get("nom_prenom",""))
        ws.write(r, 1, x.get("mobile",""))
        r += 1
    wb.close()

def export_fiche_txt(path: str, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for i, r in enumerate(rows, 1):
            f.write(
                "\n".join([
                    f"FICHE {i}",
                    f"Civilité: {r.get('civilite','')}",
                    f"Prénom: {r.get('prenom','')}",
                    f"Nom: {r.get('nom','')}",
                    f"Date de naissance: {r.get('date_naissance','')}",
                    f"Email: {r.get('email','')}",
                    f"Mobile: {r.get('mobile','')}",
                    f"Téléphone Fixe: {r.get('fixe','')}",
                    f"Code Postal: {r.get('code_postal','')}",
                    f"Ville: {r.get('ville','')}",
                    f"Adresse: {r.get('adresse','')}",
                    f"IBAN: {r.get('iban','')}",
                    f"BIC: {r.get('bic','')}",
                    "-" * 40,
                    ""
                ])
            )

# --------- Handlers ---------
@dp.message(Command("start"))
async def start(m: Message):
    await m.answer("leZbot-classic est prêt.\nCommandes: /load /count /num /export\nEnvoie-moi un fichier .txt ou .jsonl, je le mets au propre.")

# Réception de fichiers envoyés au bot (DM)
@dp.message(F.document)
async def handle_upload(m: Message):
    fn = m.document.file_name or "upload.txt"
    out_dir = os.path.join("data", "input")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, fn)
    await bot.download(m.document, out_path)
    await m.answer(f"📥 Fichier reçu: {fn}\nEnvoie maintenant /load pour le traiter.")

@dp.message(Command("load"))
async def load_cmd(m: Message):
    if ADMIN_ID and m.from_user and m.from_user.id != ADMIN_ID:
        return await m.answer("Seul l’admin peut /load")
    root = os.path.join("data","input")
    os.makedirs(root, exist_ok=True)
    added = 0
    for fn in os.listdir(root):
        p = os.path.join(root, fn)
        if not os.path.isfile(p):
            continue
        if fn.lower().endswith(".jsonl"):
            rows = parse_jsonl(p)
        else:
            rows = parse_txt(p)
        valid = [x for x in rows if x.get("mobile")]
        FICHES.extend(valid)
        added += len(valid)
    await m.answer(f"✅ {added} fiches ajoutées (propre +33). Total: {len(FICHES)}")

@dp.message(Command("count"))
async def count_cmd(m: Message):
    await m.answer(f"{len(FICHES)} fiches prêtes")

@dp.message(Command("num"))
async def num_cmd(m: Message):
    parts = (m.text or "").split(maxsplit=1)
    if len(parts) < 2: 
        return await m.answer("Ex: /num +33612345678 ou /num 0612345678")
    q = parts[1].strip()
    q_norm = to_plus33(q) or q  # normalise la recherche
    # on accepte la recherche en +33 ou en 0X
    res = [x for x in FICHES if x.get("mobile") in (q_norm, q)]
    if not res: 
        return await m.answer("Aucune fiche")
    txt = "\n".join(f"- {x['nom_prenom']} | {x['mobile']}" for x in res[:10])
    await m.answer(txt)

@dp.message(Command("export"))
async def export_cmd(m: Message):
    # /export size=500 format=xlsx|txt
    args = (m.text or "")[7:].strip()
    kv = {}
    for part in args.split():
        if "=" in part:
            k, v = part.split("=",1)
            kv[k.strip()] = v.strip()
    size = int(kv.get("size","500"))
    fmt  = kv.get("format","xlsx").lower()
    rows = FICHES[:size]
    if not rows:
        return await m.answer("Aucune fiche chargée. Envoie un fichier puis /load.")
    out_dir = os.path.join("data","staging")
    os.makedirs(out_dir, exist_ok=True)
    if fmt == "xlsx":
        out = os.path.join(out_dir, "export.xlsx")
        export_xlsx(out, rows)
        await m.answer_document(FSInputFile(out), caption=f"XLSX — {len(rows)} lignes")
    else:
        out = os.path.join(out_dir, "export.txt")
        export_fiche_txt(out, rows)
        await m.answer_document(FSInputFile(out), caption=f"TXT — {len(rows)} fiches propres")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
