# Онбординг команды «Сократ» (SK01)

Как стянуть проект, как работать с ветками и как ставить задачи ИИ-агентам.
Общие правила для агентов — в [AGENTS.md](../AGENTS.md). Архитектура — в [ARCHITECTURE.md](../ARCHITECTURE.md).

## 1. Стянуть проект (Windows, PowerShell)

Нужен Python 3.11 или 3.12.

```powershell
git clone https://github.com/Zerrant2/AICaseSber.git
cd AICaseSber
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env
pytest -q -m "not llm and not network"
```

Если в конце выведено `16 passed`, установка прошла правильно. Число будет расти по мере добавления тестов.

macOS / Linux: `python3 -m venv .venv && source .venv/bin/activate`, `cp .env.example .env`.

### Что важно

- **Каждый создаёт своего тестового бота** в [@BotFather](https://t.me/BotFather) и кладёт его токен в свой `.env`. Если два процесса опрашивают один токен, Telegram выдаёт ошибку конфликта.
- `.env` **никогда не коммитим**. В нём токен бота, ключ LLM, пароль администратора и `APP_SECRET`.
- `APP_SECRET` генерируется так: `python -c "import secrets; print(secrets.token_urlsafe(48))"`.

## 2. Работа с ветками

```powershell
git checkout main
git pull
git checkout -b codex/bot-auth          # <agent>/<задача>
# ... работа агента ...
git add -A
git commit -m "feat(bot): login and sessions"
git push -u origin codex/bot-auth
```

Дальше:
1. На GitHub открыть **Pull Request в `main`**.
2. В описании PR указать номер задачи из ТЗ (например, `X2`, `G1`) и как проверить.
3. Сливает PR **только участник с Codex**.

Правила:
- Одна задача — одна ветка — один PR. Маленькие PR сливаются быстрее и без конфликтов.
- Раз в 2–3 часа: `git pull --rebase origin main`.
- Коммиты в стиле Conventional Commits: `feat(...)`, `fix(...)`, `test(...)`, `docs: ...`.
- Названия веток: `codex/...`, `gemini/...`, `claude/...`.

## 3. Как дать задачу агентам

### Codex (ChatGPT)

Codex сам читает `AGENTS.md` в корне репозитория. Если это облачный Codex, в настройках окружения укажите команду установки `pip install -e ".[dev]"`.

Первое сообщение агенту:

> Ты работаешь в репозитории AICaseSber. Прочитай AGENTS.md, ARCHITECTURE.md, docs/tasks/TASKS_CODEX.md и src/socrat/contracts/. Выполняй задачи из TASKS_CODEX.md строго по порядку «Порядок работы». Начни с X0: создай .github/workflows/ci.yml, затем X1. Работай только в своих директориях из AGENTS.md. Контракт не меняй. На каждую задачу — отдельная ветка `codex/<задача>` и отдельный PR, в описании укажи номер задачи и как проверить. Пока реальных модулей нет, используй фейки (USE_FAKES=true).

**Защиту `main`** участник с Codex включает сам, агент этого сделать не может:

- путь: GitHub → Settings → Branches → Add rule для `main`;
- «Require a pull request before merging»;
- «Require status checks to pass» → выбрать CI.

### Gemini (Antigravity)

В Antigravity откройте папку клона `AICaseSber`.

Первое сообщение агенту:

> Ты работаешь в репозитории AICaseSber. Прочитай GEMINI.md, AGENTS.md, ARCHITECTURE.md (разделы 2 и 6), docs/tasks/TASKS_GEMINI.md, src/socrat/contracts/normative.py, src/socrat/contracts/services.py (класс KnowledgeBase) и src/socrat/testing/fakes.py (FakeKnowledgeBase). Выполняй задачи из TASKS_GEMINI.md по разделу «Порядок и вехи». Начни с G1: только источники приоритета P1, и каждый URL открой и проверь, не придумывай ссылки. Работай только в src/socrat/knowledge/, data/knowledge/, scripts/knowledge_*.py, tests/unit/knowledge/. Первый PR (G1 → G2 → G3 → G5 для математики → минимальный G7) нужен к 24.09, 11:00, ветка `gemini/knowledge-core`.

Gemini Flash слабее на длинных задачах. Давайте ему **по одной задаче** (G1, потом G2 и так далее) и после каждой проверяйте, что `pytest` зелёный.

### Claude (архитектор)

Claude работает по `docs/tasks/TASKS_CLAUDE.md`: LLM-клиент, промпты, генератор, проверки, анализ ошибок, 12 тестов кейса. Свои ветки `claude/...` он передаёт через GitHub или через общую папку.

## 4. Чек-лист перед мёржем PR

- [ ] CI зелёный (ruff + pytest).
- [ ] PR не трогает чужие директории (таблица зон — в `AGENTS.md`).
- [ ] `src/socrat/contracts/` меняется **только** в PR от Claude с меткой `contract`.
- [ ] В описании есть «как проверить», и проверка действительно проходит.
- [ ] В коммитах нет `.env`, ключей, PDF из `data/knowledge/raw/`, базы `data/socrat.db`.
- [ ] Общие файлы (`pyproject.toml`, `.env.example`, `config.py`): при конфликте сохраняем изменения обеих сторон.

## 5. Вехи до сдачи (25.09, 09:00 МСК)

| Веха | Когда | Что должно работать |
|---|---|---|
| M1 | 24.09, 12:00 | Модули работают на фейках: вход, «Создать работу» → 2 docx, «Анализ ошибки»; каталог результатов по математике; генерация math-3 через LLM |
| M2 | 24.09, 18:00 | Всё соединено: реальные генератор и база в боте, админка, 👍/👎 |
| M3 | 24.09, 23:00 | Заморозка: 12 тестов кейса зелёные, examples/, README, демо-прогон |
| M4 | 25.09, до 08:00 | Только багфиксы, перенос на GitVerse, проверка в режиме инкогнито, отправка ссылки |
