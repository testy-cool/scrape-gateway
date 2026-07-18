from pathlib import Path

import yaml


def test_compose_file_runs_the_persistent_mcp_service() -> None:
    root = Path(__file__).resolve().parents[1]
    compose_path = root / "docker-compose.yml"

    assert compose_path.exists()
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    service = compose["services"]["sgw-mcp"]

    assert service["build"] == "."
    assert "8100:8100" in service["ports"]
    assert service["environment"]["SGW_MCP_HOST"] == "0.0.0.0"
    assert service["environment"]["SGW_MCP_PORT"] == "8100"
    assert "sgw-data:/data" in service["volumes"]
    assert compose["volumes"]["sgw-data"] is None
