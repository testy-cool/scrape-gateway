from __future__ import annotations

import typer
from typer.testing import CliRunner

from scrape_gateway import discovery


runner = CliRunner()


def test_local_command_extension_registers(tmp_path, monkeypatch):
    ext = tmp_path / "hello.py"
    ext.write_text(
        "import typer\n"
        "\n"
        "def register(app: typer.Typer) -> None:\n"
        "    @app.command('hello')\n"
        "    def hello(name: str) -> None:\n"
        "        print(f'hello {name}')\n"
    )
    monkeypatch.setattr(discovery, "COMMAND_EXTENSIONS_DIR", tmp_path)

    app = typer.Typer()

    @app.command("noop")
    def noop() -> None:
        pass

    loaded = discovery.load_command_extensions(app, include_entry_points=False)
    result = runner.invoke(app, ["hello", "Vlad"])

    assert loaded == {"hello": str(tmp_path)}
    assert result.exit_code == 0
    assert "hello Vlad" in result.output


def test_local_command_extension_without_register_is_skipped(tmp_path, monkeypatch):
    (tmp_path / "broken.py").write_text("VALUE = 1\n")
    monkeypatch.setattr(discovery, "COMMAND_EXTENSIONS_DIR", tmp_path)

    app = typer.Typer()
    loaded = discovery.load_command_extensions(app, include_entry_points=False)

    assert loaded == {}
