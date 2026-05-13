"""Tests for the synthesise-fixtures CLI subcommand via src.worker.main()."""

import json
from pathlib import Path

from src.worker import main

SCENARIOS_DIR = Path("fixtures/synthetic-scenarios")


class TestSynthesiseFixturesCLI:
    def test_dry_run_prints_summary(self, capsys):
        main(
            [
                "synthesise-fixtures",
                "--scenario",
                str(SCENARIOS_DIR / "single-champion-then-silence.yaml"),
                "--dry-run",
            ]
        )
        out = capsys.readouterr().out
        assert "dry-run" in out.lower()

    def test_writes_output_to_out_dir(self, tmp_path, capsys):
        out = tmp_path / "synth-out"
        main(
            [
                "synthesise-fixtures",
                "--scenario",
                str(SCENARIOS_DIR / "single-champion-then-silence.yaml"),
                "--out",
                str(out),
            ]
        )
        assert (out / "manifest.json").exists()
        assert (out / "workspace.json").exists()
        assert (out / "organization.json").exists()
        manifest = json.loads((out / "manifest.json").read_text())
        assert manifest["scenario_name"] == "single-champion-then-silence"
        assert manifest["signal_count"] == 12

    def test_seed_override_recorded(self, tmp_path):
        out = tmp_path / "synth-out"
        main(
            [
                "synthesise-fixtures",
                "--scenario",
                str(SCENARIOS_DIR / "single-champion-then-silence.yaml"),
                "--out",
                str(out),
                "--seed",
                "1234",
            ]
        )
        manifest = json.loads((out / "manifest.json").read_text())
        assert manifest["scenario_seed"] == 1234

    def test_all_three_scenarios_materialise(self, tmp_path):
        """All three named scenarios must materialise without error."""
        for scenario_name in [
            "single-champion-then-silence",
            "multi-stakeholder-mixed-domain",
            "frustrated-escalation",
        ]:
            out = tmp_path / scenario_name
            main(
                [
                    "synthesise-fixtures",
                    "--scenario",
                    str(SCENARIOS_DIR / f"{scenario_name}.yaml"),
                    "--out",
                    str(out),
                ]
            )
            assert (out / "manifest.json").exists(), f"No manifest for {scenario_name}"
            manifest = json.loads((out / "manifest.json").read_text())
            assert manifest["signal_count"] > 0, f"No signals for {scenario_name}"
