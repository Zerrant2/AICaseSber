"""Библиотека методических приёмов (data/techniques.json)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from socrat.config import ROOT_DIR

PATH = ROOT_DIR / "data" / "techniques.json"


@dataclass(frozen=True)
class Technique:
    technique_id: str
    name: str
    uud_group: str | None
    essence: str
    when_to_use: str
    expected_shift: str
    task_kinds: tuple[str, ...]


class TechniqueLibrary:
    def __init__(self, items: list[Technique]) -> None:
        self._by_id = {t.technique_id: t for t in items}

    @classmethod
    def load(cls, path: Path = PATH) -> TechniqueLibrary:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            [
                Technique(
                    technique_id=t["technique_id"],
                    name=t["name"],
                    uud_group=t.get("uud_group"),
                    essence=t["essence"],
                    when_to_use=t["when_to_use"],
                    expected_shift=t["expected_shift"],
                    task_kinds=tuple(t.get("task_kinds", [])),
                )
                for t in data["techniques"]
            ]
        )

    def all(self) -> list[Technique]:
        return list(self._by_id.values())

    def get(self, technique_id: str) -> Technique | None:
        return self._by_id.get(technique_id)

    def filter(self, ids: list[str]) -> list[str]:
        out = []
        for i in ids:
            i = i.strip().upper()
            if i in self._by_id and i not in out:
                out.append(i)
        return out
