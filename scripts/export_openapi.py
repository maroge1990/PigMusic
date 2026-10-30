"""Export the native v1 schema without touching production appdata."""

import argparse
import json
import tempfile
from pathlib import Path
from server.app import create_app

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "api/openapi.v1.json"
SWIFT = ROOT / "swift/PigKit/Sources/PigAPI/openapi.json"


def schema():
    with tempfile.TemporaryDirectory(prefix="pig-contract-") as temporary:
        app = create_app(Path(temporary))
        value = app.openapi()

    # Apple generator accepts OpenAPI 3.0 nullable schemas. Preserve nullability
    # explicitly instead of emitting unsupported standalone 3.1 null schemas.
    def nullable(node):
        if isinstance(node, dict):
            variants = node.get("anyOf", [])
            if any(v.get("type") == "null" for v in variants):
                nonnull = [v for v in variants if v.get("type") != "null"]
                node.pop("anyOf")
                if len(nonnull) == 1:
                    node.update(nonnull[0])
                else:
                    node["anyOf"] = nonnull
                node["nullable"] = True
            for value in node.values():
                nullable(value)
        elif isinstance(node, list):
            for item in node:
                nullable(item)

    nullable(value)
    value["openapi"] = "3.0.3"
    value["paths"] = {
        p: v for p, v in value["paths"].items() if p.startswith("/api/v1/")
    }
    value["servers"] = [{"url": "https://music.example.com"}]
    for path, operations in value["paths"].items():
        for method, operation in operations.items():
            if not isinstance(operation, dict) or "responses" not in operation:
                continue
            if path not in {
                "/api/v1/auth/login",
                "/api/v1/auth/device",
                "/api/v1/setup",
                "/api/v1/setup/owner",
            }:
                operation["security"] = [{"deviceBearer": []}]
            for status in (
                "400",
                "401",
                "403",
                "404",
                "409",
                "410",
                "422",
                "429",
                "503",
            ):
                operation["responses"].setdefault(
                    status, {"description": "Request error"}
                )["content"] = {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/APIError"}
                    }
                }
    value["components"]["schemas"]["APIError"] = {
        "type": "object",
        "required": ["detail", "code"],
        "properties": {"detail": {"type": "string"}, "code": {"type": "string"}},
    }
    value["components"]["securitySchemes"] = {
        "deviceBearer": {"type": "http", "scheme": "bearer"}
    }
    return value


def encoded():
    return json.dumps(schema(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--server-only",
        action="store_true",
        help="Leave the Swift agent's schema copy untouched",
    )
    args = parser.parse_args()
    content = encoded()
    if args.check:
        assert TARGET.read_text() == content, "Native OpenAPI contract is stale"
        if not args.server_only and SWIFT.exists():
            assert SWIFT.read_text() == content, "Swift schema copy is stale"
    else:
        TARGET.parent.mkdir(exist_ok=True)
        TARGET.write_text(content)
        if not args.server_only and SWIFT.parent.exists():
            SWIFT.write_text(content)
