# Streaming and prompt reuse

## API delivery

The medication-set API uses server-sent events (SSE) for progress and results. In `src/api/app.py`, `_execute_analysis_impl` suppresses provisional provider text and invokes the text callback only after application publication checks and citation rendering. A `token` event therefore does not imply that the browser is receiving individual model tokens.

Progress events describe application stages, not private model reasoning. Provider thinking fields are not user-facing explanations. Follow-ups use a separate non-streaming HTTP response.

SSE comment heartbeats keep connections alive. Reverse proxies must allow streaming; the endpoint supplies `X-Accel-Buffering: no` and `Cache-Control: no-cache`. Errors or interrupted generation must not be interpreted as a completed assessment.

## Provider behavior

Ollama and NVIDIA transport streaming is distinct from API publication. Provider callbacks may produce text internally while the application withholds that text. Doctor mode orders records; the model's ordering output is not a clinical narrative to stream. Other initial audiences can use draft/review generation before final checks. See [reasoning architecture](reasoning_architecture.md).

Templates are loaded within the backend process. Restart after template edits. Each request supplies its own context; do not assume that earlier patient information remains valid for a new request.

## Ollama reuse

`OLLAMA_KEEP_ALIVE` requests model residency between calls. It is not permanent prompt storage or proof of a prompt-cache hit. Eviction, concurrency, model changes, and context changes can require fresh processing.

Inspect returned provider usage and timing where available. Missing cache counters mean unknown, not zero. Measure repeated requests before claiming a latency improvement.

`OLLAMA_NUM_CTX` configures the requested context window; the settings fallback is 8192. `OLLAMA_NUM_PREDICT` defaults to -1 in settings, removing the configured generation-token cap but not the model's context or resource limits. Actual environment values can override both. Choose values supported by the model and host rather than copying a previous experiment's settings.

For optional SSH access to a remote Ollama host, use [start_model_tunnel.ps1](../scripts/start_model_tunnel.ps1). Inspect its parameters and keep the tunnel running. Configure `OLLAMA_HOST` to the local forwarded endpoint and restart the backend. A tunnel is unnecessary for an already reachable local or remote endpoint.
