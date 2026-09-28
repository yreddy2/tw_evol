"""Canonical sequences and 1-based, inclusive secondary-structure coordinates."""

from pathlib import Path

from .errors import DesignError

OPEN = "([{<ABCDEFGHIJKLMNOPQRSTUVWXYZ"
CLOSE = ")]} >abcdefghijklmnopqrstuvwxyz".replace(" ", "")
BRACKETS = dict(zip(OPEN, CLOSE))


def normalize_sequence(text: str) -> str:
    sequence = "".join(text.split()).upper()
    if not sequence:
        raise DesignError("RNA sequence is empty")
    invalid = sorted(set(sequence) - set("ACGU"))
    if invalid:
        raise DesignError(f"Only canonical RNA A/C/G/U is supported; invalid bases: {invalid}")
    return sequence


def read_sequence(path: Path) -> tuple[bytes, str, str | None]:
    raw = path.read_bytes()
    lines = raw.decode("utf-8-sig").strip().splitlines()
    headers = [i for i, line in enumerate(lines) if line.lstrip().startswith(">")]
    name = None
    if headers:
        if headers != [0]:
            raise DesignError("Expected exactly one FASTA record, with its header first")
        name = lines.pop(0).lstrip()[1:].strip()
    return raw, normalize_sequence("".join(lines)), name


def parse_structure(text: str, length: int | None = None) -> tuple[str, set[tuple[int, int]]]:
    structure = "".join(text.split())
    if not structure or (length is not None and len(structure) != length):
        raise DesignError(f"Target length {len(structure)} must equal sequence length {length}")
    stacks = {symbol: [] for symbol in OPEN}
    reverse = {right: left for left, right in BRACKETS.items()}
    pairs = set()
    for position, symbol in enumerate(structure, 1):
        if symbol == ".":
            continue
        if symbol in stacks:
            stacks[symbol].append(position)
        elif symbol in reverse:
            stack = stacks[reverse[symbol]]
            if not stack:
                raise DesignError(f"Unmatched {symbol!r} at position {position}")
            pairs.add((stack.pop(), position))
        else:
            raise DesignError(f"Unsupported dot-bracket symbol {symbol!r} at {position}")
    if any(stacks.values()):
        raise DesignError("Unclosed dot-bracket symbols")
    return structure, pairs


def crossing_pairs(pairs: set[tuple[int, int]]) -> set[tuple[int, int]]:
    return {(i, j) for i, j in pairs if any(i < k < j < end or k < i < end < j for k, end in pairs)}


def validate_length(sequence: str, model: str = "grnade") -> None:
    # A deliberately bounded product envelope, not a biological or universal model limit.
    from .runtime import LOCK

    if model == "struct2seq":
        from .struct2seq_runtime import LOCK
    elif model == "na_mpnn":
        from .na_mpnn_runtime import LOCK
    lower, upper = LOCK["supported_length"]
    if not lower <= len(sequence) <= upper:
        from .errors import CapabilityError

        raise CapabilityError(
            f"{model} supports {lower}–{upper} nt; inputs are never cropped or padded"
        )
