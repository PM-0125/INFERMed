# Streaming and prompt reuse

The Ollama analysis path forwards visible response fragments through the medication-set SSE endpoint as `token` events. Provider `thinking` fields are never forwarded. The frontend shows a provisional assessment while generation and final processing run, and replaces it with the final result. Failed or incomplete generation is not a completed assessment. Follow-up responses still use their existing non-streaming HTTP endpoint.

Progress events describe actual application stages, not private model reasoning. SSE comment heartbeats keep connections alive without adding waiting messages to the UI. Reverse proxies must also allow streaming; the endpoint sends `X-Accel-Buffering: no` and `Cache-Control: no-cache`.

Templates are loaded once per backend process. Stable audience instructions and the evidence policy precede changing evidence, history, and patient context. Every request still includes its instructions and its own context; no cross-patient conversation state is reused.

`OLLAMA_KEEP_ALIVE=30m` keeps the model resident between requests when resources permit. This is not permanent prompt storage or a guarantee of a cache hit. Restarts, eviction, concurrency, and prefix changes may require fresh processing. Restart the backend after template edits.

Ollama logs and returned usage include prompt evaluation duration, load duration, prompt token counts, and `prompt_eval_cached_count` when supported. Missing cache counts mean unknown, not zero. Compare repeated requests with the same instruction prefix to measure savings; do not infer savings from keep-alive alone.

The local setup uses the VM through `scripts/start_model_tunnel.ps1 -RemoteHost <host> -RemoteUser <user>` and `OLLAMA_HOST=http://127.0.0.1:11435`. Supply `-IdentityFile <path>` when the default SSH identity location is not appropriate. Keep the SSH tunnel running, then restart the local backend. `OLLAMA_NUM_CTX=131072` is the requested finite context window; `OLLAMA_NUM_PREDICT=-1` removes the application output-token cap, not the model's context or resource limits.
