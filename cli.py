"""Talk to the pod from a terminal (works against local or Railway).

    python cli.py /briefing TTD
    python cli.py "/ask TTD: What if earnings miss 20%?"
    python cli.py /meeting
    python cli.py /deepdive NAVN
    python cli.py /update PUBM

Uses ANALYST_API_URL (default http://127.0.0.1:8000) and API_ACCESS_KEY from the environment / .env.
"""

import json
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()
API = os.getenv("ANALYST_API_URL", "http://127.0.0.1:8000").rstrip("/")
KEY = os.getenv("API_ACCESS_KEY", "")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    text = " ".join(sys.argv[1:])
    if not text.startswith("/"):
        text = "/" + text
    resp = requests.post(f"{API}/command", json={"text": text}, headers={"X-API-Key": KEY} if KEY else {}, timeout=300)
    if resp.status_code >= 400:
        print(f"Error {resp.status_code}: {resp.json().get('detail', resp.text)}")
        return 1
    data = resp.json()
    if "briefing_md" in data:
        print(data["briefing_md"])
    elif "answer" in data:
        print(f"{data['analyst']} on {data['ticker']}:\n\n{data['answer']}")
    else:
        print(json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
