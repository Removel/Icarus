"""Read-only REST smoke; optional --seed explicitly permits sample writes."""
import argparse
import os

import httpx
from seed_demo import seed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--seed", action="store_true")
    args = parser.parse_args()
    token = os.environ.get("RELA_MEM_API_TOKEN")
    if not token:
        parser.error("RELA_MEM_API_TOKEN is required")
    with httpx.Client(base_url=args.url, headers={"Authorization": f"Bearer {token}"}, timeout=10) as client:
        response = client.get("/healthz")
        response.raise_for_status()
        assert response.json()["status"] == "ready"
        response = client.get("/v1/persons")
        response.raise_for_status()
        assert {"items", "has_more", "next_cursor"} <= response.json().keys()
        if args.seed:
            result = seed(client)
            response = client.get("/v1/graph/shortest-path", params={"from": result["person_a"], "to": result["person_b"]})
            response.raise_for_status()
            assert response.json()["length"] == 1
    print("REST smoke passed" + (" (sample records retained)" if args.seed else " (read-only)"))


if __name__ == "__main__":
    main()
