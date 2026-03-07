#!/usr/bin/env python3
"""Validate QuantConnect API authentication."""

import hashlib
import os
import sys
import time

import requests


def validate_qc_auth() -> bool:
    """Test QC API auth with projects/read endpoint."""
    user_id = os.environ.get("QC_USER_ID", "").strip()
    api_token = os.environ.get("QC_API_TOKEN", "").strip()

    if not user_id or not api_token:
        print("ERROR: QC_USER_ID or QC_API_TOKEN not set")
        return False

    ts = str(int(time.time()))
    token_hash = hashlib.sha256(
        f"{user_id}:{api_token}:{ts}".encode("utf-8")
    ).hexdigest()
    headers = {"Timestamp": ts}
    auth = (user_id, token_hash)

    try:
        response = requests.get(
            "https://www.quantconnect.com/api/v2/projects/read",
            headers=headers,
            auth=auth,
            timeout=10,
        )

        if response.status_code == 200:
            data = response.json()
            if data.get("success"):
                print(
                    f"✅ QC auth validated - found {len(data.get('projects', []))} projects"
                )
                return True
            else:
                print(f"❌ QC API returned success=false: {data.get('errors')}")
                return False
        elif response.status_code == 401:
            print("❌ QC auth failed - 401 Unauthorized (check credentials)")
            return False
        elif response.status_code == 403:
            print("❌ QC auth failed - 403 Forbidden (check API permissions)")
            return False
        else:
            print(f"❌ QC API returned {response.status_code}: {response.text[:200]}")
            return False

    except requests.exceptions.Timeout:
        print("❌ QC API timeout after 10 seconds")
        return False
    except Exception as e:
        print(f"❌ QC API error: {e}")
        return False


if __name__ == "__main__":
    success = validate_qc_auth()
    sys.exit(0 if success else 1)
