#!/usr/bin/env python3
"""Exit 1 if the QC result JSON indicates a qc_error, causing the job to fail
and triggering the self-heal workflow. Skips gracefully if file is missing
(e.g. aider or validate step failed before QC was attempted)."""
import argparse
import json
import sys
from pathlib import Path

FAIL_STATUSES = {"qc_error", "error", "failed"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", required=True, help="Path to QC result JSON file")
    args = parser.parse_args()

    result_path = Path(args.result)
    if not result_path.exists():
        print(f"[check_qc_outcome] Result file not found: {result_path} — skipping check")
        sys.exit(0)

    try:
        data = json.loads(result_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[check_qc_outcome] Could not parse result file: {exc} — skipping check")
        sys.exit(0)

    status = str(data.get("status", "")).lower()
    error_msg = data.get("error", data.get("message", ""))

    if status in FAIL_STATUSES:
        print(
            f"[check_qc_outcome] QC outcome is '{status}': {error_msg}\n"
            "Failing job so self-heal workflow triggers."
        )
        sys.exit(1)

    print(f"[check_qc_outcome] QC outcome is '{status}' — OK")
    sys.exit(0)


if __name__ == "__main__":
    main()
