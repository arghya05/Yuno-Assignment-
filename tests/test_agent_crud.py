"""Agent CRUD: SOUL.md round-trip via load_soul + the HTTP PUT endpoint."""
from __future__ import annotations

import textwrap


def write_soul(agents_dir, name: str, frontmatter_yaml: str, body: str) -> None:
    (agents_dir / f"{name}.md").write_text(f"---\n{frontmatter_yaml}\n---\n{body}\n")


def test_load_soul_parses_frontmatter(isolated_agents):
    write_soul(isolated_agents, "scout", textwrap.dedent("""
        name: scout
        mode: autonomous
        model: claude-haiku-4-5
        max_cost_usd: 0.01
        tools: [recent_transactions]
        skills: [pattern_recognition]
    """).strip(), "# Role\nDo the scouting.")

    from backend.runtime import load_soul
    soul = load_soul("scout")
    assert soul.name == "scout"
    assert soul.model == "claude-haiku-4-5"
    assert soul.tools == ["recent_transactions"]
    assert soul.max_cost_usd == 0.01
    assert "Do the scouting." in soul.body


def test_load_soul_without_frontmatter(isolated_agents):
    (isolated_agents / "bare.md").write_text("# Just a body\nNo frontmatter here.\n")
    from backend.runtime import load_soul
    soul = load_soul("bare")
    assert soul.frontmatter == {}
    assert "# Just a body" in soul.body


def test_system_prompt_includes_dispatch_conditions(isolated_agents):
    write_soul(isolated_agents, "router", textwrap.dedent("""
        name: router
        mode: autonomous
        model: claude-haiku-4-5
        dispatches_to:
          - agent: worker
            when: "task is small"
          - agent: heavy
            when: "task is large"
    """).strip(), "# Role\nRoute by size.")

    from backend.runtime import load_soul
    soul = load_soul("router")
    prompt = soul.system_prompt()
    assert "worker" in prompt
    assert "when task is small" in prompt
    assert "heavy" in prompt
    assert "when task is large" in prompt


def test_put_agent_writes_yaml(tmp_path, monkeypatch):
    """PUT /agents/{name} writes SOUL.md to disk; subsequent load_soul reads new content."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    # seed an initial file
    (agents_dir / "alpha.md").write_text("---\nname: alpha\n---\n# Old\n")

    from backend import runtime
    monkeypatch.setattr(runtime, "AGENTS_DIR", agents_dir)
    from backend import main as backend_main
    monkeypatch.setattr(backend_main, "AGENTS_DIR", agents_dir)

    from fastapi.testclient import TestClient
    with TestClient(backend_main.app) as client:
        r = client.put("/agents/alpha", json={
            "frontmatter": {"name": "alpha", "model": "claude-haiku-4-5", "tools": ["t1"]},
            "body": "# Role\nThe alpha agent.",
        })
        assert r.status_code == 200, r.text
        assert r.json()["saved"] == "alpha"

        # round-trip via GET
        r2 = client.get("/agents/alpha")
        assert r2.status_code == 200
        data = r2.json()
        assert data["frontmatter"]["model"] == "claude-haiku-4-5"
        assert data["frontmatter"]["tools"] == ["t1"]
        assert "alpha agent" in data["body"]


def test_get_agent_404_for_missing(tmp_path, monkeypatch):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    from backend import runtime
    monkeypatch.setattr(runtime, "AGENTS_DIR", agents_dir)
    from backend import main as backend_main
    monkeypatch.setattr(backend_main, "AGENTS_DIR", agents_dir)

    from fastapi.testclient import TestClient
    with TestClient(backend_main.app) as client:
        r = client.get("/agents/ghost")
        assert r.status_code == 404
