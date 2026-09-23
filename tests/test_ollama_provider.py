import json

import pytest

from src.llm.llm_interface import generate_response, generate_followup_response
from src.llm.llm_interface import build_prompt


@pytest.mark.parametrize('mode', ['Doctor', 'Patient', 'Pharma'])
def test_prompt_prefix_is_stable_and_does_not_mix_patients(mode):
    first = build_prompt({'query': 'PATIENT_ONE_UNIQUE'}, mode)
    second = build_prompt({'query': 'PATIENT_TWO_UNIQUE'}, mode)
    assert first.split('[POLICY]')[0] == second.split('[POLICY]')[0]
    assert first.index('[POLICY]') < first.index('PATIENT_ONE_UNIQUE')
    assert 'PATIENT_TWO_UNIQUE' not in first
    assert 'PATIENT_ONE_UNIQUE' not in second
    assert 'Do NOT propose specific numeric dose changes' in first


@pytest.mark.parametrize('followup', [False, True])
@pytest.mark.parametrize('failure', ['', 'length', 'empty', 'disconnect'])
def test_ollama_stream_configuration_and_completion(monkeypatch, followup, failure):
    for key, value in {
        'INFERMED_SKIP_DOTENV': 'true', 'LLM_PROVIDER': 'ollama',
        'OLLAMA_HOST': 'http://127.0.0.1:11435', 'OLLAMA_MODEL': 'gpt-oss:20b',
        'OLLAMA_REASONING_EFFORT': 'medium', 'OLLAMA_NUM_CTX': '131072',
        'OLLAMA_NUM_PREDICT': '-1', 'LLM_STREAM': 'true',
        'LLM_TEMPERATURE': '0.7', 'LLM_TOP_P': '0.9',
    }.items():
        monkeypatch.setenv(key, value)
    captured = {}

    class Response:
        status_code = 200
        closed = False

        def iter_lines(self, **kwargs):
            yield json.dumps({'thinking': 'PRIVATE REASONING'}).encode()
            if failure != 'empty':
                yield json.dumps({'response': 'Evidence is insufficient.'}).encode()
            if failure != 'disconnect':
                yield json.dumps({'done': True, 'done_reason': 'length' if failure == 'length' else 'stop',
                                  'eval_count': 42, 'prompt_eval_count': 100}).encode()

        def close(self):
            self.closed = True

    response = Response()

    def post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return response

    monkeypatch.setattr('requests.post', post)
    chunks = []
    if followup:
        out = generate_followup_response({}, 'Doctor', question='What is missing?')
    else:
        out = generate_response({}, 'Doctor', on_text=chunks.append)
        assert chunks == ([] if failure == 'empty' else ['Evidence is insufficient.'])
    assert captured['url'] == 'http://127.0.0.1:11435/api/generate'
    assert captured['json']['think'] == 'medium'
    assert captured['json']['options']['num_ctx'] == 131072
    assert captured['json']['options']['temperature'] == 0.7
    assert captured['json']['options']['num_predict'] == -1
    assert captured['stream'] is True
    assert captured['json']['keep_alive'] == '30m'
    assert response.closed
    assert 'PRIVATE REASONING' not in out['text']
    if failure:
        assert out['meta']['error'] is True
    else:
        assert out['meta']['provider'] == 'ollama'
        assert out['usage']['eval_count'] == 42
        assert 'Evidence is insufficient.' in out['text']
