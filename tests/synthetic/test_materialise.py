"""Tests for materialise.py — byte-identity, directory structure, and manifest schema."""

import hashlib
import json
from pathlib import Path

import pytest

from src.synthetic.materialise import read_manifest, write_scenario_to_disk
from src.synthetic.orchestrator import load_scenario

SCENARIOS_DIR = Path("fixtures/synthetic-scenarios")


def _hash_dir(directory: Path) -> str:
    """SHA-256 over the sorted concatenation of all file contents in a directory tree.

    Used to assert byte-identical re-runs without string comparison of every file.
    Excludes last_run.json — that file carries a wall-clock generated_at timestamp
    by design (ADR-015 §D5: manifest.json is the reproducible artifact; last_run.json
    is human-debug metadata about the most recent run).
    """
    h = hashlib.sha256()
    for filepath in sorted(directory.rglob("*")):
        if filepath.is_file() and filepath.name != "last_run.json":
            h.update(filepath.name.encode())
            h.update(filepath.read_bytes())
    return h.hexdigest()


class TestWriteScenarioToDisk:
    def test_produces_manifest(self, tmp_path):
        scenario = load_scenario(SCENARIOS_DIR / "single-champion-then-silence.yaml")
        out = tmp_path / "out"
        write_scenario_to_disk(scenario, SCENARIOS_DIR / "single-champion-then-silence.yaml", out)
        assert (out / "manifest.json").exists()

    def test_manifest_schema(self, tmp_path):
        scenario = load_scenario(SCENARIOS_DIR / "single-champion-then-silence.yaml")
        out = tmp_path / "out"
        manifest = write_scenario_to_disk(
            scenario, SCENARIOS_DIR / "single-champion-then-silence.yaml", out
        )
        assert manifest["schema_version"] == 1
        assert manifest["scenario_name"] == "single-champion-then-silence"
        assert manifest["scenario_seed"] == 4217
        assert isinstance(manifest["scenario_input_hash"], str)
        assert len(manifest["scenario_input_hash"]) == 64  # SHA-256 hex
        assert isinstance(manifest["signal_count"], int)
        assert manifest["signal_count"] > 0
        assert isinstance(manifest["account_slugs"], list)
        assert len(manifest["account_slugs"]) > 0

    def test_workspace_json_present(self, tmp_path):
        scenario = load_scenario(SCENARIOS_DIR / "single-champion-then-silence.yaml")
        out = tmp_path / "out"
        write_scenario_to_disk(scenario, SCENARIOS_DIR / "single-champion-then-silence.yaml", out)
        assert (out / "workspace.json").exists()
        ws = json.loads((out / "workspace.json").read_text())
        assert ws["slug"] == scenario.workspace_slug

    def test_organization_json_present(self, tmp_path):
        scenario = load_scenario(SCENARIOS_DIR / "single-champion-then-silence.yaml")
        out = tmp_path / "out"
        write_scenario_to_disk(scenario, SCENARIOS_DIR / "single-champion-then-silence.yaml", out)
        assert (out / "organization.json").exists()
        org = json.loads((out / "organization.json").read_text())
        assert "slug" in org

    def test_accounts_dir_present(self, tmp_path):
        scenario = load_scenario(SCENARIOS_DIR / "single-champion-then-silence.yaml")
        out = tmp_path / "out"
        write_scenario_to_disk(scenario, SCENARIOS_DIR / "single-champion-then-silence.yaml", out)
        assert (out / "accounts").is_dir()
        account_files = list((out / "accounts").glob("*.json"))
        assert len(account_files) == len(scenario.accounts)

    def test_signals_dir_present(self, tmp_path):
        scenario = load_scenario(SCENARIOS_DIR / "single-champion-then-silence.yaml")
        out = tmp_path / "out"
        write_scenario_to_disk(scenario, SCENARIOS_DIR / "single-champion-then-silence.yaml", out)
        assert (out / "signals").is_dir()

    def test_signal_files_match_count(self, tmp_path):
        scenario = load_scenario(SCENARIOS_DIR / "single-champion-then-silence.yaml")
        out = tmp_path / "out"
        manifest = write_scenario_to_disk(
            scenario, SCENARIOS_DIR / "single-champion-then-silence.yaml", out
        )
        signal_files = list((out / "signals").rglob("*.json"))
        assert len(signal_files) == manifest["signal_count"]

    def test_signal_files_are_valid_json(self, tmp_path):
        scenario = load_scenario(SCENARIOS_DIR / "single-champion-then-silence.yaml")
        out = tmp_path / "out"
        write_scenario_to_disk(scenario, SCENARIOS_DIR / "single-champion-then-silence.yaml", out)
        for signal_file in (out / "signals").rglob("*.json"):
            data = json.loads(signal_file.read_text())
            assert "external_id" in data
            assert "body" in data
            assert data["body"].strip()

    def test_signal_files_numbered_sequentially(self, tmp_path):
        scenario = load_scenario(SCENARIOS_DIR / "single-champion-then-silence.yaml")
        out = tmp_path / "out"
        write_scenario_to_disk(scenario, SCENARIOS_DIR / "single-champion-then-silence.yaml", out)
        for account_dir in (out / "signals").iterdir():
            if account_dir.is_dir():
                files = sorted(account_dir.glob("*.json"))
                names = [f.name for f in files]
                expected = [f"{i:03d}.json" for i in range(len(files))]
                assert names == expected, f"Non-sequential files in {account_dir}: {names}"

    def test_dry_run_writes_nothing(self, tmp_path, capsys):
        scenario = load_scenario(SCENARIOS_DIR / "single-champion-then-silence.yaml")
        out = tmp_path / "dry-out"
        write_scenario_to_disk(
            scenario,
            SCENARIOS_DIR / "single-champion-then-silence.yaml",
            out,
            dry_run=True,
        )
        assert not out.exists(), "dry-run must not create output directory"
        captured = capsys.readouterr()
        assert "dry-run" in captured.out.lower()

    def test_seed_override_recorded_in_manifest(self, tmp_path):
        scenario = load_scenario(SCENARIOS_DIR / "single-champion-then-silence.yaml")
        out = tmp_path / "out"
        manifest = write_scenario_to_disk(
            scenario,
            SCENARIOS_DIR / "single-champion-then-silence.yaml",
            out,
            seed_override=9999,
        )
        assert manifest["scenario_seed"] == 9999


class TestByteIdentity:
    def test_same_seed_byte_identical_signal_files(self, tmp_path):
        """Running materialise twice for the same scenario produces byte-identical signal files."""
        scenario = load_scenario(SCENARIOS_DIR / "single-champion-then-silence.yaml")
        scenario_path = SCENARIOS_DIR / "single-champion-then-silence.yaml"

        out1 = tmp_path / "run1"
        out2 = tmp_path / "run2"
        write_scenario_to_disk(scenario, scenario_path, out1)
        write_scenario_to_disk(scenario, scenario_path, out2)

        assert _hash_dir(out1) == _hash_dir(out2), (
            "Byte-identical re-run failed: signal file contents differ"
        )

    def test_same_seed_identical_external_ids(self, tmp_path):
        scenario = load_scenario(SCENARIOS_DIR / "single-champion-then-silence.yaml")
        scenario_path = SCENARIOS_DIR / "single-champion-then-silence.yaml"

        out1 = tmp_path / "run1"
        out2 = tmp_path / "run2"
        write_scenario_to_disk(scenario, scenario_path, out1)
        write_scenario_to_disk(scenario, scenario_path, out2)

        def collect_external_ids(out: Path) -> list[str]:
            ids = []
            for f in sorted(out.rglob("signals/**/*.json")):
                data = json.loads(f.read_text())
                ids.append(data["external_id"])
            return ids

        assert collect_external_ids(out1) == collect_external_ids(out2)

    def test_different_seeds_produce_different_output(self, tmp_path):
        scenario = load_scenario(SCENARIOS_DIR / "single-champion-then-silence.yaml")
        scenario_path = SCENARIOS_DIR / "single-champion-then-silence.yaml"

        out1 = tmp_path / "run_default"
        out2 = tmp_path / "run_override"
        write_scenario_to_disk(scenario, scenario_path, out1)
        write_scenario_to_disk(scenario, scenario_path, out2, seed_override=1234)

        # Different seeds should produce different external_ids
        assert _hash_dir(out1) != _hash_dir(out2)

    def test_multi_stakeholder_byte_identical(self, tmp_path):
        scenario = load_scenario(SCENARIOS_DIR / "multi-stakeholder-mixed-domain.yaml")
        scenario_path = SCENARIOS_DIR / "multi-stakeholder-mixed-domain.yaml"

        out1 = tmp_path / "run1"
        out2 = tmp_path / "run2"
        write_scenario_to_disk(scenario, scenario_path, out1)
        write_scenario_to_disk(scenario, scenario_path, out2)

        assert _hash_dir(out1) == _hash_dir(out2)


class TestReadManifest:
    def test_read_manifest_roundtrip(self, tmp_path):
        scenario = load_scenario(SCENARIOS_DIR / "single-champion-then-silence.yaml")
        out = tmp_path / "out"
        written_manifest = write_scenario_to_disk(
            scenario, SCENARIOS_DIR / "single-champion-then-silence.yaml", out
        )
        read_back = read_manifest(out)
        assert read_back["scenario_name"] == written_manifest["scenario_name"]
        assert read_back["scenario_seed"] == written_manifest["scenario_seed"]
        assert read_back["signal_count"] == written_manifest["signal_count"]

    def test_read_manifest_missing_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            read_manifest(tmp_path / "nonexistent-dir")
