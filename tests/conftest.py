"""Shared fixtures: an isolated SQLite DB per test, ESTV offline by default, row builders."""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.classify import taxonomy  # noqa: E402
from app.features.cashflow import Row  # noqa: E402

LIVE = os.environ.get("ESTV_LIVE") == "1"


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    db.init(tmp_path / "test.db")
    if not LIVE:
        from app.tax import estv

        def offline(op, payload):
            raise estv.EstvError("offline in tests")

        monkeypatch.setattr(estv, "_call", offline)
    yield


class RowFactory:
    def __init__(self):
        self.n = 0

    def __call__(self, d, desc, amount, category, is_foreign=False, balance=None, counterparty=None) -> Row:
        self.n += 1
        return Row(
            id=f"t{self.n:04d}", date=d if isinstance(d, date) else date.fromisoformat(d), description=desc,
            counterparty=counterparty or desc, source_category="Test", amount=amount, balance=balance,
            category=category, kind=taxonomy.kind(category), is_foreign=is_foreign,
        )


@pytest.fixture
def mk():
    return RowFactory()


def monthly_dates(start: str, n: int, day: int = 25) -> list[date]:
    y, m = map(int, start.split("-"))
    out = []
    for _ in range(n):
        out.append(date(y, m, day))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def base_year(mk, salary=6000.0, rent=None, balance_end=20000.0, start="2025-01", months=13, extra=()):
    """A simple salaried year: monthly salary, groceries, optional rent; balance on the last row."""
    rows = []
    for d in monthly_dates(start, months):
        rows.append(mk(d, "Firma AG Lohnzahlung", salary, "salary"))
        rows.append(mk(d + timedelta(days=2), "Coop", -800.0, "groceries"))
        if rent:
            rows.append(mk(d + timedelta(days=3), "Wohnungsmiete", -rent, "rent"))
    rows += list(extra)
    rows.sort(key=lambda r: (r.date, r.id))
    rows[-1].balance = balance_end
    return rows


BASE_PROFILE = {
    "client_id": "t", "display_name": "Test", "occupation": "Employee", "segment": "Mass market", "gender": "female",
    "age": 35, "age_band": "35", "marital_status": "single", "children": 0, "zip": "8001", "city": "Zürich",
    "canton": "ZH", "confession": "none", "employment_type": "employee", "still_working": True,
    "has_pension_fund": True, "is_student": False, "other_assets": 0.0, "salary_net": 72000.0,
    "gross_salary_est": 81818.0, "self_employment_net": 0.0, "pension_income": 0.0, "investment_income": 0.0,
    "earned_income_net": 72000.0, "relationship": "single", "sources": {},
}


@pytest.fixture
def profile():
    return dict(BASE_PROFILE)
