"""Безопасный вычислитель арифметических выражений (AST, без eval) и проверки чисел.

Поддерживает: + − × · * : / ( ), целые и десятичные (точка или запятая), степень ^ / **,
унарный минус. Всё считается в Fraction — без ошибок округления.
"""

from __future__ import annotations

import ast
import re
from fractions import Fraction

_MAX_ABS = 10**12


class MathError(ValueError):
    pass


def normalize_expression(expr: str) -> str:
    e = expr.strip()
    e = e.replace("×", "*").replace("·", "*").replace("∙", "*").replace("х", "*").replace("x", "*")
    e = e.replace(":", "/").replace("÷", "/").replace("−", "-").replace("–", "-").replace("—", "-")
    e = e.replace("^", "**")
    e = re.sub(r"(?<=\d),(?=\d)", ".", e)  # десятичная запятая
    e = re.sub(r"(?<=\d)\s+(?=\d{3}\b)", "", e)  # 1 000 → 1000
    e = e.rstrip("=").strip()
    return e


def _eval(node: ast.AST) -> Fraction:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int | float)
        and not isinstance(node.value, bool)
    ):
        return Fraction(str(node.value))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
        v = _eval(node.operand)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp):
        a, b = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Add):
            r = a + b
        elif isinstance(node.op, ast.Sub):
            r = a - b
        elif isinstance(node.op, ast.Mult):
            r = a * b
        elif isinstance(node.op, ast.Div):
            if b == 0:
                raise MathError("деление на ноль")
            r = a / b
        elif isinstance(node.op, ast.Pow):
            if b.denominator != 1 or abs(b) > 10:
                raise MathError("недопустимая степень")
            r = a ** int(b)
        else:
            raise MathError("недопустимая операция")
        if abs(r) > _MAX_ABS:
            raise MathError("слишком большое число")
        return r
    raise MathError("недопустимый элемент выражения")


def evaluate(expr: str) -> Fraction:
    e = normalize_expression(expr)
    if not e or len(e) > 200:
        raise MathError("пустое или слишком длинное выражение")
    if not re.fullmatch(r"[\d\s.+\-*/()]+", e):
        raise MathError(f"в выражении есть посторонние символы: {expr!r}")
    try:
        tree = ast.parse(e, mode="eval")
    except SyntaxError as err:
        raise MathError(f"синтаксическая ошибка в {expr!r}") from err
    return _eval(tree)


def parse_number(text: str | None) -> Fraction | None:
    """Первое число в строке ('12', '12 карточек', '3,5 см', '47.') → Fraction; иначе None."""
    if text is None:
        return None
    m = re.search(r"-?\d[\d\s]*(?:[.,]\d+)?(?:/\d+)?", str(text))
    if not m:
        return None
    raw = m.group(0).replace(" ", "").replace(",", ".")
    try:
        return Fraction(raw)
    except (ValueError, ZeroDivisionError):
        return None


def fmt(v: Fraction) -> str:
    if v.denominator == 1:
        return str(v.numerator)
    as_float = float(v)
    s = f"{as_float:.4f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")


def numbers_in(text: str) -> list[Fraction]:
    out = []
    for m in re.finditer(r"(?<![\w.,])\d+(?:[.,]\d+)?", text or ""):
        try:
            out.append(Fraction(m.group(0).replace(",", ".")))
        except ValueError:
            continue
    return out


def operands(expr: str) -> list[Fraction]:
    return numbers_in(normalize_expression(expr).replace("*", " ").replace("/", " "))


# ------------------------------------------------------ детерминированная замена чисел (T11)


def parse_number_mapping(wish: str | None) -> dict[int, int]:
    """«замени 7 на 6, 8 на 9» / «7→6» → {7: 6, 8: 9}. Пустой словарь, если шаблона нет."""
    if not wish:
        return {}
    pairs = re.findall(r"(\d+)\s*(?:на|->|→|=>)\s*(\d+)", wish)
    return {int(a): int(b) for a, b in pairs}


def replace_numbers(text: str, mapping: dict[int, int]) -> str:
    if not mapping or not text:
        return text

    def sub(m: re.Match) -> str:
        v = int(m.group(0))
        return str(mapping.get(v, v))

    return re.sub(r"(?<![\d.,])\d+(?![\d.,]\d)", sub, text)


def answer_value(text: str | None) -> Fraction | None:
    """Итоговое число из ответа, даже если модель написала его фразой.

    «Ответ: 57 фломастеров» → 57; «Сначала 4 · 9 = 36, потом 36 − 12 = 24. Осталось 24 тетради» → 24;
    «Ошибка: сложил 6 и 5. Верно: 6 · 5 = 30 карандашей» → 30; «12» → 12. Часть после «проверка» игнорируется.
    """
    if not text:
        return None
    low = text.lower()
    m = re.findall(r"ответ\s*[:\-—]?\s*(-?\d[\d\s]*(?:[.,]\d+)?)", low)
    if m:
        return parse_number(m[-1])
    main = re.split(r"провер", low, maxsplit=1)[0]
    nums = numbers_in(main) or numbers_in(low)
    return nums[-1] if nums else None
