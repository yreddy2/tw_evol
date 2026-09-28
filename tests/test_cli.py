import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from rna_design.cli import main
from rna_design.config import Experiment, InputConfig, load_config


@pytest.fixture
def sequence(tmp_path):
    p = tmp_path / "input.fasta"
    p.write_text(">example\nACGUACGU\n")
    return p


def test_init_validate_dry_run(sequence, tmp_path, capsys):
    config = tmp_path / "config.yaml"
    assert main(["init", "--sequence", str(sequence), "--output", str(config), "--json"]) == 0
    capsys.readouterr()
    assert main(["validate", "--config", str(config), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["target_source"] == "reference_mfe"
    assert result["inference_performed"] is False
    assert main(["run", "--config", str(config), "--dry-run", "--json"]) == 0
    assert not (tmp_path / "manifest.json").exists()
    assert main(["init", "--sequence", str(sequence), "--output", str(config)]) == 2


@pytest.mark.parametrize(
    "data",
    [
        {"regions": []},
        {"model": {"name": "random"}},
        {"model": {"mode": "3d"}},
        {"budget": {"proposals": True}},
        {"model": {"sampling_temperature": float("nan")}},
    ],
)
def test_strict_config(sequence, data):
    with pytest.raises(ValidationError):
        Experiment(input=InputConfig(sequence=sequence), **data)


def test_target_source_contradiction(sequence):
    with pytest.raises(ValidationError):
        InputConfig(sequence=sequence, target_source="provided")
    with pytest.raises(ValidationError):
        InputConfig(sequence=sequence, target=Path("a.dbn"), target_source="reference_mfe")


def test_relative_paths(sequence, tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump({"input": {"sequence": sequence.name}, "runtime": {"root": "models"}})
    )
    loaded = load_config(config)
    assert loaded.input.sequence == sequence
    assert loaded.runtime.root == tmp_path / "models"


def test_missing_runtime_exit(sequence, tmp_path, capsys):
    assert main(["doctor", "--runtime-root", str(tmp_path / "missing"), "--json"]) == 3
    assert json.loads(capsys.readouterr().out)["ready"] is False
    assert (
        main(
            [
                "run",
                "--sequence",
                str(sequence),
                "--runtime-root",
                str(tmp_path / "missing"),
                "--out",
                str(tmp_path / "run"),
                "--json",
            ]
        )
        == 3
    )
    assert not (tmp_path / "run").exists()


def test_light_commands_do_not_import_models(sequence):
    code = f"""
import sys
from rna_design.cli import main
assert main(['validate', '--sequence', {str(sequence)!r}]) == 0
assert not any(m in sys.modules for m in ['torch', 'RNA', 'numpy', 'torch_geometric'])
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=os.environ
    )
    assert result.returncode == 0, result.stderr
