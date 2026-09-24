"""Пожелание учителя к заданию: какие действия он просит использовать или убрать.

«Замени на вычитание и сложение» → требуются «+» и «−»; «Замени умножение на деление» → требуется «:»,
умножение в решении запрещено; «Без деления» → деление запрещено. Проверка — кодом по шагам решения
(шаги «Проверка…» не считаются: проверку сложением нельзя выдать за задачу на сложение).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from socrat.contracts import Task

from .mathcheck import normalize_expression

OP_PATTERNS: dict[str, str] = {
    "+": r"\b(сложени\w*|сложи\w*|сложить|прибав\w*|плюс\w*|сумм\w*)",
    "-": r"\b(вычитани\w*|вычесть|вычти\w*|вычита\w*|отним\w*|отнять|отнима\w*|минус\w*|разност\w*)",
    "*": r"\b(умнож\w*|произведени\w*)",
    "/": r"\b(делени\w*|раздел\w*|подели\w*|делить|дели\b|частно\w*)",
}
OP_RU = {"+": "сложение", "-": "вычитание", "*": "умножение", "/": "деление"}
_ORDER = "+-*/"


@dataclass
class OperationWish:
    required: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.required or self.forbidden)

    def describe_ru(self) -> str:
        parts = []
        if self.required:
            parts.append("в решении обязательно должны быть действия: " + _join(self.required))
        if self.forbidden:
            parts.append("в решении не должно быть действий: " + _join(self.forbidden))
        return "; ".join(parts)


def _join(ops: list[str]) -> str:
    return " и ".join(OP_RU[o] for o in ops)


def _ops_in(text: str) -> list[str]:
    return [op for op in _ORDER if re.search(OP_PATTERNS[op], text)]


def parse_operation_wish(wish: str | None) -> OperationWish:
    if not wish:
        return OperationWish()
    low = wish.lower().replace("ё", "е")
    required: list[str] = []
    forbidden: list[str] = []
    m = re.search(r"\b(?:замени\w*|поменя\w*|вместо)\b\s*(.*?)\s+\bна\b\s+(.+)", low)
    if m and _ops_in(m.group(1)):
        forbidden += _ops_in(m.group(1))
        low = m.group(2)
    for op in _ORDER:
        for hit in re.finditer(OP_PATTERNS[op], low):
            before = low[max(0, hit.start() - 25) : hit.start()]
            if re.search(r"\b(без|не|кроме|убери|убрать)\s+(\w+\s+)?$", before):
                if op not in forbidden:
                    forbidden.append(op)
            elif op not in required:
                required.append(op)
    forbidden = [op for op in forbidden if op not in required]
    return OperationWish(required=required, forbidden=forbidden)


def main_operations(task: Task) -> set[str]:
    """Действия в шагах решения (без шагов проверки)."""
    ops: set[str] = set()
    for step in task.solution_steps:
        if not step.expression or (step.text or "").strip().lower().startswith("провер"):
            continue
        expr = normalize_expression(step.expression)
        ops |= {op for op in _ORDER if op in expr.lstrip("-")}
    return ops


def check_operations(task: Task, wish: OperationWish) -> list[str]:
    if not wish:
        return []
    used = main_operations(task)
    errors = []
    missing = [op for op in wish.required if op not in used]
    extra = [op for op in wish.forbidden if op in used]
    if missing:
        errors.append(
            f"Задание {task.number}: учитель просил, чтобы {wish.describe_ru()}. В шагах решения (не считая "
            f"проверки) нет действия: {_join(missing)}. Составь другую задачу, решение которой по смыслу "
            "требует этих действий; упоминание в проверке не считается."
        )
    if extra:
        errors.append(
            f"Задание {task.number}: учитель просил убрать действие «{_join(extra)}», а решение его использует."
        )
    return errors
