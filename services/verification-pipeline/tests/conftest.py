import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
TARGET_REPO = FIXTURES / "target_repo"
FLAKY_DIR = FIXTURES / "flaky"
REGRESSION_DIR = FIXTURES / "regression"
PLANS_DIR = FIXTURES / "plans"


@pytest.fixture
def target_repo() -> Path:
    return TARGET_REPO


@pytest.fixture
def flaky_dir() -> Path:
    return FLAKY_DIR


@pytest.fixture(autouse=False)
def reset_flaky_counter():
    counter = FLAKY_DIR / ".flaky_attempt_counter"
    if counter.exists():
        counter.unlink()
    yield
    if counter.exists():
        counter.unlink()


@pytest.fixture
def valid_plan() -> dict:
    return json.loads((PLANS_DIR / "valid_plan.json").read_text())


@pytest.fixture
def plan_missing_criterion_test() -> dict:
    return json.loads((PLANS_DIR / "plan_missing_criterion_test.json").read_text())


@pytest.fixture
def regression_dir() -> Path:
    return REGRESSION_DIR
