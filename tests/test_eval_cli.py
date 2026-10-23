from typer.testing import CliRunner

from kb.cli import app


def test_eval_cli_rejects_final_grid_without_reading_data():
    result = CliRunner().invoke(app, ["eval-rag", "--final", "--grid"])
    assert result.exit_code != 0
    assert "--final 不能与 --grid" in result.output


def test_doctor_does_not_display_secret(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "synthetic-secret-never-print")
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "synthetic-secret-never-print" not in result.output
    assert "dashscope_key_configured" in result.output
