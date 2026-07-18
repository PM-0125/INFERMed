from fastapi.testclient import TestClient

from src.api.app import app


def test_health_endpoint():
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_analyze_endpoint_uses_uncached_llm_response(monkeypatch):
    calls = {}

    def fake_retrieve_pair_context(*args, **kwargs):
        calls.setdefault("contexts", []).append({"args": args, "kwargs": kwargs})
        return {
            "drugs": {"a": {"name": args[0]}, "b": {"name": args[1]}},
            "signals": {"tabular": {"prr": 2.2}, "faers": {}, "mechanistic": {}},
            "pkpd": {"pk_detail": {"overlaps": {}}, "pd_detail": {}},
        }

    def fake_generate_final_answer(context, mode, **kwargs):
        calls["answer_context"] = context
        calls["answer_mode"] = mode
        return {"text": "## Bottom Line\nUse caution."}

    monkeypatch.setattr("src.api.app._retrieve_pair_context", fake_retrieve_pair_context)
    monkeypatch.setattr("src.api.app._generate_final_answer", fake_generate_final_answer)
    client = TestClient(app)
    response = client.post(
        "/api/interactions/analyze",
        json={"drugs": [" warfarin ", "fluconazole"], "mode": "doctor", "refreshEvidence": False},
    )

    assert response.status_code == 200
    assert calls["contexts"][0]["args"][:2] == ("warfarin", "fluconazole")
    assert calls["contexts"][0]["kwargs"]["mode"] == "doctor"
    assert calls["contexts"][0]["kwargs"]["force_refresh"] is False
    assert calls["answer_mode"] == "doctor"
    assert response.json()["assessment"][0]["title"] == "Bottom Line"
    assert response.json()["analysisId"].startswith("ana_")
    assert response.json()["decision"]["analysis_id"] == response.json()["analysisId"]


def test_medication_set_endpoint_returns_future_read_model(monkeypatch):
    def fake_retrieve_pair_context(*args, **kwargs):
        return {
            "drugs": {"a": {"name": args[0]}, "b": {"name": args[1]}},
            "signals": {"tabular": {"prr": 2.5}, "faers": {}, "mechanistic": {}},
            "pkpd": {"pk_detail": {"overlaps": {}}, "pd_detail": {}},
        }

    monkeypatch.setattr("src.api.app._retrieve_pair_context", fake_retrieve_pair_context)
    monkeypatch.setattr("src.api.app._generate_final_answer", lambda context, mode, **kwargs: {"text": "## Bottom Line\nUse caution."})
    client = TestClient(app)
    response = client.post(
        "/api/medication-sets/analyze",
        json={
            "medications": [{"text": "warfarin"}, {"text": "fluconazole"}],
            "audience": "doctor",
            "refresh_evidence": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["analysisId"].startswith("ana_")
    assert payload["assessment"][0]["title"] == "Bottom Line"
    assert payload["risk"]["label"]
    assert payload["drugs"][0]["name"] == "warfarin"
    assert "overview" in payload["evidence"]
    assert payload["normalizedMedications"][0]["normalized_name"] == "warfarin"
    assert payload["topRisks"][0]["analysis_id"] == payload["analysisId"]
    assert "compatibility" in payload


def test_medication_set_stream_returns_progress_and_result(monkeypatch):
    def fake_retrieve_pair_context(*args, **kwargs):
        return {
            "drugs": {"a": {"name": args[0]}, "b": {"name": args[1]}},
            "signals": {"tabular": {"prr": 2.5}, "faers": {}, "mechanistic": {}},
            "pkpd": {"pk_detail": {"overlaps": {}}, "pd_detail": {}},
        }

    monkeypatch.setattr("src.api.app._retrieve_pair_context", fake_retrieve_pair_context)
    monkeypatch.setattr("src.api.app._generate_final_answer", lambda context, mode, **kwargs: {"text": "## Bottom Line\nUse caution."})
    client = TestClient(app)
    response = client.post(
        "/api/medication-sets/analyze/stream",
        json={
            "medications": [{"text": "warfarin"}, {"text": "fluconazole"}, {"text": "ibuprofen"}],
            "audience": "doctor",
            "refresh_evidence": False,
        },
    )

    assert response.status_code == 200
    text = response.text
    assert '"type": "progress"' in text
    assert '"type": "result"' in text
    assert '"assessment"' in text
    assert '"risk"' in text
    assert '"evidence"' in text
    assert '"pair_count": 3' in text


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
