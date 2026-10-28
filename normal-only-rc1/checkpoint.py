from pathlib import Path

def scan_low(directory, initial_low):
    directory = Path(directory)
    acknowledged = directory / "sentid"
    baseline = directory / "initial_scan_id"

    if acknowledged.is_symlink() or baseline.is_symlink():
        raise RuntimeError("symlinked_legacy_cursor")
    if acknowledged.exists():
        value = int(acknowledged.read_text().strip())
    elif baseline.exists():
        value = int(baseline.read_text().strip())
    else:
        if (directory / "lastid").exists() or (directory / "new.csv").exists():
            raise RuntimeError("ambiguous_legacy_start_requires_review")
        value = int(initial_low)

    if value < 0:
        raise ValueError("Negative cursor is invalid")
    return value
