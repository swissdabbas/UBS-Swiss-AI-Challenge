"""HTTP smoke test of the whole client journey (rules mode, ESTV offline)."""

import time

from fastapi.testclient import TestClient

from app.main import app
from app.suitability import questionnaire

client = TestClient(app)


def wait(job_id):
    for _ in range(200):
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] != "running":
            assert j["status"] == "done", j
            return j
        time.sleep(0.05)
    raise AssertionError("job timed out")


def test_client_journey():
    assert client.post("/api/mode", json={"mode": "off"}).status_code == 200
    assert len(client.get("/api/clients").json()) >= 16
    cid = "persona5"
    wait(client.post(f"/api/clients/{cid}/analyse").json()["job_id"])
    a = client.get(f"/api/clients/{cid}/analysis").json()
    assert a["signals"] and a["tax_plan"]["eligible"]

    tx = client.get(f"/api/clients/{cid}/transactions").json()["transactions"]
    assert client.post(f"/api/clients/{cid}/transactions/{tx[0]['id']}/override", json={"category": "other"}).status_code == 200
    assert client.post(f"/api/clients/{cid}/transactions/{tx[0]['id']}/override", json={"category": "bogus"}).status_code == 400

    # recommendations are refused before the questionnaire and acknowledgement
    j = client.post(f"/api/clients/{cid}/recommend").json()["job_id"]
    while (s := client.get(f"/api/jobs/{j}").json())["status"] == "running":
        time.sleep(0.05)
    assert s["status"] == "error"

    demo = questionnaire.demo_answers(a["profile"], {x["type"] for x in a["signals"]})
    wait(client.post(f"/api/clients/{cid}/suitability", json={"profile": {"canton": "ZH"}, **demo}).json()["job_id"])
    assert client.post(f"/api/clients/{cid}/acknowledge").status_code == 200
    wait(client.post(f"/api/clients/{cid}/recommend").json()["job_id"])
    r = client.get(f"/api/clients/{cid}/recommendations").json()
    assert r["baskets"] and r["acknowledgement"]["acknowledged_at"]

    pdf = client.get(f"/api/clients/{cid}/report.pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    audit = client.get(f"/api/clients/{cid}/audit").json()
    kinds = {e["kind"] for e in audit["events"]}
    assert {"analysis_completed", "finsa_acknowledged", "recommendation_completed", "advice_record_generated"} <= kinds


def test_upload_validates_schema():
    bad = client.post("/api/clients/upload", files={"file": ("x.csv", b"date,amount\n2026-01-01,5\n", "text/csv")})
    assert bad.status_code == 422 and "missing column" in bad.json()["detail"]
