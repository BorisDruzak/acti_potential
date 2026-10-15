"""Generate the checked contract without environment settings or deployment URLs."""

import json
from pathlib import Path

from app.service_api.api import schema


def main():
    path = Path(__file__).resolve().parents[2] / "contracts" / "knowledge-service-v1.openapi.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schema(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
