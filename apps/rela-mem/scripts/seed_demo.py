"""Opt-in demo data; invoke only against a disposable or explicitly chosen service."""
import argparse
import json
import os

import httpx


def checked(response):
    response.raise_for_status()
    return response.json()


def seed(client):
    ids = []
    for value, name in (("rela_demo_zhang", "张三"), ("rela_demo_li", "李四")):
        result = checked(client.get("/v1/identity/resolve", params={"namespace": "demo", "value": value}))["person"]
        if result is None:
            result = checked(client.post("/v1/persons", json={"display_name": name, "aliases": [{"namespace": "demo", "value": value}]}))["person"]
        ids.append(result["person_id"])
    circles = checked(client.get("/v1/circles", params={"query": "rela-mem 示例家庭"}))["items"]
    circle_id = circles[0]["circle_id"] if circles else checked(client.post("/v1/circles", json={"name": "rela-mem 示例家庭", "kind": "family"}))["circle"]["circle_id"]
    for pid, alias in zip(ids, ("老张", "小李")):
        checked(client.put(f"/v1/circles/{circle_id}/members/{pid}", json={"alias": alias, "role": "member"}))
    profile = checked(client.get(f"/v1/persons/{ids[0]}/profile", params={"keys": "cat_food_preference"}))["items"]
    if not profile:
        checked(client.put(f"/v1/persons/{ids[0]}/profile/cat_food_preference", json={"value": "喜欢鸡肉味猫罐头", "source": "import", "source_ref": "demo:seed"}))
    checked(client.post("/v1/relations", json={"from_person_id": ids[0], "to_person_id": ids[1], "kind": "kin", "direction": "two_way", "forward_detail": "表哥", "reverse_detail": "表弟", "scope_circle_id": circle_id, "source": "import", "source_ref": "demo:seed"}))
    return {"person_a": ids[0], "person_b": ids[1], "circle_id": circle_id}


def main():
    parser = argparse.ArgumentParser(description="Write explicitly requested demo records; does not delete data")
    parser.add_argument("--url", required=True)
    parser.add_argument("--allow-write", action="store_true", required=True)
    args = parser.parse_args()
    token = os.environ.get("RELA_MEM_API_TOKEN")
    if not token:
        parser.error("RELA_MEM_API_TOKEN is required")
    with httpx.Client(base_url=args.url, headers={"Authorization": f"Bearer {token}"}, timeout=10) as client:
        print(json.dumps(seed(client), ensure_ascii=False))


if __name__ == "__main__":
    main()
