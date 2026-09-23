"""Pre-compute the offline tax fallback table from the ESTV calculator.

For each canton capital, gross salary level and household type, ESTV computes the
tax saved by a CHF 7'258 Pillar 3a contribution. The app interpolates this table
when ESTV is unreachable ("rough estimate based on canton").

    .venv/bin/python scripts/build_tax_tables.py            # writes config/tax_tables.json
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, db  # noqa: E402
from app.tax import estv  # noqa: E402

CAPITALS = {
    "ZH": ("8001", "Zürich"), "BE": ("3011", "Bern"), "LU": ("6003", "Luzern"), "UR": ("6460", "Altdorf"),
    "SZ": ("6430", "Schwyz"), "OW": ("6060", "Sarnen"), "NW": ("6370", "Stans"), "GL": ("8750", "Glarus"),
    "ZG": ("6300", "Zug"), "FR": ("1700", "Fribourg"), "SO": ("4500", "Solothurn"), "BS": ("4051", "Basel"),
    "BL": ("4410", "Liestal"), "SH": ("8200", "Schaffhausen"), "AR": ("9100", "Herisau"), "AI": ("9050", "Appenzell"),
    "SG": ("9000", "St. Gallen"), "GR": ("7000", "Chur"), "AG": ("5000", "Aarau"), "TG": ("8500", "Frauenfeld"),
    "TI": ("6500", "Bellinzona"), "VD": ("1003", "Lausanne"), "VS": ("1950", "Sion"), "NE": ("2000", "Neuchâtel"),
    "GE": ("1204", "Genève"), "JU": ("2800", "Delémont"),
}
GROSS = [40000, 60000, 80000, 100000, 150000, 250000, 400000]
CONTRIB = 7258


def main() -> None:
    db.init()
    year = int(config.settings()["pillar3a"]["tax_year"])
    table: dict = {"tax_year": year, "contribution": CONTRIB, "gross_incomes": GROSS, "source": "ESTV swisstaxcalculator",
                   "cantons": {}}
    for canton, (zip_code, city) in CAPITALS.items():
        entry = {"zip": zip_code, "city": city, "single": [], "married": []}
        for rel in ("single", "married"):
            for g in GROSS:
                p = {"employment_type": "employee", "gross_salary_est": g, "self_employment_net": 0, "pension_income": 0,
                     "earned_income_net": g, "relationship": rel, "confession": "none", "children": 0, "age": 40,
                     "zip": zip_code, "city": city}
                try:
                    r = estv.taxes_for_3a(p, [0, CONTRIB], year)
                    saved = r["taxes"][0]["total"] - r["taxes"][CONTRIB]["total"]
                except estv.EstvError as exc:
                    print(f"  {canton} {rel} {g}: {exc}")
                    saved = None
                entry[rel].append(round(saved / CONTRIB, 4) if saved is not None else None)
                time.sleep(0.05)
        table["cantons"][canton] = entry
        print(canton, entry["single"], entry["married"], flush=True)
    out = config.path(config.settings()["tax"]["fallback_tables"])
    out.write_text(json.dumps(table, indent=1, ensure_ascii=False), encoding="utf-8")
    print("wrote", out)


if __name__ == "__main__":
    main()
