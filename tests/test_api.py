"""
Integration tests for the Prompt A/B Testing Platform.

Uses a SQLite in-memory database so no running PostgreSQL is needed.
Each test gets a fresh database via the reset_db autouse fixture.
"""
import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def create_prompt(client, name="test-prompt"):
    return client.post("/prompts", json={"name": name}).json()


def create_version(client, prompt_id, system_prompt="Say hello. {{name}}", commit="v1"):
    return client.post(f"/prompts/{prompt_id}/versions", json={
        "system_prompt": system_prompt,
        "commit_message": commit,
    }).json()


def create_experiment(client, prompt_id, metric="latency_ms"):
    return client.post("/experiments", json={
        "name": "test-exp",
        "prompt_id": prompt_id,
        "primary_metric": metric,
        "sample_size": 30,
        "owner": "test@test.com",
    }).json()


def add_variant(client, exp_id, name, version_id, traffic):
    return client.post(f"/experiments/{exp_id}/variants", json={
        "variant_name": name,
        "prompt_version_id": version_id,
        "traffic_percentage": traffic,
    })


# ── Prompt versioning tests ───────────────────────────────────────────────────

def test_create_prompt(client):
    r = client.post("/prompts", json={"name": "my-prompt"})
    assert r.status_code == 200
    assert r.json()["name"] == "my-prompt"
    assert "id" in r.json()


def test_create_first_version_returns_id(client):
    p = create_prompt(client)
    r = client.post(f"/prompts/{p['id']}/versions", json={
        "system_prompt": "Hello {{name}}",
        "commit_message": "initial",
    })
    assert r.status_code == 200
    data = r.json()
    assert "id" in data
    assert data["version"] == 1


def test_version_numbers_increment(client):
    p = create_prompt(client)
    v1 = create_version(client, p["id"], commit="first")
    v2 = create_version(client, p["id"], commit="second")
    assert v1["version"] == 1
    assert v2["version"] == 2


def test_first_version_auto_activates(client):
    p = create_prompt(client)
    v = create_version(client, p["id"])
    prompt = client.get(f"/prompts/{p['id']}/versions").json()
    assert len(prompt) == 1
    assert prompt[0]["id"] == v["id"]


def test_activate_version(client):
    p = create_prompt(client)
    v1 = create_version(client, p["id"], commit="v1")
    v2 = create_version(client, p["id"], commit="v2")
    r = client.put(f"/prompts/{p['id']}/activate/{v2['id']}", params={"actor": "tester"})
    assert r.status_code == 200
    assert r.json()["active_version_id"] == v2["id"]


def test_activate_wrong_prompt_returns_404(client):
    p = create_prompt(client)
    v = create_version(client, p["id"])
    r = client.put(f"/prompts/999/activate/{v['id']}")
    assert r.status_code == 404


def test_diff_versions(client):
    p = create_prompt(client)
    v1 = create_version(client, p["id"], system_prompt="Be concise. {{q}}", commit="v1")
    v2 = create_version(client, p["id"], system_prompt="Be detailed. {{q}}", commit="v2")
    r = client.get(f"/prompts/{p['id']}/diff/{v1['id']}/{v2['id']}")
    assert r.status_code == 200
    diff = r.json()["diff"]
    assert "system_prompt" in diff


def test_compare_versions_missing_variable_returns_422(client):
    p = create_prompt(client)
    v1 = create_version(client, p["id"], system_prompt="Hello {{name}}")
    v2 = create_version(client, p["id"], system_prompt="Hi {{name}}")
    r = client.post(f"/prompts/{p['id']}/compare", json={
        "version_a_id": v1["id"],
        "version_b_id": v2["id"],
        "variables": {},  # missing 'name'
    })
    assert r.status_code == 422


def test_compare_versions_success(client):
    p = create_prompt(client)
    v1 = create_version(client, p["id"], system_prompt="Say hi. {{name}}")
    v2 = create_version(client, p["id"], system_prompt="Say hello. {{name}}")
    r = client.post(f"/prompts/{p['id']}/compare", json={
        "version_a_id": v1["id"],
        "version_b_id": v2["id"],
        "variables": {"name": "Alice"},
    })
    assert r.status_code == 200
    data = r.json()
    assert "version_a" in data
    assert "version_b" in data


# ── Experiment CRUD tests ─────────────────────────────────────────────────────

def test_create_experiment_invalid_metric(client):
    p = create_prompt(client)
    r = client.post("/experiments", json={
        "name": "test", "prompt_id": p["id"], "primary_metric": "invalid_metric"
    })
    assert r.status_code == 400


def test_create_experiment_draft_status(client):
    p = create_prompt(client)
    exp = create_experiment(client, p["id"])
    assert exp["status"] == "draft"
    assert exp["winner"] is None


def test_start_experiment_needs_two_variants(client):
    p = create_prompt(client)
    v = create_version(client, p["id"])
    exp = create_experiment(client, p["id"])
    add_variant(client, exp["id"], "A", v["id"], 100)
    r = client.put(f"/experiments/{exp['id']}/start")
    assert r.status_code == 400
    assert "2 variants" in r.json()["detail"]


def test_start_experiment_traffic_must_sum_to_100(client):
    p = create_prompt(client)
    v1 = create_version(client, p["id"], commit="v1")
    v2 = create_version(client, p["id"], commit="v2")
    exp = create_experiment(client, p["id"])
    add_variant(client, exp["id"], "A", v1["id"], 60)
    add_variant(client, exp["id"], "B", v2["id"], 60)  # 120 total
    r = client.put(f"/experiments/{exp['id']}/start")
    assert r.status_code == 400
    assert "100" in r.json()["detail"]


def test_start_experiment_success(client):
    p = create_prompt(client)
    v1 = create_version(client, p["id"], commit="v1")
    v2 = create_version(client, p["id"], commit="v2")
    exp = create_experiment(client, p["id"])
    add_variant(client, exp["id"], "A", v1["id"], 50)
    add_variant(client, exp["id"], "B", v2["id"], 50)
    r = client.put(f"/experiments/{exp['id']}/start")
    assert r.status_code == 200
    assert r.json()["status"] == "running"


def test_stop_experiment(client):
    p = create_prompt(client)
    v1 = create_version(client, p["id"], commit="v1")
    v2 = create_version(client, p["id"], commit="v2")
    exp = create_experiment(client, p["id"])
    add_variant(client, exp["id"], "A", v1["id"], 50)
    add_variant(client, exp["id"], "B", v2["id"], 50)
    client.put(f"/experiments/{exp['id']}/start")
    r = client.put(f"/experiments/{exp['id']}/stop")
    assert r.status_code == 200
    assert r.json()["status"] == "completed"


def test_serve_completion_no_active_version_returns_404(client):
    p = create_prompt(client)
    r = client.post("/experiments/v1/completions", json={
        "prompt_id": p["id"], "user_id": "user1", "variables": {}
    })
    assert r.status_code == 404


def test_serve_completion_missing_variable_returns_422(client):
    p = create_prompt(client)
    create_version(client, p["id"], system_prompt="Hello {{name}}")
    r = client.post("/experiments/v1/completions", json={
        "prompt_id": p["id"], "user_id": "user1", "variables": {}
    })
    assert r.status_code == 422


def test_serve_completion_success(client):
    p = create_prompt(client)
    create_version(client, p["id"], system_prompt="Hello {{name}}")
    r = client.post("/experiments/v1/completions", json={
        "prompt_id": p["id"], "user_id": "user1", "variables": {"name": "Alice"}
    })
    assert r.status_code == 200
    assert "response" in r.json()


def test_promote_winner_without_winner_returns_400(client):
    p = create_prompt(client)
    v1 = create_version(client, p["id"], commit="v1")
    v2 = create_version(client, p["id"], commit="v2")
    exp = create_experiment(client, p["id"])
    add_variant(client, exp["id"], "A", v1["id"], 50)
    add_variant(client, exp["id"], "B", v2["id"], 50)
    client.put(f"/experiments/{exp['id']}/start")
    r = client.post(f"/experiments/{exp['id']}/promote-winner")
    assert r.status_code == 400
    assert "No winner" in r.json()["detail"]


def test_results_returns_insufficient_data_when_few_samples(client):
    p = create_prompt(client)
    v1 = create_version(client, p["id"], commit="v1")
    v2 = create_version(client, p["id"], commit="v2")
    exp = create_experiment(client, p["id"])
    add_variant(client, exp["id"], "A", v1["id"], 50)
    add_variant(client, exp["id"], "B", v2["id"], 50)
    client.put(f"/experiments/{exp['id']}/start")
    r = client.get(f"/experiments/{exp['id']}/results")
    assert r.status_code == 200
    assert r.json()["verdict"] == "insufficient_data"
