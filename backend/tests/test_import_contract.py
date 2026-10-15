import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError


def test_import_contract_and_source_qualification():
    root = Path(__file__).resolve().parents[2] / "migration"
    schema = json.loads((root / "import-contract.schema.json").read_text())
    example = json.loads((root / "examples/synthetic-record.json").read_text())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    validator.validate(example)
    other = {**example, "source_database": "raion"}
    validator.validate(other)
    assert (example["source_database"], example["source_id"]) != (
        other["source_database"],
        other["source_id"],
    )
    for patch in [
        {"source_database": "other"},
        {"target_id": "bad"},
        {"file_state": "unverified-as-missing"},
    ]:
        with pytest.raises(ValidationError):
            validator.validate({**example, **patch})
    for state in ["present", "missing", "no_path", "inaccessible", "invalid_path", "unknown"]:
        validator.validate({**example, "file_state": state})
