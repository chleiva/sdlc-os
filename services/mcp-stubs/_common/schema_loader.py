import json
from pathlib import Path


def load_schema(schema_path: str | Path) -> dict:
    return json.loads(Path(schema_path).read_text())
