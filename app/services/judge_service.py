import os
import random
import httpx

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_DEFAULT_JUDGE_MODEL = "llama-3.1-8b-instant"

JUDGE_PROMPT = """You are evaluating the quality of an AI assistant response.

Original prompt: {prompt}

Response to evaluate: {response}

Score the response on a scale of 1 to 5:
1 = Very poor (irrelevant, harmful, or completely wrong)
2 = Poor (mostly unhelpful or inaccurate)
3 = Acceptable (partially helpful but missing key points)
4 = Good (helpful and accurate with minor gaps)
5 = Excellent (thorough, accurate, and clearly helpful)

Reply with only the number (1-5). Nothing else."""


def _parse_score(text: str) -> int | None:
    text = text.strip()
    for char in text:
        if char.isdigit() and char in "12345":
            return int(char)
    return None


def _score_with_groq(prompt: str, response: str, model: str) -> int | None:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None
    judge_input = JUDGE_PROMPT.format(prompt=prompt, response=response)
    try:
        result = httpx.post(
            GROQ_API_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": judge_input}],
                "max_tokens": 5,
            },
            timeout=30.0,
        )
        result.raise_for_status()
        text = result.json()["choices"][0]["message"]["content"]
        return _parse_score(text)
    except Exception:
        return None


def _score_with_ollama(prompt: str, response: str, model: str) -> int | None:
    judge_input = JUDGE_PROMPT.format(prompt=prompt, response=response)
    try:
        result = httpx.post(
            "http://localhost:11434/api/generate",
            json={"model": model, "prompt": judge_input, "stream": False},
            timeout=30.0,
        )
        result.raise_for_status()
        return _parse_score(result.json().get("response", ""))
    except Exception:
        return None


def _score_mock(prompt: str, response: str) -> int:
    return random.choices([3, 4, 4, 5, 5], k=1)[0]


def score_response(prompt: str, response: str) -> int | None:
    """
    Call the LLM judge to score a response 1–5.

    Uses the same LLM_PROVIDER env var as the main service.
    Returns None if scoring fails — callers treat None as 'unscored'.
    """
    provider = os.getenv("LLM_PROVIDER", "mock").lower()

    if provider == "groq":
        model = os.getenv("GROQ_JUDGE_MODEL", os.getenv("GROQ_MODEL", GROQ_DEFAULT_JUDGE_MODEL))
        return _score_with_groq(prompt, response, model)

    if provider == "ollama":
        model = os.getenv("OLLAMA_JUDGE_MODEL", os.getenv("OLLAMA_MODEL", "llama3.2"))
        return _score_with_ollama(prompt, response, model)

    return _score_mock(prompt, response)
