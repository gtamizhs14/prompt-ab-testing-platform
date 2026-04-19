import os
import time
import random
import httpx
from dataclasses import dataclass

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_DEFAULT_MODEL = "llama-3.1-8b-instant"


@dataclass
class LLMResult:
    response_text: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    is_error: bool


def _fill_template(template: str, variables: dict) -> str:
    for key, value in variables.items():
        template = template.replace(f"{{{{{key}}}}}", str(value))
    return template


def _call_groq(prompt: str, model: str) -> LLMResult:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return LLMResult(
            response_text="GROQ_API_KEY not set in environment.",
            input_tokens=0, output_tokens=0, latency_ms=0, is_error=True,
        )
    start = time.time()
    try:
        response = httpx.post(
            GROQ_API_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()
        latency_ms = int((time.time() - start) * 1000)
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return LLMResult(
            response_text=text,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            latency_ms=latency_ms,
            is_error=False,
        )
    except Exception as e:
        latency_ms = int((time.time() - start) * 1000)
        return LLMResult(
            response_text=str(e),
            input_tokens=0, output_tokens=0, latency_ms=latency_ms, is_error=True,
        )


def _call_ollama(prompt: str, model: str) -> LLMResult:
    start = time.time()
    try:
        response = httpx.post(
            "http://localhost:11434/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()
        latency_ms = int((time.time() - start) * 1000)
        text = data.get("response", "")
        input_tokens = len(prompt) // 4
        output_tokens = len(text) // 4
        return LLMResult(
            response_text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            is_error=False,
        )
    except Exception as e:
        latency_ms = int((time.time() - start) * 1000)
        return LLMResult(
            response_text=str(e),
            input_tokens=0, output_tokens=0, latency_ms=latency_ms, is_error=True,
        )


def _call_mock(prompt: str) -> LLMResult:
    latency_ms = random.randint(200, 800)
    time.sleep(latency_ms / 1000)
    input_tokens = len(prompt) // 4
    output_tokens = random.randint(30, 120)
    return LLMResult(
        response_text=f"[mock response to: {prompt[:60]}...]",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        is_error=False,
    )


def complete(system_prompt: str, variables: dict | None = None) -> LLMResult:
    """
    Fill template variables into the prompt and call the configured LLM provider.

    LLM_PROVIDER options:
      mock   — fake responses, no API calls (default)
      ollama — local Ollama instance (set OLLAMA_MODEL, default: llama3.2)
      groq   — Groq cloud API, free tier (set GROQ_API_KEY, GROQ_MODEL optional)
    """
    filled_prompt = _fill_template(system_prompt, variables or {})
    provider = os.getenv("LLM_PROVIDER", "mock").lower()

    if provider == "groq":
        model = os.getenv("GROQ_MODEL", GROQ_DEFAULT_MODEL)
        return _call_groq(filled_prompt, model)

    if provider == "ollama":
        model = os.getenv("OLLAMA_MODEL", "llama3.2")
        return _call_ollama(filled_prompt, model)

    return _call_mock(filled_prompt)
