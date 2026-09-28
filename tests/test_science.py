import math
from pathlib import Path

import pytest

from rna_design.errors import CapabilityError, DesignError
from rna_design.folding import fold
from rna_design.inputs import crossing_pairs, parse_structure, read_sequence, validate_length
from rna_design.metrics import levenshtein, pair_metrics, predicted_openknot, sequence_metrics


@pytest.mark.parametrize(
    "left,right,distance",
    [
        ("ACGU", "CGUA", 2),
        ("", "AC", 2),
        ("((....))", ".(....).", 2),
        ("ACGU", "ACGU", 0),
        ("AG", "GA", 2),
    ],
)
def test_levenshtein(left, right, distance):
    assert levenshtein(left, right) == distance
    assert levenshtein(right, left) == distance


def test_shift_hamming_differs():
    metrics = sequence_metrics("ACGU", "CGUA")
    assert metrics["sequence_levenshtein_to_input"]["value"] == 2
    assert metrics["hamming_to_input"]["value"] == 4


def test_twort_composition():
    _, sequence, _ = read_sequence(Path(__file__).parents[1] / "twort_seq.txt")
    assert len(sequence) == 282
    assert sequence.count("C") == 43
    assert sequence_metrics(sequence, sequence)["c_percent"]["value"] == 100 * 43 / 282


@pytest.mark.parametrize("text", ["ACNT", "", "ACGT", ">a\nACGU\n>b\nACGU", "ACGU\n>a\nACGU"])
def test_invalid_sequence(tmp_path, text):
    path = tmp_path / "input"
    path.write_text(text)
    with pytest.raises(DesignError):
        read_sequence(path)


def test_normalize_single_record(tmp_path):
    path = tmp_path / "input"
    path.write_text(">sample\n aCgU \n uUaa\n")
    raw, sequence, name = read_sequence(path)
    assert sequence == "ACGUUUAA"
    assert name == "sample"
    assert raw == path.read_bytes()


@pytest.mark.parametrize("structure", ["((..)", "..)..", "(.&.)", "(.", "["])
def test_malformed_structure(structure):
    with pytest.raises(DesignError):
        parse_structure(structure)


def test_bracket_invariance_and_crossings():
    a = parse_structure("((....))")[1]
    b = parse_structure("[[....]]")[1]
    assert a == b == {(1, 8), (2, 7)}
    assert crossing_pairs(b) == set()
    crossing = parse_structure("([..)]")[1]
    assert crossing_pairs(crossing) == crossing == {(1, 5), (2, 6)}
    assert parse_structure("AB..ab")[1] == crossing
    assert pair_metrics(a, b)["pair_f1"]["value"] == 1


def test_pair_conventions():
    both = pair_metrics(set(), set())
    assert both["pair_f1"]["value"] == 1
    assert both["pair_precision"]["value"] is None
    assert both["pair_recall"]["value"] is None
    assert both["base_pair_distance"]["value"] == 0
    assert pair_metrics(set(), {(1, 4)})["pair_f1"]["value"] == 0
    pairs = {(1, 5), (2, 6)}
    assert pair_metrics({(1, 5)}, pairs)["crossed_pair_recall"]["value"] == 0.5


def test_openknot_strict_thresholds_and_non_pk_scale():
    score = predicted_openknot("(....)", [0.5, 0.125, 0.2, 0.2, 0.2, 0.49])
    assert score["value"] == pytest.approx(100 / 3)
    assert score["provenance"]["attainable_maximum"] == 50
    assert predicted_openknot("([..)]", [0.1, 0.1, 0.8, 0.8, 0.1, 0.1])["value"] == 100
    assert predicted_openknot("([..)]", [0.25, 0.25, 0.8, 0.8, 0.25, 0.25])["value"] == 50


@pytest.mark.parametrize("values", [[0.2], [math.nan] * 6, [math.inf] * 6])
def test_bad_reactivities(values):
    with pytest.raises(ValueError):
        predicted_openknot("(....)", values)


@pytest.mark.parametrize("length", [0, 7, 501])
def test_boundary_rejection(length):
    with pytest.raises(CapabilityError):
        validate_length("A" * length)


def test_vienna_real_thermodynamics():
    result = fold("GGGAAACCC", 37)
    assert len(result["dot_bracket"]) == 9
    assert 0 <= result["mfe_frequency"] <= 1
    assert result["provenance"]["constrained"] is False
    assert result["mfe_kcal_mol"] <= 0
