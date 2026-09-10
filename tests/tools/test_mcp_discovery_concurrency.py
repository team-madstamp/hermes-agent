import asyncio


def test_discover_all_caps_inflight_connections(monkeypatch):
    from tools import mcp_tool_discovery as discovery

    current = 0
    peak = 0
    completed = []

    async def fake_discover(name, _config):
        nonlocal current, peak
        current += 1
        peak = max(peak, current)
        await asyncio.sleep(0.01)
        current -= 1
        completed.append(name)
        return []

    monkeypatch.setattr(discovery, "_discover_and_register_server", fake_discover)
    monkeypatch.setattr(discovery, "_note_connect_success", lambda _name: None)
    candidates = {f"server-{index}": {} for index in range(discovery._MCP_DISCOVERY_MAX_CONCURRENCY * 2 + 1)}

    asyncio.run(discovery._discover_all(candidates))

    assert peak == discovery._MCP_DISCOVERY_MAX_CONCURRENCY
    assert set(completed) == set(candidates)
