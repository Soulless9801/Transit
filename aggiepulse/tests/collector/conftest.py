import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def vehicles_payload():
    return json.loads((FIXTURES / "vehicles.json").read_text())


@pytest.fixture
def base_payload():
    return json.loads((FIXTURES / "routes.json").read_text())


@pytest.fixture
def ingest_ts():
    return json.loads((FIXTURES / "capture.json").read_text())["ingest_ts"]
