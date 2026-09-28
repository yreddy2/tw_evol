"""Inspectable, exact sequence-to-backbone mapping; never infer a biological alignment."""

import hashlib
import math
from pathlib import Path

from .errors import CapabilityError, DesignError
from .storage import identity

RNA_ATOMS = ["OP1", "OP2", "P", "O5'", "C5'", "C4'", "O4'", "C3'", "O3'", "C2'", "O2'", "C1'"]
PHOSPHATE_ATOMS = {"P", "OP1", "OP2"}


def inspect_backbone(path: Path, chain: str, sequence=None, missing_atoms="error"):
    if missing_atoms not in {"error", "mask_phosphate"}:
        raise DesignError("Unknown missing-atom policy")
    try:
        from Bio import __version__
        from Bio.PDB import MMCIFParser, PDBParser
    except ImportError as error:
        raise CapabilityError(
            "Backbone parsing requires Biopython; install the CLI [structure] extra"
        ) from error

    suffix = path.suffix.lower()
    if suffix not in {".pdb", ".cif", ".mmcif"}:
        raise DesignError("Backbone must be a PDB or mmCIF file")
    parser = (
        PDBParser(QUIET=True, PERMISSIVE=False) if suffix == ".pdb" else MMCIFParser(QUIET=True)
    )
    try:
        structure = parser.get_structure("backbone", str(path))
    except Exception as error:
        raise DesignError(f"Cannot parse backbone: {error}") from error
    models = list(structure)
    if len(models) != 1:
        raise DesignError(
            "Exactly one coordinate model is required; select a model explicitly in a separate file"
        )
    if chain not in models[0]:
        raise DesignError(
            f"Chain {chain!r} not found; available chains: {[c.id for c in models[0]]}"
        )
    residues, ignored, errors, numbering_gaps = [], [], [], []
    previous = None
    for residue in models[0][chain]:
        name = residue.resname.strip()
        hetero, number, insertion = residue.id
        insertion = insertion.strip()
        label = f"{chain}:{number}{insertion}"
        if residue.is_disordered() == 2:
            errors.append(f"Ambiguous alternate residue identity at {label}; resolve it first")
        if name not in {"A", "C", "G", "U"}:
            if hetero == " " or any(atom in residue for atom in ["C1'", "C1*", "O2'"]):
                errors.append(f"Noncanonical or non-RNA polymer residue {label} ({name})")
            else:
                ignored.append({"residue_id": label, "name": name})
            continue
        if previous is not None and number > previous + 1:
            numbering_gaps.append({"after": previous, "before": number})
        previous = number
        atoms = {}
        for atom_name in RNA_ATOMS:
            if atom_name not in residue:
                continue
            atom = residue[atom_name]
            if atom.is_disordered():
                errors.append(
                    f"Ambiguous alternate locations at {label} {atom_name}; resolve them first"
                )
                continue
            xyz = [float(x) for x in atom.coord]
            occupancy = atom.get_occupancy()
            if occupancy is None or not math.isfinite(occupancy) or occupancy <= 0:
                errors.append(f"Invalid/zero occupancy at {label} {atom_name}")
            elif not all(math.isfinite(x) for x in xyz):
                errors.append(f"Nonfinite coordinates at {label} {atom_name}")
            else:
                atoms[atom_name] = xyz
        missing = [a for a in RNA_ATOMS if a not in atoms]
        allowed = PHOSPHATE_ATOMS if missing_atoms == "mask_phosphate" else set()
        if set(missing) - allowed:
            errors.append(f"Missing required atoms at {label}: {', '.join(missing)}")
        residues.append(
            {
                "position": len(residues) + 1,
                "chain": chain,
                "residue_number": number,
                "insertion_code": insertion,
                "residue_id": label,
                "base": name,
                "atoms": atoms,
                "missing_atoms": missing,
            }
        )
    observed = "".join(r["base"] for r in residues)
    if not residues:
        errors.append("Selected chain contains no canonical RNA residues")
    mapping = []
    mismatches = []
    # Equal-length exact comparison only. A length mismatch never gets an inferred mapping.
    if sequence is not None and len(sequence) == len(observed):
        mapping = [
            {"reference_position": r["position"], "residue_id": r["residue_id"]} for r in residues
        ]
        mismatches = [
            {"position": i, "reference": a, "backbone": b}
            for i, (a, b) in enumerate(zip(sequence, observed), 1)
            if a != b
        ]
        if mismatches:
            errors.append(f"Reference/backbone sequence mismatch at {len(mismatches)} position(s)")
    elif sequence is not None:
        errors.append(
            f"Reference is {len(sequence)} nt but chain {chain} has {len(observed)} RNA residues; exact mapping is unavailable"
        )
    value = {
        "schema_version": 1,
        "mapping_strategy": "exact_sequence",
        "chain": chain,
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "format": suffix[1:],
        "parser": f"Biopython-{__version__}",
        "sequence": observed,
        "length": len(observed),
        "reference_length": len(sequence) if sequence is not None else None,
        "mapping": mapping,
        "mismatches": mismatches,
        "unmapped_reference_positions": list(range(1, len(sequence) + 1))
        if sequence is not None and not mapping
        else [],
        "numbering_gaps": numbering_gaps,
        "missing_atom_policy": missing_atoms,
        "residues": residues,
        "ignored_nonpolymer_residues": ignored,
        "excluded_chains": [c.id for c in models[0] if c.id != chain],
        "coordinate_policy": "observed RNA atoms only; absent phosphate slots use native masks, never reconstructed coordinates",
        "compatible": not errors and (sequence is None or observed == sequence),
        "errors": errors,
    }
    return {**value, "id": identity(value)}


def validate_backbone(settings, sequence):
    report = inspect_backbone(settings.path, settings.chain, sequence, settings.missing_atoms)
    if not report["compatible"]:
        raise CapabilityError(
            "; ".join(report["errors"]) + "; use inspect-backbone for the mapping report"
        )
    return report
