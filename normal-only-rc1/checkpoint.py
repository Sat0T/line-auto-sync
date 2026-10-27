from pathlib import Path

def scan_low(directory, initial_low):
    directory = Path(directory)
    acknowledged = directory / "sentid"
    baseline = directory / "initial_scan_id"

    if acknowledged.exists():
        value = int(acknowledged.read_text().strip())
    elif baseline.exists():
        value = int(baseline.read_text().strip())
    else:
        value = int(initial_low)
        with baseline.open("x") as f:
            f.write(str(value))

    if value < 0:
        raise ValueError("Negative cursor is invalid")
    return value
