"""
Seeds a realistic customer support A/B experiment and runs 60 completions.
Run with: python seed_experiment.py
"""
import requests
import time

B = "http://localhost:8000"

QUESTIONS = [
    "My app keeps crashing when I try to upload a file. What should I do?",
    "I forgot my password and the reset email isn't arriving.",
    "My subscription renewed but I lost access to premium features.",
    "The mobile app is very slow on my iPhone 14.",
    "I can't export my data to CSV — the button does nothing.",
    "I was charged twice for the same order this month.",
    "How do I add a team member to my account?",
    "My integration with Slack stopped working after the latest update.",
    "The search function isn't returning results I know exist.",
    "I need to cancel my account but can't find the option.",
    "My API key is returning 401 Unauthorized even though it's correct.",
    "Videos won't load — stuck on a spinning wheel.",
    "I need an invoice for my company's finance department.",
    "The dark mode setting keeps resetting every time I log in.",
    "Notifications stopped working after I updated the app.",
    "I can't delete old files — the delete button is greyed out.",
    "Two-factor authentication is locking me out of my account.",
    "My data from the old system didn't import correctly.",
    "The dashboard is showing wrong totals for last month.",
    "I get a 'session expired' error every 10 minutes.",
]

# ── Step 1: Create prompt ─────────────────────────────────────────────────────
print("Creating prompt...")
p = requests.post(f"{B}/prompts", json={"name": "customer-support-bot"}).json()
PID = p["id"]
print(f"  prompt id={PID}")

# ── Step 2: Create versions ───────────────────────────────────────────────────
print("Creating version A (generic)...")
va = requests.post(f"{B}/prompts/{PID}/versions", json={
    "system_prompt": (
        "You are a customer support agent. "
        "Answer the following user question helpfully and clearly. "
        "Question: {{question}}"
    ),
    "commit_message": "baseline: generic support prompt",
}).json()
VAID = va["id"]
print(f"  generic version id={VAID}")

print("Creating version B (structured)...")
vb = requests.post(f"{B}/prompts/{PID}/versions", json={
    "system_prompt": (
        "You are a senior customer support engineer. "
        "Structure every response as follows:\n"
        "1. Acknowledge the issue in one sentence.\n"
        "2. Provide numbered troubleshooting steps.\n"
        "3. End with one tip to prevent the issue recurring.\n"
        "Question: {{question}}"
    ),
    "commit_message": "treatment: structured troubleshooting prompt",
}).json()
VBID = vb["id"]
print(f"  structured version id={VBID}")

# ── Step 3: Create & start experiment ─────────────────────────────────────────
print("Creating experiment...")
exp = requests.post(f"{B}/experiments", json={
    "name": "support-bot-generic-vs-structured",
    "prompt_id": PID,
    "primary_metric": "latency_ms",
    "sample_size": 60,
    "owner": "gokul@test.com",
}).json()
EID = exp["id"]
print(f"  experiment id={EID}")

requests.post(f"{B}/experiments/{EID}/variants", json={
    "variant_name": "generic",
    "prompt_version_id": VAID,
    "traffic_percentage": 50,
})
requests.post(f"{B}/experiments/{EID}/variants", json={
    "variant_name": "structured",
    "prompt_version_id": VBID,
    "traffic_percentage": 50,
})

r = requests.put(f"{B}/experiments/{EID}/start").json()
print(f"  start: {r}")

# ── Step 4: Seed 60 completions ───────────────────────────────────────────────
print(f"\nSeeding 60 completions for experiment {EID}...")
for i in range(60):
    question = QUESTIONS[i % len(QUESTIONS)]
    resp = requests.post(f"{B}/experiments/v1/completions", json={
        "prompt_id": PID,
        "user_id": f"support_user_{i}",
        "variables": {"question": question},
    })
    if resp.status_code == 200:
        d = resp.json()
        print(f"  [{i+1:02d}/60] variant={d.get('variant'):<12} latency={d.get('latency_ms')}ms")
    else:
        print(f"  [{i+1:02d}/60] ERROR {resp.status_code}: {resp.text[:80]}")
    time.sleep(0.3)   # gentle pacing to avoid Groq rate-limit errors

print("\nDone. Check results at GET /experiments/{EID}/results")
print(f"Dashboard: select experiment [{EID}] support-bot-generic-vs-structured")
