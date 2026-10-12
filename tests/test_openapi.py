"""The OpenAPI document is the manual (docs/api.md): every route has a description, every path lives under /api, and the document itself is sound."""
import json


def test_every_route_has_a_one_sentence_description(client):
    spec = client.app.openapi()
    missing = [f"{m.upper()} {p}" for p, ops in spec["paths"].items() for m, op in ops.items() if not (op.get("description") or "").strip()]
    assert not missing, f"routes without a description (a docstring on the function): {missing}"
    too_long = [f"{m.upper()} {p}" for p, ops in spec["paths"].items() for m, op in ops.items() if len((op.get("description") or "").split("\n")[0]) > 300]
    assert not too_long, too_long


def test_the_document_is_valid_json_and_only_the_api_is_in_it(client):
    spec = json.loads(json.dumps(client.app.openapi()))
    assert spec["openapi"].startswith("3.") and spec["info"]["version"]
    assert all(p.startswith("/api/") for p in spec["paths"]), [p for p in spec["paths"] if not p.startswith("/api/")]
    assert len(spec["paths"]) > 150
