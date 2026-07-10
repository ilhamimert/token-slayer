"""CLI tests for the `tslayer estimate` command."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cca.cli import app

runner = CliRunner()


class TestEstimateCLI:
    def test_simple_prompt_inline(self):
        result = runner.invoke(app, ["estimate", "What is Python?"])
        assert result.exit_code == 0
        assert "SIMPLE" in result.output
        assert "Token estimate" in result.output

    def test_complex_prompt_routes_to_complex(self):
        prompt = "Analyze and compare the architectural trade-offs in depth, step-by-step"
        result = runner.invoke(app, ["estimate", prompt])
        assert result.exit_code == 0
        assert "COMPLEX" in result.output

    def test_json_output_has_required_keys(self):
        result = runner.invoke(app, ["estimate", "--json", "Write a sort function"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        for key in ("input_tokens", "output_tokens", "complexity",
                    "chosen_model", "chosen_provider",
                    "baseline_cost_usd", "routed_cost_usd",
                    "savings_usd", "savings_pct"):
            assert key in data, f"Missing key: {key}"

    def test_json_output_savings_pct_is_float(self):
        result = runner.invoke(app, ["estimate", "--json", "What is 2+2?"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data["savings_pct"], float)

    def test_output_tokens_override(self):
        result = runner.invoke(app, ["estimate", "--json", "--output-tokens", "500", "hello"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["output_tokens"] == 500

    def test_provider_flag_anthropic(self):
        result = runner.invoke(app, ["estimate", "--json", "--provider", "anthropic", "hello world simple"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["chosen_provider"] == "anthropic"

    def test_file_flag(self, tmp_path: Path):
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text("What is the capital of France?", encoding="utf-8")
        result = runner.invoke(app, ["estimate", "--file", str(prompt_file)])
        assert result.exit_code == 0
        assert "SIMPLE" in result.output

    def test_file_not_found_exits_1(self):
        result = runner.invoke(app, ["estimate", "--file", "/nonexistent/path/prompt.txt"])
        assert result.exit_code == 1

    def test_no_prompt_reads_stdin_gracefully(self):
        # CliRunner provides empty stdin; command should still exit cleanly
        result = runner.invoke(app, ["estimate"], input="")
        # Either succeeds with 0 tokens or exits with error — both acceptable
        assert result.exit_code in (0, 1)

    def test_savings_line_shown(self):
        result = runner.invoke(app, ["estimate", "What is 2+2?"])
        assert result.exit_code == 0
        assert "Savings:" in result.output

    def test_baseline_and_routed_cost_shown(self):
        result = runner.invoke(app, ["estimate", "What is 2+2?"])
        assert result.exit_code == 0
        assert "Baseline cost" in result.output
        assert "Routed cost" in result.output

    def test_input_token_count_positive(self):
        result = runner.invoke(app, ["estimate", "--json", "hello world how are you"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["input_tokens"] > 0

    def test_output_tokens_defaults_to_quarter_of_input(self):
        result = runner.invoke(app, ["estimate", "--json", "word " * 100])
        assert result.exit_code == 0
        data = json.loads(result.output)
        expected = max(1, data["input_tokens"] // 4)
        assert data["output_tokens"] == expected
