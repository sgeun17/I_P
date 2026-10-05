from structured_output_adapter import item_json_schema, output_schema_version


def test_adapter_consumes_official_item_result_without_redefining_contract():
    schema = item_json_schema()
    assert schema["properties"]["result"]["enum"] == ["MET", "NOT_MET", "UNKNOWN"]
    assert "Citation" in schema["$defs"]
    assert output_schema_version() == "phase2-output-0.3"
