import base64
import json
import os
import re
import time
import urllib.error
import urllib.request

API_URL = "https://openrouter.ai/api/v1/chat/completions"
RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524, 529}


class OpenRouterError(Exception):
    pass


def _post(payload: dict, timeout: int) -> dict:
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://rec.bizstudio.it",
            "X-Title": "Bizstudio Recorder Worker",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def _parse_json(text: str):
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def chat_json(models: list[str], messages: list[dict], max_tokens: int, timeout: int = 300):
    """Returns (parsed_json, model_used, cost_usd); tries each model with backoff before falling back."""
    errors = []
    for model in models:
        for attempt in range(3):
            try:
                body = _post(
                    {
                        "model": model,
                        "messages": messages,
                        "max_tokens": max_tokens,
                        "temperature": 0,
                        "response_format": {"type": "json_object"},
                        "usage": {"include": True},
                    },
                    timeout,
                )
                if body.get("error"):
                    raise OpenRouterError(str(body["error"])[:300])
                choice = body["choices"][0]
                content = choice["message"].get("content") or ""
                if choice.get("finish_reason") == "length":
                    raise OpenRouterError("response truncated (max_tokens)")
                cost = float((body.get("usage") or {}).get("cost") or 0)
                return _parse_json(content), model, cost
            except urllib.error.HTTPError as error:
                detail = error.read()[:300].decode(errors="replace")
                errors.append(f"{model}: HTTP {error.code} {detail}")
                if error.code not in RETRYABLE_STATUS:
                    break
            except (urllib.error.URLError, TimeoutError, OpenRouterError, json.JSONDecodeError, KeyError, IndexError) as error:
                errors.append(f"{model}: {type(error).__name__}: {str(error)[:300]}")
            time.sleep(5 * 2**attempt)
    raise OpenRouterError(" | ".join(errors[-4:]))


def audio_message(prompt: str, mp3_path: str) -> list[dict]:
    with open(mp3_path, "rb") as handle:
        data = base64.b64encode(handle.read()).decode()
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "input_audio", "input_audio": {"data": data, "format": "mp3"}},
            ],
        }
    ]
