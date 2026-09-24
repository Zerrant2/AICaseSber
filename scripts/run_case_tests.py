"""Прогон 12 проверок кейса SK01 и таблица результатов.

  python scripts/run_case_tests.py            # офлайн (сценарная LLM + справочник кейса)
  python scripts/run_case_tests.py --llm      # + прогон на реальной модели (нужен LLM_API_KEY в .env)

Результат: reports/case_tests.md и reports/case_tests.csv
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Windows-консоль (CP1251) не умеет печатать «→», «✓» и т.п. — issue #17
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

REPORT_JSON = ROOT / "reports" / "case_tests.json"
STATUS = {
    "passed": "✅ пройдено",
    "failed": "❌ не пройдено",
    "xfail": "⏸ отложено",
    "skipped": "⏭ пропущено",
}


def main() -> int:
    if REPORT_JSON.exists():
        REPORT_JSON.unlink()
    marker = [] if "--llm" in sys.argv else ["-m", "not llm"]
    code = subprocess.call([sys.executable, "-m", "pytest", "-q", "tests/case_sk01", *marker], cwd=ROOT)
    results = json.loads(REPORT_JSON.read_text(encoding="utf-8")) if REPORT_JSON.exists() else {}
    tests = json.loads((ROOT / "data/reference/sk01/tests.json").read_text(encoding="utf-8"))
    rows = []
    for t in tests:
        r = results.get(t["id"], {})
        rows.append(
            {
                "id": t["id"],
                "input": t["input"],
                "expected": t["expected"],
                "actual": " | ".join(a for a in r.get("actual", []) if a) or "—",
                "status": STATUS.get(r.get("status", ""), r.get("status", "не запускался")),
            }
        )
    with (ROOT / "reports/case_tests.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    passed = sum(r["status"].startswith("✅") for r in rows)
    lines = [
        "# Проверки кейса SK01 — фактические результаты",
        "",
        f"*Прогон: {datetime.now():%d.%m.%Y %H:%M}. Пройдено: **{passed} из {len(rows)}**. "
        "Команда: `python scripts/run_case_tests.py`.*",
        "",
        "Офлайн-прогон использует справочник кейса и сценарную LLM (детерминированные ответы), чтобы проверить "
        "логику системы: проверки кодом, ограничители, выгрузку и разбор ответов. Прогон на реальной модели — "
        "`--llm`.",
        "",
        "| ID | Вход | Ожидание | Фактический результат | Статус |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        cells = [
            r[k].replace("|", "/").replace("\n", " ") for k in ("id", "input", "expected", "actual", "status")
        ]
        lines.append("| " + " | ".join(cells) + " |")
    (ROOT / "reports/case_tests.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n{passed}/{len(rows)} → reports/case_tests.md")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
