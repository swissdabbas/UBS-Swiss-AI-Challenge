import csv

import pytest

from app.ingest import loader
from app.ingest.loader import IngestError, normalize_counterparty, parse_transactions, profile_from_filename

HEADER = "date,description,category,amount,currency,balance\n"


def test_all_personas_load_and_validate():
    files = loader.persona_files()
    assert len(files) == 16
    for f in files:
        profile, txns = loader.load_client(profile_from_filename(f).client_id)
        with open(f, encoding="utf-8") as fh:
            assert len(txns) == sum(1 for _ in csv.DictReader(fh))
        assert len({t.id for t in txns}) == len(txns), "transaction ids must be unique"
        assert all(t.currency == "CHF" and not t.is_fx for t in txns)


def test_missing_column_fails_loudly():
    with pytest.raises(IngestError, match="missing column\\(s\\) balance"):
        parse_transactions("date,description,category,amount,currency\n2026-01-01,Coop,Groceries,-5,CHF\n", "x", "x.csv")


def test_bad_amount_reports_row_number():
    with pytest.raises(IngestError, match="x.csv row 3"):
        parse_transactions(HEADER + "2026-01-01,Coop,Groceries,-5,CHF,10\n2026-01-02,Coop,Groceries,abc,CHF,5\n", "x", "x.csv")


def test_swiss_number_and_date_formats():
    t = parse_transactions(HEADER + "05.03.2026,Lohn,Income,1'234.50,,9'000\n", "x", "x.csv")[0]
    assert t.amount == 1234.5 and t.balance == 9000 and t.date.isoformat() == "2026-03-05" and t.currency == "CHF"


def test_fx_flag_from_currency_column():
    t = parse_transactions(HEADER + "2026-01-01,Hotel Paris,Travel,-100,EUR,50\n", "x", "x.csv")[0]
    assert t.is_fx


@pytest.mark.parametrize("raw,expected", [
    ("MIGROS ZH 1234", "Migros"),
    ("Starbucks Zürich", "Starbucks"),
    ("COOP 4411", "Coop"),
    ("Migros Reisen Pauschalreise", "Migros Reisen Pauschalreise"),
    ("Kundenzahlung - Webdesign", "Kundenzahlung - Webdesign"),
])
def test_counterparty_normalisation(raw, expected):
    assert normalize_counterparty(raw) == expected


@pytest.mark.parametrize("name,age,band,est", [
    ("persona3_CHF_25-34_male_swengineer_married.csv", 29, "25-34", True),
    ("persona11_CHF_24_female_lownetworth_debt_single.csv", 24, "24", False),
    ("persona9_CHF_65plus_male_retired_widowed.csv", 68, "65+", True),
])
def test_profile_from_filename(tmp_path, name, age, band, est):
    p = profile_from_filename(tmp_path / name)
    assert (p.age, p.age_band, p.age_estimated) == (age, band, est)


def test_bad_filename_is_rejected(tmp_path):
    with pytest.raises(IngestError):
        profile_from_filename(tmp_path / "clients.csv")


def test_product_catalog_matches_config():
    products = loader.load_products()
    assert len(products) == 55
    assert len({p.id for p in products}) == 55
    for p in products:
        if p.returns:
            assert p.returns.low <= p.returns.medium <= p.returns.high


def test_product_without_config_fails():
    rows = [{c: "x" for c in loader.PRODUCT_COLUMNS} | {"Product Name": "Brand New Card"}]
    with pytest.raises(IngestError, match="no entry in config/products.yaml"):
        loader.parse_products(rows, {"products": {}}, "p.csv")
