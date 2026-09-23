# AGENTS.md — правила для всех ИИ-агентов проекта «Сократ»

Этот файл читают Codex (ChatGPT), Antigravity (Gemini) и Claude. **Прочитай его целиком до первой правки.**

## 0. Кто ты и что делаешь

| Агент | Человек-оператор | ТЗ | Твои директории |
|---|---|---|---|
| **Codex** (ChatGPT) | участник с ChatGPT — он же **мёржит PR** | `docs/tasks/TASKS_CODEX.md` | `src/socrat/bot/`, `storage/`, `export/`, `app.py`, `Dockerfile`, `docker-compose.yml`, `.github/`, `README.md`, `tests/unit/{bot,storage,export}/` |
| **Gemini** (Antigravity) | участник с Gemini | `docs/tasks/TASKS_GEMINI.md` | `src/socrat/knowledge/`, `data/knowledge/`, `scripts/knowledge_*.py`, `tests/unit/knowledge/` |
| **Claude** | архитектор | `docs/tasks/TASKS_CLAUDE.md` | `src/socrat/contracts/`, `llm/`, `core/`, `prompts/`, `testing/`, `data/fixtures/`, `data/techniques.json`, `tests/contract/`, `tests/case_sk01/`, `ARCHITECTURE.md`, `AGENTS.md` |

**Не редактируй чужие директории.** Если нужна правка у соседа, напиши в описании PR или в `docs/contract_change_requests.md`.

## 1. Сначала прочитай

1. `ARCHITECTURE.md` — что строим, поток данных, принципы P1–P8.
2. `src/socrat/contracts/` — все модели и интерфейсы. **Импортируй модели только из `socrat.contracts`.**
3. Своё ТЗ в `docs/tasks/`.
4. `src/socrat/testing/fakes.py` — фейки соседей. Разрабатывай и тестируй свой модуль на них.

## 2. Жёсткие правила продукта

- **Данные детей не храним и не обрабатываем.** Никаких полей «ФИО ученика». Педагог хранится как ник + роль + хэш пароля.
- **Нормы не выдумываем.** В методичку и ответы бота попадают только результаты, которые есть в каталоге `knowledge`, с источником, разделом и страницей. Внутренние ID (P01, `math-3-P-07`) **не называть номерами пунктов ФГОС**.
- **Ученик никогда не видит ответов.** Ученический документ строится только из `socrat.contracts.student_view(work)`.
- **Уровни** в ученическом листе называются «Вариант А / Б / В», без слов «лёгкий» и «сложный».
- **Нет диагнозов**, оценок личности, мотивации, интеллекта. Рефлексия фиксируется нейтрально.
- **Интерфейс и тексты — на русском.** Код, имена переменных и коммиты — на английском; docstring-и можно на русском.

## 3. Git

- Ветка: `<agent>/<short-task>` (`codex/bot-auth`, `gemini/knowledge-parser`, `claude/core-generator`).
- Перед PR выполни:
  ```bash
  pip install -e ".[dev]"
  ruff check src tests && ruff format --check src tests
  pytest -q -m "not llm and not network"
  ```
- PR маленькие и частые, 1 PR ≈ 1 задача из ТЗ. В описании PR: что сделано, как проверить, какие задачи ТЗ закрыты (например, `G2`, `X4`).
- Коммиты: `feat(bot): ...`, `fix(knowledge): ...`, `test(core): ...`, `docs: ...`.
- `git pull --rebase origin main` — минимум раз в 2–3 часа.
- **Никогда:** `git push --force` в `main`, коммит `.env`, ключей, PDF-файлов из `data/knowledge/raw/`, базы `data/socrat.db`.

## 4. Код

- Python ≥ 3.11, типизация везде, `async` для ввода-вывода.
- Настройки только через `socrat.config.get_settings()`. Новая переменная → `config.py` + `.env.example` в том же PR.
- Логи через `logging.getLogger(__name__)`, без `print`. **Не логировать** пароли, токены и полные тексты вводов педагога (не больше 80 символов).
- Любая внешняя операция (HTTP, LLM) — с таймаутом и понятной ошибкой для пользователя.
- Тесты: `tests/unit/<module>/test_*.py`. Тесты, которые ходят в сеть или в LLM, помечай `@pytest.mark.network` / `@pytest.mark.llm`.

## 5. Если что-то непонятно

1. Проверь `ARCHITECTURE.md` и контракт.
2. Прими разумное решение, запиши его одной строкой в `docs/decisions.md` (дата, кто, что, почему) и продолжай.
3. Если решение меняет контракт или чужой модуль — остановись и опиши вопрос в PR или в `docs/contract_change_requests.md`.
