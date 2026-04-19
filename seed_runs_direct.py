"""
Seeds realistic ExperimentRun data directly into the DB for a clean demo.

Scenario: generic prompt (fast, lower quality) vs structured prompt (slower, higher quality).
- Generic: latency ~N(650, 150ms), quality_score mostly 3-4
- Structured: latency ~N(1150, 180ms), quality_score mostly 4-5

This gives a clear latency winner (generic) AND a clear quality winner (structured),
demonstrating how the primary_metric choice determines who wins.
"""
import random
import sys
import os

# Ensure app package is importable
sys.path.insert(0, os.path.dirname(__file__))

from app.db.database import SessionLocal
from app.db.models import ExperimentRun

random.seed(42)

# ── Config ─────────────────────────────────────────────────────────────────────
EXPERIMENT_ID = 6          # the support-bot experiment we created

GENERIC_LATENCY_MEAN   = 650
GENERIC_LATENCY_STD    = 150
GENERIC_QUALITY_DIST   = [3, 3, 4, 4, 4]   # weighted draw

STRUCTURED_LATENCY_MEAN = 1150
STRUCTURED_LATENCY_STD  = 180
STRUCTURED_QUALITY_DIST = [4, 4, 4, 5, 5]  # higher quality

N_PER_VARIANT = 40

QUESTIONS = [
    "My app keeps crashing when I upload a file.",
    "I forgot my password and the reset email isn't arriving.",
    "My subscription renewed but I lost premium access.",
    "The mobile app is very slow on my iPhone.",
    "I can't export my data to CSV.",
    "I was charged twice this month.",
    "How do I add a team member?",
    "My Slack integration broke after the latest update.",
    "Search isn't returning results I know exist.",
    "I need to cancel my account.",
]

RESPONSES = {
    "generic": "Thank you for reaching out. {action} Please let us know if you need anything else.",
    "structured": (
        "I understand you're experiencing {issue}.\n\n"
        "Here's how to resolve it:\n"
        "1. {step1}\n2. {step2}\n3. {step3}\n\n"
        "Tip: {tip}"
    ),
}


def _latency(mean: float, std: float) -> int:
    return max(200, int(random.gauss(mean, std)))


def _quality(dist: list) -> int:
    return random.choice(dist)


def seed(db):
    runs = []

    for i in range(N_PER_VARIANT):
        question = QUESTIONS[i % len(QUESTIONS)]

        # Generic run
        runs.append(ExperimentRun(
            experiment_id=EXPERIMENT_ID,
            user_id=f"user_g_{i}",
            variant="generic",
            latency_ms=_latency(GENERIC_LATENCY_MEAN, GENERIC_LATENCY_STD),
            input_tokens=random.randint(28, 40),
            output_tokens=random.randint(30, 60),
            is_error=0,
            response_text=RESPONSES["generic"].format(action="Try clearing your cache and re-logging."),
            quality_score=_quality(GENERIC_QUALITY_DIST),
        ))

        # Structured run
        runs.append(ExperimentRun(
            experiment_id=EXPERIMENT_ID,
            user_id=f"user_s_{i}",
            variant="structured",
            latency_ms=_latency(STRUCTURED_LATENCY_MEAN, STRUCTURED_LATENCY_STD),
            input_tokens=random.randint(55, 75),
            output_tokens=random.randint(120, 220),
            is_error=0,
            response_text=RESPONSES["structured"].format(
                issue=question[:40],
                step1="Clear cache and log out",
                step2="Check your account settings",
                step3="Contact support if persists",
                tip="Keep your app updated to avoid this.",
            ),
            quality_score=_quality(STRUCTURED_QUALITY_DIST),
        ))

    db.bulk_save_objects(runs)
    db.commit()
    print(f"Inserted {len(runs)} runs into experiment {EXPERIMENT_ID}")


if __name__ == "__main__":
    db = SessionLocal()
    try:
        seed(db)
    finally:
        db.close()

    # Quick stats summary
    import requests
    r = requests.get(f"http://localhost:8000/experiments/{EXPERIMENT_ID}/results").json()
    sig = r.get("significance") or {}
    vm = r.get("variant_metrics", {})
    print(f"\nResults for experiment {EXPERIMENT_ID}:")
    print(f"  Verdict:  {r.get('verdict')}")
    print(f"  Winner:   {sig.get('winner') or r.get('winner') or '—'}")
    print(f"  p-value:  {sig.get('p_value')}")
    print(f"  MDE:      {sig.get('mde')}")
    ci = sig.get("confidence_interval") or {}
    print(f"  95% CI:   [{ci.get('lower')}, {ci.get('upper')}]  diff={ci.get('difference')}")
    for v, m in vm.items():
        print(f"  {v:<12}  n={m['sample_count']}  latency={m['latency_ms']['mean']}ms  quality={m['quality_score']['mean']}")
