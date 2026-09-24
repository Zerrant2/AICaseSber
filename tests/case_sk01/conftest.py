"""Тесты кейса SK01: 12 проверок из data/reference/sk01/tests.json.

Каждый тест записывает «фактический результат» через фикстуру `record`; по итогам
прогона пишется reports/case_tests.json (его превращает в таблицу scripts/run_case_tests.py).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from socrat.config import Settings
from socrat.core import build_core
from socrat.testing.fakes import FakeKnowledgeBase
from socrat.testing.scripted import ScriptedLLM

ROOT = Path(__file__).resolve().parents[2]
REF = ROOT / "data" / "reference" / "sk01"
REPORT = ROOT / "reports" / "case_tests.json"
_RESULTS: dict[str, dict] = {}


@pytest.fixture
def record(request):
    def _rec(test_id: str, actual: str) -> None:
        _RESULTS.setdefault(test_id, {"actual": [], "nodeid": request.node.nodeid})
        _RESULTS[test_id]["actual"].append(actual)

    return _rec


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if rep.when == "call" or (rep.when == "setup" and rep.outcome != "passed"):
        for r in _RESULTS.values():
            if r["nodeid"] == item.nodeid:
                r["status"] = "xfail" if hasattr(rep, "wasxfail") else rep.outcome
        tid = item.name.split("_")[1] if item.name.startswith("test_T") else None
        if tid and tid not in _RESULTS:
            _RESULTS[tid] = {
                "actual": [str(rep.longrepr)[:300] if rep.failed else ""],
                "nodeid": item.nodeid,
                "status": "xfail" if hasattr(rep, "wasxfail") else rep.outcome,
            }


def pytest_sessionfinish(session, exitstatus):
    if not _RESULTS:
        return
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    old = json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.exists() else {}
    old.update(_RESULTS)
    REPORT.write_text(json.dumps(old, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.fixture(scope="session")
def case_tests() -> dict[str, dict]:
    return {t["id"]: t for t in json.loads((REF / "tests.json").read_text(encoding="utf-8"))}


@pytest.fixture
def core():
    settings = Settings(_env_file=None, llm_max_repairs=2)
    return build_core(settings, FakeKnowledgeBase(), llm=ScriptedLLM())


@pytest.fixture
def teacher_request() -> dict:
    return json.loads((REF / "teacher_request.json").read_text(encoding="utf-8"))
