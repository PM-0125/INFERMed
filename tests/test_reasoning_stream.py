import json
import pytest

from fastapi.testclient import TestClient
from src.api.app import app


@pytest.fixture(autouse=True)
def inline_worker_boundary(monkeypatch):
    # Keep the real API/publication pipeline with local fake source dependencies;
    # process lifecycle has separate tests with actual spawned workers.
    monkeypatch.setattr(
        "src.api.app.run_isolated",
        lambda function, argument, scope, **kw: function(argument, **kw),
    )


def events(response):
    return [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def test_stream_withholds_draft_tokens_and_never_succeeds_on_failure(monkeypatch):
    monkeypatch.setattr("src.api.app._retrieve_pair_context", lambda *a, **k: {})

    def fail(ctx, mode, **kwargs):
        assert ctx["reasoning_input"]["decision"]
        kwargs["on_text"]("Partial draft")
        raise RuntimeError("Provider disconnected")

    monkeypatch.setattr("src.api.app._generate_final_answer", fail)
    response = TestClient(app).post(
        "/api/medication-sets/analyze/stream",
        json={"medications": [{"text": "a"}, {"text": "b"}], "audience": "doctor"},
    )
    assert response.status_code == 200
    rows = events(response)
    assert not any(r["type"] == "token" for r in rows)
    assert rows[-1]["type"] == "error"
    assert not any(r["type"] == "result" for r in rows)


def test_stream_final_result_includes_review_status(monkeypatch):
    monkeypatch.setattr("src.api.app._retrieve_pair_context", lambda *a, **k: {})

    def answer(ctx, mode, **kwargs):
        kwargs["on_text"]("Evidence is insufficient.")
        return {"text": "Evidence is insufficient."}

    monkeypatch.setattr("src.api.app._generate_final_answer", answer)
    rows = events(
        TestClient(app).post(
            "/api/medication-sets/analyze/stream",
            json={"medications": [{"text": "a"}, {"text": "b"}], "audience": "doctor"},
        )
    )
    stages = [r.get("stage") for r in rows]
    assert stages.index("reasoning_ready") < stages.index("answer_generation_started")
    assert rows[-1]["type"] == "result"
    assert rows[-1]["result"]["verification"]["status"] == "review_required"


def test_stream_does_not_publish_unsupported_advice_before_final_checks(monkeypatch):
    monkeypatch.setattr("src.api.app._retrieve_pair_context", lambda *a, **k: {})

    def answer(ctx, mode, **kwargs):
        kwargs["on_text"]("Check INR twice weekly. [ev_fake]")
        return {"text": "## Monitoring & Actions\nCheck INR twice weekly. [ev_fake]"}

    monkeypatch.setattr("src.api.app._generate_final_answer", answer)
    rows = events(
        TestClient(app).post(
            "/api/medication-sets/analyze/stream",
            json={"medications": [{"text": "a"}, {"text": "b"}], "audience": "doctor"},
        )
    )
    assert not any("twice weekly" in r.get("text", "") for r in rows)
    assert "twice weekly" not in str(rows[-1]["result"]["assessment"])
    assert (
        rows[-1]["result"]["verification"]["publication"]["status"]
        == "recommendations_withheld"
    )
