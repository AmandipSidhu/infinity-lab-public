#!/usr/bin/env python3
"""Validate QuantConnect API authentication."""

import hashlib
import os
import sys
import time
from base64 import b64encode

import requests


def validate_qc_auth() -> bool:
    """Test QC API auth with projects/read endpoint.
    
    Official QC auth formula:
      hash = SHA256(api_token:timestamp)
      Authorization = Basic base64(user_id:hash)
    
    Ref: https://www.quantconnect.com/docs/v2/cloud-platform/api-reference/authentication
    """
    user_id = os.environ.get("QC_USER_ID", "").strip()
    api_token = os.environ.get("QC_API_TOKEN", "").strip()

    if not user_id or not api_token:
        print("ERROR: QC_USER_ID or QC_API_TOKEN not set")
        return False

    # Step 1: hash only api_token:timestamp (NO user_id in hash)
    ts = str(int(time.time()))
    time_stamped_token = f"{api_token}:{ts}".encode("utf-8")
    hashed_token = hashlib.sha256(time_stamped_token).hexdigest()

    # Step 2: base64 encode user_id:hashed_token for Authorization header
    authentication = f"{user_id}:{hashed_token}".encode("utf-8")
    authentication = b64encode(authentication).decode("ascii")

    headers = {
        "Authorization": f"Basic {authentication}",
        "Timestamp": ts,
    }

    try:
        response = requests.get(
            "https://www.quantconnect.com/api/v2/projects/read",
            headers=headers,
            timeout=10,
        )

        if response.status_code == 200:
            data = response.json()
            if data.get("success"):
                print(
                    f"\u2705 QC auth validated - found {len(data.get('projects', []))} projects"
                )
                return True
            else:
                print(f"\u274c QC API returned success=false: {data.get('errors')}")
                return False
        elif response.status_code == 401:
            print("\u274c QC auth failed - 401 Unauthorized (check credentials)")
            return False
        elif response.status_code == 403:
            print("\u274c QC auth failed - 403 Forbidden (check API permissions)")
            return False
        else:
            print(f"\u274c QC API returned {response.status_code}: {response.text[:200]}")
            return False

    except requests.exceptions.Timeout:
        print("\u274c QC API timeout after 10 seconds")
        return False
    except Exception as e:
        print(f"\u274c QC API error: {e}")
        return False


if __name__ == "__main__":
    success = validate_qc_auth()
    sys.exit(0 if success else 1)
