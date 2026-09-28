import math

from .errors import CapabilityError


def fold(sequence: str, temperature_c: float) -> dict:
    try:
        import RNA
    except ImportError as error:
        raise CapabilityError(
            "Install folding support: pip install 'rna-design-cli[fold]'"
        ) from error
    md = RNA.md()
    md.temperature = temperature_c
    fc = RNA.fold_compound(sequence, md)
    structure, energy = fc.mfe()
    fc.exp_params_rescale(energy)
    _, ensemble_energy = fc.pf()
    frequency = fc.pr_structure(structure)
    if len(structure) != len(sequence) or not all(map(math.isfinite, (energy, frequency))):
        raise ValueError("ViennaRNA returned malformed or nonfinite predictions")
    if not 0 <= frequency <= 1:
        raise ValueError("ViennaRNA MFE frequency is outside [0,1]")
    return {
        "sequence": sequence,
        "dot_bracket": structure,
        "mfe_kcal_mol": energy,
        "mfe_frequency": frequency,
        "ensemble_energy_kcal_mol": ensemble_energy,
        "provenance": {
            "engine": "ViennaRNA",
            "version": RNA.__version__,
            "physical_temperature_c": temperature_c,
            "dangles": md.dangles,
            "noLP": md.noLP,
            "energy_parameters": "Turner2004 defaults",
            "scope": "pseudoknot-free diagnostic",
            "constrained": False,
        },
    }
