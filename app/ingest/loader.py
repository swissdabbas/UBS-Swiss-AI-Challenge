"""Load and validate client transaction CSVs, derive the base profile, load the product catalog."""

from __future__ import annotations

import csv
import glob
import hashlib
import io
import re
import unicodedata
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import yaml

from .. import config
from .schemas import Product, Profile, Transaction

REQUIRED_COLUMNS = ["date", "description", "category", "amount", "currency", "balance"]
PRODUCT_COLUMNS = [
    "Product Name", "Category", "Product Type", "Key Characteristics",
    "Risk Profile Required", "Risk Requirement Details", "Price / Cost", "Source URL",
]


class IngestError(ValueError):
    """Raised with a human-readable message when an input file does not match its schema."""


# ----------------------------------------------------------------- helpers

def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


KNOWN_MERCHANTS = {
    "migros": "Migros", "coop": "Coop", "denner": "Denner", "aldi": "Aldi Suisse", "aldi suisse": "Aldi Suisse",
    "lidl": "Lidl", "volg": "Volg", "globus": "Globus", "manor": "Manor", "sbb": "SBB", "swisscom": "Swisscom",
    "salt": "Salt", "sunrise": "Sunrise", "ewz": "EWZ", "starbucks": "Starbucks", "migrol": "Migrol",
}
_CANTON_CODES = "ZH|BE|LU|UR|SZ|OW|NW|GL|ZG|FR|SO|BS|BL|SH|AR|AI|SG|GR|AG|TG|TI|VD|VS|NE|GE|JU"
_CITIES = "Zürich|Zurich|Bern|Basel|Genf|Genève|Geneva|Luzern|Lausanne|Winterthur|St\\. Gallen|Lugano"


def normalize_counterparty(description: str) -> str:
    """'MIGROS ZH 1234' -> 'Migros', 'Starbucks Zürich' -> 'Starbucks'; other text is kept."""
    s = re.sub(r"\s+", " ", description.strip())
    s = re.sub(r"\s+#?\d{3,}$", "", s)                       # store / terminal number
    s = re.sub(rf"\s+(?:{_CANTON_CODES})$", "", s)             # canton code (case-sensitive)
    s = re.sub(rf"\s+(?:{_CITIES})$", "", s, flags=re.I)       # city suffix
    low = s.lower()
    if low in KNOWN_MERCHANTS:
        return KNOWN_MERCHANTS[low]
    if s.isupper() and len(s) > 3:
        s = s.title()
    return s


def _parse_date(value: str) -> date:
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date {value!r} (expected YYYY-MM-DD or DD.MM.YYYY)")


def _parse_amount(value: str) -> float:
    v = value.strip().replace("'", "").replace("’", "").replace(" ", "")
    if v.count(",") and v.count("."):
        v = v.replace(",", "")
    elif v.count(",") == 1:
        v = v.replace(",", ".")
    return float(v)


def txn_id(client_id: str, row: int, d: date, description: str, amount: float) -> str:
    raw = f"{client_id}|{row}|{d.isoformat()}|{description}|{amount:.2f}"
    return "t" + hashlib.sha1(raw.encode()).hexdigest()[:10]


# ------------------------------------------------------------ transactions

def parse_transactions(text: str, client_id: str, source_name: str) -> list[Transaction]:
    base = config.settings()["app"]["base_currency"]
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    found = [c.strip() for c in (reader.fieldnames or [])]
    missing = [c for c in REQUIRED_COLUMNS if c not in found]
    if missing:
        raise IngestError(
            f"{source_name}: missing column(s) {', '.join(missing)}. "
            f"Found: {', '.join(found) or 'none'}. Expected: {', '.join(REQUIRED_COLUMNS)}."
        )
    out: list[Transaction] = []
    for i, raw in enumerate(reader, start=2):  # row 1 is the header
        row = {k.strip(): (v or "") for k, v in raw.items() if k}
        try:
            d = _parse_date(row["date"])
            amount = _parse_amount(row["amount"])
            balance = _parse_amount(row["balance"]) if row["balance"].strip() else None
        except ValueError as exc:
            raise IngestError(f"{source_name} row {i}: {exc}") from None
        desc = row["description"].strip()
        if not desc:
            raise IngestError(f"{source_name} row {i}: empty description")
        cur = (row["currency"].strip() or base).upper()
        out.append(
            Transaction(
                id=txn_id(client_id, i, d, desc, amount),
                client_id=client_id,
                row=i,
                date=d,
                description=desc,
                counterparty=normalize_counterparty(desc),
                source_category=row["category"].strip() or "Uncategorised",
                amount=amount,
                currency=cur,
                balance=balance,
                is_fx=cur != base,
            )
        )
    if not out:
        raise IngestError(f"{source_name}: no transactions found")
    out.sort(key=lambda t: (t.date, t.row))
    return out


# ------------------------------------------------------------------ profile

FILENAME_RE = re.compile(
    r"^persona(?P<num>\d+)_(?P<cur>[A-Z]{3})_(?P<age>\d+(?:-\d+)?|\d+plus)_(?P<gender>male|female)_"
    r"(?P<label>.+)_(?P<marital>single|married|divorced|widowed)$"
)

LABELS = {
    "gigworker": ("Gig worker", "Mass market"),
    "retail": ("Retail employee", "Mass market"),
    "swengineer": ("Software engineer", "Mass affluent"),
    "freelancer": ("Freelance designer", "Mass market"),
    "teacher": ("Teacher", "Mass market"),
    "nurse": ("Nurse", "Mass market"),
    "contractor": ("Self-employed contractor", "Mass affluent"),
    "accountant": ("Accountant", "Mass affluent"),
    "retired": ("Retired", "Mass market"),
    "lownetworth_debt": ("Temporary worker", "Low net worth (in debt)"),
    "massaffluent": ("Salaried employee", "Mass affluent"),
    "hnw": ("Salaried professional", "High net worth"),
    "vhnw": ("Entrepreneur / investor", "Very high net worth"),
    "uhnw": ("Family wealth principal", "Ultra high net worth"),
    "negativenetworth": ("Freelancer", "Negative net worth"),
}

# Demo names and exact ages for the synthetic personas (ages fall inside each file's age band).
PERSONAS = {
    1: ("Luca Meier", 22),
    2: ("Lea Brunner", 20),
    3: ("Noah Keller", 31),
    4: ("Chiara Rossi", 28),
    5: ("Thomas Müller", 44),
    6: ("Sandra Weber", 41),
    7: ("Beat Schneider", 58),
    8: ("Monika Huber", 55),
    9: ("Hans Zimmermann", 71),
    10: ("Ursula Fischer", 68),
    11: ("Jana Baumann", 24),
    12: ("Marco Steiner", 42),
    13: ("Claudia Frei", 48),
    14: ("Andreas Gerber", 55),
    15: ("Christoph Sutter", 63),
    16: ("David Moser", 33),
}


def profile_from_filename(path: Path) -> Profile:
    stem = path.stem
    m = FILENAME_RE.match(stem)
    if not m:
        raise IngestError(
            f"{path.name}: file name does not follow persona<N>_<CUR>_<age>_<gender>_<label>_<marital>.csv"
        )
    age_raw = m["age"]
    if age_raw.endswith("plus"):
        lo = int(age_raw[:-4])
        age, band, est = lo + 3, f"{lo}+", True
    elif "-" in age_raw:
        lo, hi = map(int, age_raw.split("-"))
        age, band, est = (lo + hi) // 2, f"{lo}-{hi}", True
    else:
        age, band, est = int(age_raw), age_raw, False
    occupation, segment = LABELS.get(m["label"], (m["label"].replace("_", " ").title(), "Mass market"))
    num = int(m["num"])
    name = f"Persona {num}"
    if num in PERSONAS:
        name, age = PERSONAS[num]
        est = False
    return Profile(
        client_id=f"persona{num}",
        display_name=name,
        source_file=path.name,
        age=age,
        age_band=band,
        age_estimated=est,
        gender=m["gender"],
        marital_status=m["marital"],
        occupation=occupation,
        segment=segment,
        currency=m["cur"],
    )


# ------------------------------------------------------------------ clients

def persona_files() -> list[Path]:
    pattern = str(config.path(config.settings()["app"]["personas_glob"]))
    files = [Path(p) for p in glob.glob(pattern)]
    return sorted(files, key=lambda p: int(re.search(r"persona(\d+)", p.name).group(1)))


def upload_files() -> list[Path]:
    d = config.path(config.settings()["app"]["uploads_dir"])
    return sorted(d.glob("*.csv")) if d.exists() else []


def _upload_profile(path: Path) -> Profile:
    meta_path = path.with_suffix(".yaml")
    meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    return Profile(
        client_id=path.stem,
        display_name=meta.get("display_name") or path.stem,
        source_file=path.name,
        age=int(meta.get("age", 40)),
        age_band=str(meta.get("age", 40)),
        age_estimated="age" not in meta,
        gender=meta.get("gender"),
        marital_status=meta.get("marital_status", "single"),
        occupation=meta.get("occupation", "Unknown"),
        segment=meta.get("segment", "Mass market"),
        uploaded=True,
    )


def list_profiles() -> list[Profile]:
    return [profile_from_filename(p) for p in persona_files()] + [_upload_profile(p) for p in upload_files()]


def client_path(client_id: str) -> Path:
    for p in persona_files():
        if profile_from_filename(p).client_id == client_id:
            return p
    for p in upload_files():
        if p.stem == client_id:
            return p
    raise KeyError(client_id)


def load_client(client_id: str) -> tuple[Profile, list[Transaction]]:
    path = client_path(client_id)
    profile = _upload_profile(path) if path.parent == config.path(config.settings()["app"]["uploads_dir"]) else profile_from_filename(path)
    txns = parse_transactions(path.read_text(encoding="utf-8"), profile.client_id, path.name)
    return profile, txns


# ----------------------------------------------------------------- products

def parse_products(rows: Iterable[dict], attrs: dict, source_name: str) -> list[Product]:
    rows = list(rows)
    if not rows:
        raise IngestError(f"{source_name}: no products found")
    missing = [c for c in PRODUCT_COLUMNS if c not in rows[0]]
    if missing:
        raise IngestError(f"{source_name}: missing column(s) {', '.join(missing)}")
    defaults = attrs.get("defaults", {})
    per_product = attrs.get("products", {})
    products: list[Product] = []
    for r in rows:
        pid = slugify(r["Product Name"])
        if pid not in per_product:
            raise IngestError(f"{source_name}: product {r['Product Name']!r} ({pid}) has no entry in config/products.yaml")
        extra = {**defaults, **(per_product[pid] or {})}
        products.append(
            Product(
                id=pid,
                name=r["Product Name"].strip(),
                category=r["Category"].strip(),
                product_type=r["Product Type"].strip(),
                characteristics=r["Key Characteristics"].strip(),
                risk_profile_required=r["Risk Profile Required"].strip(),
                risk_details=r["Risk Requirement Details"].strip(),
                price=r["Price / Cost"].strip(),
                url=r["Source URL"].strip(),
                **extra,
            )
        )
    unknown = set(per_product) - {p.id for p in products}
    if unknown:
        raise IngestError(f"config/products.yaml lists products not in {source_name}: {', '.join(sorted(unknown))}")
    return products


@lru_cache(maxsize=1)
def load_products() -> tuple[Product, ...]:
    csv_path = config.path(config.settings()["app"]["products_csv"])
    with open(csv_path, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    attrs = config.load_yaml("products.yaml")
    return tuple(parse_products(rows, attrs, csv_path.name))


def product_index() -> dict[str, Product]:
    return {p.id: p for p in load_products()}
