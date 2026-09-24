"""Промпты LLM (шаблоны Jinja2 в *.md рядом с этим файлом).

PROMPT_VERSION записывается в DiagnosticWork.meta.prompt_version — меняйте при правке промптов.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

PROMPT_VERSION = "1.3"
_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_DIR)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
        autoescape=False,
    )


def render(name: str, **ctx) -> str:
    """render("system_methodologist", grade=3, ...) → текст промпта. Комментарии {# #} вырезаются."""
    return _env().get_template(f"{name}.md").render(**ctx).strip()
