from __future__ import annotations


def test_new_agent_has_no_enabled_skills(client) -> None:
    created = client.post("/api/agents", json={"name": "Skills default"})
    assert created.status_code == 201
    assert created.json()["enabled_skills"] == []
    assert created.json()["enabled_tools"] == []


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


def test_agent_enabled_tools_roundtrip(client) -> None:
    created = client.post(
        "/api/agents",
        json={"name": "Tools agent", "enabled_tools": ["web_fetch", "web_fetch"]},
    )
    assert created.status_code == 201
    agent = created.json()
    assert agent["enabled_tools"] == ["web_fetch"]

    renamed = client.put(f"/api/agents/{agent['id']}", json={"name": "Renamed tools"})
    assert renamed.status_code == 200
    assert renamed.json()["enabled_tools"] == ["web_fetch"]

    cleared = client.put(f"/api/agents/{agent['id']}", json={"enabled_tools": []})
    assert cleared.status_code == 200
    assert cleared.json()["enabled_tools"] == []


def test_agent_allows_valid_but_unregistered_tool_names(client) -> None:
    created = client.post(
        "/api/agents",
        json={"name": "Missing tool", "enabled_tools": ["not_registered"]},
    )
    assert created.status_code == 201
    assert created.json()["enabled_tools"] == ["not_registered"]


def test_agent_rejects_invalid_tool_names(client) -> None:
    for invalid in ("bad tool", "", "a" * 65):
        response = client.post(
            "/api/agents", json={"name": "Invalid tool", "enabled_tools": [invalid]}
        )
        assert response.status_code == 422, invalid
