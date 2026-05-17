from fastapi.testclient import TestClient

from src.api.app import app


def test_health_endpoint():
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_analyze_endpoint_uses_uncached_llm_response(monkeypatch):
    calls = {}

    def fake_run_rag(*args, **kwargs):
        calls["args"] = args
        calls["kwargs"] = kwargs
        return {
            "context": {
                "drugs": {"a": {"name": "warfarin"}, "b": {"name": "fluconazole"}},
                "signals": {"tabular": {"prr": 2.2}, "faers": {}, "mechanistic": {}},
                "pkpd": {"pk_detail": {"overlaps": {}}, "pd_detail": {}},
            },
            "answer": {"text": "## Bottom Line\nUse caution."},
        }

    monkeypatch.setattr("src.api.app.run_rag", fake_run_rag)
    client = TestClient(app)
    response = client.post(
        "/api/interactions/analyze",
        json={"drugs": [" warfarin ", "fluconazole"], "mode": "doctor", "refreshEvidence": False},
    )

    assert response.status_code == 200
    assert calls["args"][:2] == ("warfarin", "fluconazole")
    assert calls["kwargs"]["mode"] == "doctor"
    assert calls["kwargs"]["use_cache_context"] is True
    assert calls["kwargs"]["use_cache_response"] is False
    assert calls["kwargs"]["model_name"] is None
    assert response.json()["assessment"][0]["title"] == "Bottom Line"


def test_followup_endpoint_uses_scoped_followup_prompt(monkeypatch):
    calls = {}

    def fake_get_context_cached(drug_a, drug_b):
        return (
            {
                "drugs": {"a": {"name": drug_a}, "b": {"name": drug_b}},
                "signals": {"mechanistic": {"targets_a": ["x"]}},
                "sources": {"openfda": ["FAERS"], "duckdb": ["TwoSides"]},
            },
            "warfarin_fluconazole",
        )

    def fake_generate_followup_response(context, mode, **kwargs):
        calls["mode"] = mode
        calls["history"] = kwargs["history"]
        calls["question"] = kwargs["question"]
        calls["prior_assessment"] = kwargs["prior_assessment"]
        calls["model_name"] = kwargs["model_name"]
        return {"text": "Follow-up answer"}

    monkeypatch.setattr("src.api.app.get_context_cached", fake_get_context_cached)
    monkeypatch.setattr("src.api.app.generate_followup_response", fake_generate_followup_response)
    client = TestClient(app)
    response = client.post(
        "/api/interactions/followup",
        json={"drugs": ["warfarin", "fluconazole"], "mode": "pv_research", "question": "Renal impairment?"},
    )

    assert response.status_code == 200
    assert calls["mode"] == "pv_research"
    assert calls["history"] == []
    assert calls["question"] == "Renal impairment?"
    assert calls["prior_assessment"] is None
    assert calls["model_name"] is None
    assert response.json()["answer"] == "Follow-up answer"
