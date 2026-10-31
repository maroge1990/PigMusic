from scripts.export_openapi import TARGET, encoded


def test_native_contract_is_current_and_core_responses_are_typed():
    assert TARGET.read_text() == encoded()
    import json

    value = json.loads(TARGET.read_text())
    for path in [
        "/api/v1/library/tracks",
        "/api/v1/library/artists",
        "/api/v1/library/albums",
        "/api/v1/library/changes",
        "/api/v1/me",
        "/api/v1/albums/{album_id}",
        "/api/v1/artists/{artist_id}",
    ]:
        assert (
            "$ref"
            in value["paths"][path]["get"]["responses"]["200"]["content"][
                "application/json"
            ]["schema"]
        )


def test_the_contract_has_no_limits_the_swift_generator_cannot_read():
    """swift-openapi-generator reads exclusiveMinimum/exclusiveMaximum only as
    OpenAPI 3.0 booleans; Pydantic's gt/lt write numbers and broke the native
    build (31 Oct). Use ge/le instead."""
    import json

    found = []

    def walk(value, path):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"exclusiveMinimum", "exclusiveMaximum"} and not isinstance(
                    item, bool
                ):
                    found.append(path + "." + key)
                walk(item, path + "." + key)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")

    # The generated contract itself, so the test runs in the image layout too.
    walk(json.loads(encoded()), "")
    assert found == []
