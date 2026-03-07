"""Integration tests for QuantConnect API."""

import os
import subprocess

import pytest

_CREDENTIALS_AVAILABLE = bool(
    os.environ.get("QC_USER_ID", "").strip()
    and os.environ.get("QC_API_TOKEN", "").strip()
)


@pytest.mark.integration
@pytest.mark.skipif(
    not _CREDENTIALS_AVAILABLE,
    reason="QC credentials not available",
)
def test_qc_auth_validation() -> None:
    """Test QC API authentication works."""
    result = subprocess.run(
        ["python", "scripts/validate_qc_auth.py"],
        capture_output=True,
        text=True,
    )

    assert (
        result.returncode == 0
    ), f"Auth validation failed: stdout={result.stdout}\nstderr={result.stderr}"
    assert "✅" in result.stdout, (
        "Expected success marker in output. "
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


@pytest.mark.integration
@pytest.mark.skipif(
    not _CREDENTIALS_AVAILABLE,
    reason="QC credentials not available",
)
def test_qc_rest_client_basic() -> None:
    """Verify scripts/qc_rest_client.py exists (full API test is a separate PR)."""
    assert os.path.exists("scripts/qc_rest_client.py")
