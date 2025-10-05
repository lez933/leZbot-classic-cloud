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

VERSION = "v2-tail9+regex"

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

def digits_tail9(s: str) -> str:
    """Garde seulement les chiffres et compare sur les 9 derniers (FR)."""
    d = re.sub(r"\D", "", s or "")
    return d[-9:] if len(d) >= 9 else d

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
        "mobile": num,            # +33 propre
        "fixe": "",
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
LABEL_RX = re.compile(r"^([^:]+):\s*(.*)$")
PHONE_FALLBACK_RX = re.compile(r"(?:\+33|0)\s*[1-9](?:[ .-]?\d){8}")

MOBILE_LABELS = {
    "mobile","téléphone mobile","telephone mobile",
    "téléphone portable","telephone portable",
    "portable","téléphone(s)","telephone(s)",
    "téléphone","telephone","gsm","tel","numéro","numero"
}
FIXE_LABELS = {"téléphone fixe","telephone fixe","fixe"}

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
            mo = LABEL_RX.match(ln)
            if mo:
                k = mo.group(1).strip().lower()
                v = mo.group(2).strip()
                m[k] = v

        # Extraire num via labels connus
        mobile_raw = None
        fixe_raw = None
        for k, v in m.items():
            if k in MOBILE_LABELS and not mobile_raw:
                mobile_raw = v
            elif k in FIXE_LABELS and not fixe_raw:
                fixe_raw = v

        fiche = make_fiche({
            "civilite": m.get("civilité") or m.get("civilite"),
            "prenom": m.get("prénom") or m.get("prenom"),
            "nom": m.get("nom"),
            "date_naissance": m.get("date de naissance") or m.get("date_naissance"),
            "email": m.get("email"),
            "mobile": mobile_raw,
            "fixe": fixe_raw,
            "code_postal": m.get("code postal") or m.get("code_postal") or m.get("cp"),
            "ville": m.get("ville"),
            "adresse": m.get("adresse") or m.get("address"),
            "iban": m.get("iban"),
            "bic": m.get("bic") or m.get("swift"),
            "nom_prenom": (m.get("nom_prenom") or f"{m.get('nom','')} {m.get('prénom') or m.get('prenom') or ''}").strip(),
        })

        # Dernier recours : cherche un numéro dans le bloc
        if not fiche.get("mobile"):
            raw = "\n".join(block)
            cand = PHONE_FALLBACK_RX.findall(raw)
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

# --------- Export & Affichage ---------
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

def format_fiche_text(r, i=None):
    entete = f"FICHE {i}\n" if i is not None else ""
    return (
        entete +
        "Civilité: " + (r.get('civilite','') or "") + "\n" +
        "Prénom: " + (r.get('prenom','') or "") + "\n" +
        "Nom: " + (r.get('nom','') or "") + "\n" +
        "Date de naissance: " + (r.get('date_naissance','') or "") + "\n" +
        "Email: " + (r.get('email','') or "") + "\n" +
        "Mobile: " + (r.get('mobile','') or "") + "\n" +
        "Téléphone Fixe: " + (r.get('fixe','') or "") + "\n" +
        "Code Postal: " + (r.get('code_postal','') or "") + "\n" +
        "Ville: " + (r.get('ville','') or "") + "\n" +
        "Adresse: " + (r.get('adresse','') or "") + "\n" +
        "IBAN: " + (r.get('iban','') or "") + "\n" +
        "BIC: " + (r.get('bic','') or "") + "\n" +
        "----------------------------------------"
    )

def export_fiche_txt(path: str, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for i, r in enumerate(rows, 1):
            f.write(format_fiche_text(r, i) + "\n\n")

# --------- Handlers ---------
@dp.message(Command("start"))
async def start(m: Message):
    await m.answer(f"leZbot-classic {VERSION} prêt.\nCommandes: /load /count /num /export /clear\nEnvoie-moi un fichier .txt ou .jsonl, je le mets au propre.")

@dp.message(Command("clear"))
async def clear_cmd(m: Message):
    if ADMIN_ID and m.from_user and m.from_user.id != ADMIN_ID:
        return await m.answer("Seul l’admin peut /clear")
    FICHES.clear()
    await m.answer("Mémoire vidée. Envoie un fichier puis /load.")

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
    # Normalisation: on compare sur les 9 derniers chiffres (FR)
    q_tail = digits_tail9(to_plus33(q) or q)
    if not q_tail:
        return await m.answer("Numéro invalide. Exemple: /num 0612345678")

    res = [x for x in FICHES if digits_tail9(x.get("mobile")) == q_tail]
    if not res: 
        return await m.answer("Aucune fiche")
    # Renvoi en format FICHE propre
    parts_txt = [format_fiche_text(r, i+1) for i, r in enumerate(res[:3])]
    txt = "\n\n".join(parts_txt)
    await m.answer(txt[:3800])

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
