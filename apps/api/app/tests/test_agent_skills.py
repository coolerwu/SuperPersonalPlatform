from __future__ import annotations


def test_new_agent_has_no_enabled_skills(client) -> None:
    created = client.post("/api/agents", json={"name": "Skills default"})
    assert created.status_code == 201
    assert created.json()["enabled_skills"] == []


def test_agent_enabled_skills_roundtrip(client) -> None:
    created = client.post(
        "/api/agents",
        json={"name": "Skills agent", "enabled_skills": ["demo", "demo", "alpha"]},
    )
    assert created.status_code == 201
    agent = created.json()
    assert agent["enabled_skills"] == ["demo", "alpha"]

    updated = client.put(
        f"/api/agents/{agent['id']}", json={"enabled_skills": ["beta"]}
    )
    assert updated.status_code == 200
    assert updated.json()["enabled_skills"] == ["beta"]

    renamed = client.put(f"/api/agents/{agent['id']}", json={"name": "Renamed"})
    assert renamed.status_code == 200
    assert renamed.json()["enabled_skills"] == ["beta"]

    cleared = client.put(f"/api/agents/{agent['id']}", json={"enabled_skills": []})
    assert cleared.status_code == 200
    assert cleared.json()["enabled_skills"] == []


def test_agent_allows_valid_but_uninstalled_skill_names(client) -> None:
    created = client.post(
        "/api/agents",
        json={"name": "Missing skill", "enabled_skills": ["not-installed"]},
    )
    assert created.status_code == 201
    assert created.json()["enabled_skills"] == ["not-installed"]


def test_agent_rejects_invalid_skill_names(client) -> None:
    for invalid in ("Bad Name", "-demo", "demo-", "a--b", "a" * 65):
        response = client.post(
            "/api/agents", json={"name": "Invalid skill", "enabled_skills": [invalid]}
        )
        assert response.status_code == 422, invalid
