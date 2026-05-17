from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import requests

from src.config.settings import get_settings
from src.llm.llm_interface import _nvidia_chat_completions_url


def main() -> int:
    parser = argparse.ArgumentParser(description="Check NVIDIA chat-completions connectivity without printing secrets.")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--max-tokens", type=int, default=32)
    parser.add_argument("--reasoning-effort", choices=["low", "medium", "high"], default="low")
    parser.add_argument("--stream", action="store_true", help="Use NVIDIA/OpenAI SSE streaming.")
    args = parser.parse_args()

    settings = get_settings()
    endpoint = _nvidia_chat_completions_url(settings.nvidia_base_url)

    print(f"provider={settings.llm_provider}")
    print(f"endpoint={endpoint}")
    print(f"model={settings.nvidia_model or '<missing>'}")
    print(f"has_key={bool(settings.nvidia_api_key)}")
    print(f"stream={str(args.stream).lower()}")
    print(f"reasoning_effort={args.reasoning_effort}")

    if not settings.nvidia_api_key or not settings.nvidia_model:
        print("result=configuration_error")
        return 2

    payload = {
        "model": settings.nvidia_model,
        "messages": [{"role": "user", "content": "Reply with exactly this word: OK"}],
        "temperature": settings.llm_temperature,
        "top_p": settings.llm_top_p,
        "max_tokens": args.max_tokens,
        "stream": args.stream,
        "reasoning_effort": args.reasoning_effort,
    }

    try:
        response = requests.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {settings.nvidia_api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream" if args.stream else "application/json",
            },
            json=payload,
            stream=args.stream,
            timeout=args.timeout,
        )
    except requests.exceptions.Timeout:
        print(f"result=timeout_after_{args.timeout:g}s")
        return 3
    except requests.exceptions.RequestException as exc:
        print(f"result=request_error")
        print(f"error={exc}")
        return 4

    print(f"status={response.status_code}")
    print(f"content_type={response.headers.get('content-type', '')}")

    if response.status_code != 200:
        preview = response.text.replace("\n", " ").strip()[:300]
        print(f"result=http_error")
        print(f"body_preview={preview}")
        return 5

    if args.stream:
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        for raw_line in response.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            line = str(raw_line).strip()
            if not line.startswith("data:"):
                continue
            chunk = line[len("data:"):].strip()
            if chunk == "[DONE]":
                break
            try:
                data = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            for choice in data.get("choices", []) or []:
                delta = choice.get("delta") or {}
                if delta.get("reasoning_content"):
                    reasoning_parts.append(str(delta["reasoning_content"]))
                if delta.get("content"):
                    content_parts.append(str(delta["content"]))
        content = "".join(content_parts).strip()
        reasoning = "".join(reasoning_parts).strip()
        print("result=ok")
        print(f"content_preview={content[:200]}")
        print(f"reasoning_preview={reasoning[:200]}")
        return 0 if content or reasoning else 6

    data = response.json()
    choices = data.get("choices") or []
    message = (choices[0].get("message") or {}) if choices else {}
    content = str(message.get("content") or "").strip()
    print(f"result=ok")
    print(f"content_preview={content[:200]}")
    print(f"finish_reason={(choices[0] or {}).get('finish_reason') if choices else ''}")
    print("usage=" + json.dumps(data.get("usage", {}), ensure_ascii=False))
    return 0 if content else 6


if __name__ == "__main__":
    raise SystemExit(main())
