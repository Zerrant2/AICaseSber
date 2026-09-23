# ТЗ: Codex (ChatGPT) — бот, хранилище, экспорт в Word, сборка, мёржи

**Исполнитель:** Codex · **Оператор:** участник с ChatGPT (он же мёржит все PR).
**Твоя зона:**
- код: `src/socrat/bot/`, `src/socrat/storage/`, `src/socrat/export/`, `src/socrat/app.py`;
- сборка и CI: `Dockerfile`, `docker-compose.yml`, `.github/`;
- документация: `README.md`;
- тесты: `tests/unit/{bot,storage,export}/`.

**До начала прочитай:** `AGENTS.md`, `ARCHITECTURE.md` (разделы 1–4, 7, 10), `src/socrat/contracts/*`, `src/socrat/testing/fakes.py`.

> Главное: ты строишь всё на фейках (`USE_FAKES=true`) и не ждёшь реальный генератор и базу.
> Фейки реализуют те же протоколы, поэтому при переключении на реальные модули меняется только `app.py`.

---

## X0. Приём контракта и CI · 🔴 первым делом · ~1 ч

1. Проверь PR `contract/v1` от Claude:
   - `pip install -e ".[dev]" && pytest -q` — должно быть зелёным;
   - смёржи squash-ом в `main`.
2. В GitHub → Settings → Branches: защити `main` — только PR, обязательный зелёный CI.
3. Создай `.github/workflows/ci.yml`:
   - Python 3.12;
   - `pip install -e ".[dev]"`;
   - `ruff check src tests`;
   - `pytest -q -m "not llm and not network"`;
   - триггеры: `pull_request` и `push` в `main`.

**Готово, когда:** контракт в `main`, CI проходит на `main`.

## X1. Хранилище (`storage/`) · 🔴 · ~3 ч

Реализуй протоколы `TeacherRepository`, `WorkRepository`, `FeedbackRepository`, `UsageRepository` на SQLAlchemy 2 async + aiosqlite. Добавь хранилище сессий.

**Таблицы:**

| Таблица | Колонки |
|---|---|
| `teachers` | `id` PK, `nick` UNIQUE, `role`, `password_lookup` UNIQUE (HMAC-SHA256(APP_SECRET, password) hex), `password_hash` (argon2), `active`, `created_at`, `created_by` ('admin') |
| `sessions` | `chat_key` PK (HMAC-SHA256(APP_SECRET, str(chat_id))), `role`, `teacher_id` NULL, `nick`, `expires_at` |
| `login_attempts` | `chat_key`, `ts` — для ограничения перебора |
| `works` | `work_id` PK, `teacher_id` NULL, `created_at`, `grade`, `subject_id`, `work_json` (TEXT, `DiagnosticWork.model_dump_json()`) |
| `feedback` | `id`, `work_id`, `task_id`, `rating`, `comment`, `teacher_id`, `created_at` |
| `usage` | `id`, `kind`, `model`, `tokens_in`, `tokens_out`, `cost_usd`, `teacher_id`, `created_at` |

**Правила:**
- **Telegram ID в открытом виде нигде не хранить.** Только `chat_key` = HMAC.
- Пароль педагога генерирует `TeacherRepository.create`:
  - `GENERATED_PASSWORD_LENGTH` символов из алфавита без похожих символов (`abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789`);
  - `secrets.choice`;
  - возвращается в `NewTeacherCredentials.plain_password` **один раз**.
- `authenticate(password)`: найти строку по `password_lookup`, затем `argon2.verify(password_hash, password)`, затем проверить `active`.
- `FeedbackRepository.top_examples(grade, subject_id, k)`: задания с 👍 и без 👎 из работ того же класса и предмета, сначала свежие.
- `UsageRepository.summary(days)` — агрегаты за период.
- Создание таблиц — `await init_db(engine)` через `metadata.create_all` при старте. Alembic не нужен.

**Файлы:** `storage/db.py` (engine, session factory, init_db), `storage/models.py` (ORM), `storage/repositories.py`, `storage/sessions.py` (`SessionStore`: `get(chat_id)`, `create(chat_id, SessionInfo)`, `delete(chat_id)`, `register_failed_attempt(chat_id) -> bool locked`).

**Тесты** (`tests/unit/storage/`) на SQLite в памяти:
- create → authenticate;
- неверный пароль → None;
- после сброса старый пароль не работает;
- отключённый педагог не входит;
- top_examples;
- истечение сессии.

## X2. Вход и сессии (`bot/`) · 🔴 · ~2 ч

- `bot/main.py`: `python -m socrat.bot` запускает long polling. Services собираются через `socrat.app.build_services()`.
- **Middleware `AuthMiddleware`:** по `chat_id` достаёт сессию из `SessionStore` и кладёт `SessionInfo` в `data["session"]`. Без сессии пропускает только `/start` и ввод пароля.
- **Сценарий входа:**
  1. `/start` → «Здравствуйте! Это «Сократ» — помощник педагога для диагностических работ. Введите пароль.»
  2. Пользователь присылает пароль → бот **сразу удаляет это сообщение** (`message.delete()`, ошибки удаления игнорировать).
  3. Совпадение с `ADMIN_PASSWORD` (через `secrets.compare_digest`) → сессия `Role.ADMIN`, ник «Администратор».
  4. Иначе `teachers.authenticate()` → сессия с ролью педагога.
  5. Неудача → «Пароль не подошёл». После `LOGIN_MAX_ATTEMPTS` неудач — блокировка на `LOGIN_LOCKOUT_MINUTES`.
  6. Сессия живёт `SESSION_TTL_HOURS`. `/logout` и кнопка «🚪 Выйти» удаляют её.
- **Главное меню** (reply keyboard):
  - педагог: «📝 Создать работу», «🔍 Анализ типичной ошибки», «ℹ️ Границы и источники», «🚪 Выйти»;
  - админ: то же + «⚙️ Админ-панель».
- Все тексты бота — в `bot/texts.py` (одно место для правок формулировок).

## X3. Админ-панель · 🟡 · ~2 ч

Inline-меню «⚙️ Админ-панель»:

- **➕ Добавить педагога.** Ник (2–64 символа) → роль кнопками (Педагог / Методист / Педагог-психолог) → `teachers.create`. Бот показывает:
  > Педагог «ник» создан. Пароль: `XXXX` — передайте его лично, повторно он показан не будет.

  Сообщение с паролем удалить через 5 минут (`asyncio` таймер, ошибки игнорировать).
- **👥 Педагоги** — список (ник, роль, активен). У каждого кнопки: 🔁 Сбросить пароль · 🚫 Отключить / ✅ Включить · 🎭 Сменить роль.
- **📚 Нормативная база:**
  - «Статус» → `knowledge.status()`: документы, фрагменты, результаты, проблемы;
  - «🔄 Обновить» → `await knowledge.update(progress)` с прогрессом в одном сообщении;
  - «📎 Загрузить материал школы» → принять документ PDF/DOCX до 20 МБ → `knowledge.ingest_school_material(filename, bytes, progress)`. Предупредить: материал используется как контекст, не как нормативный источник.
- **💰 Расходы** → `usage.summary(30)`: запросы, токены, $.
- **💳 Оплата** → «Скоро».

## X4. Сценарий «Создать работу» (FSM) · 🔴 · ~4 ч

Состояния и кнопки (inline, с «◀ Назад» на каждом шаге):

1. **Класс:** 1…11 (сетка 4×3).
2. **Предмет:** кнопки из `knowledge.list_subjects(grade)`. Если список пуст — сообщение про `KNOWLEDGE_EMPTY`.
3. **Тема:** свободный текст (подсказка «Например: *Внетабличное умножение и деление*»). Затем `knowledge.check_topic(...)`:
   - `in_program=false` → «Тема не найдена в программе N класса по предмету. Близкие темы:» + кнопки `suggestions` + «Всё равно продолжить с моей темой».
4. **Уровень:** Лёгкий / Базовый / Сложный / **Все три уровня**.
5. **Что диагностируем (УУД):** Познавательные / Регулятивные / Коммуникативные / **Сбалансированно** (по умолчанию).
6. **Число заданий:** 1…8. Рядом с каждой кнопкой расчётное время. Сейчас формула `4 + n × 3.5` мин, потом возьми `generator.check_request(...).estimated_minutes`.
7. **Подтверждение:** сводка всех параметров + «✅ Сгенерировать» / «✏️ Изменить».
8. `generator.check_request(req)`:
   - `blocked` → показать `message_ru` всех issues и кнопки `suggestions`, вернуть на нужный шаг;
   - `WARN` → показать и дать «Продолжить» / «Изменить».
9. **Генерация в фоне:**
   - одно сообщение «⏳ Готовлю работу…» — редактируется колбэком `progress(text)`, не чаще раза в 1.5 с;
   - `ChatAction.UPLOAD_DOCUMENT`;
   - глобальный `asyncio.Semaphore(MAX_CONCURRENT_GENERATIONS)`, у пользователя не больше одной генерации одновременно.
10. **Результат:**
    - `works.save(work, teacher_id)`;
    - `usage.add(UsageRecord(kind="generation", model=work.meta.model, tokens_in=…, tokens_out=…, cost_usd=…, teacher_id=…))`;
    - два документа через `BufferedInputFile(exporter.student_docx(work), filename)`;
    - сводка: для каждого варианта — время по `time_plan`, покрытые группы УУД (`work.coverage`), предупреждения (`work.warnings`);
    - кнопки: «👍/👎 по заданиям», «🔁 Перегенерировать задание», «🧪 Разобрать ответ ученика».
11. **Ошибки:**
    - `GenerationError` → показать `message_ru`, кнопка «Повторить»;
    - прочие исключения → «Что-то пошло не так, попробуйте ещё раз» + лог с трассировкой (без текста темы полностью).

Сбор `GenerationRequest`: `subject_name` берётся из `Subject.name`, `author_role` — из сессии.

## X5. Экспорт в Word (`export/`) · 🔴 · ~4 ч

Реализуй протокол `Exporter` в `export/docx_exporter.py` (python-docx). Шрифт Times New Roman 12 pt, поля 2 см, A4.

**`student_docx(work)`** — строить **только** из `socrat.contracts.student_view(work)`:

```
Имя ______________   Класс ______   Дата __________
{title}                                    (жирный, 14 pt)
{subject_name}, {grade} класс · {label}    («Вариант А» — НИКАКИХ «лёгкий/сложный»)
{instruction}                              (курсив)

Задание 1
{text}
{support}                                  (если есть; курсив, серый, с префиксом «Подсказка: » если его нет)
________________________________________   × answer_lines строк
...
Подумай и ответь                            (заголовок рефлексии)
• {question}
________________________________________   × 2 строки на вопрос
```

- Каждый следующий вариант — с новой страницы.
- Ни одного поля, кроме `student_view`, в этот документ не попадает.

**`teacher_docx(work)`:**

1. **Титул:** «Методические материалы для учителя», тема, предмет, класс, дата. Параметры запроса: уровень, фокус УУД, число заданий.
2. **Ограничения** — список `work.limitations`, рамкой.
3. Для каждого варианта (заголовок «Вариант А — лёгкий уровень»; **здесь** уровень можно называть):
   - `level_rationale`;
   - таблица плана времени: чтение / задания / рефлексия / итого + `basis`;
   - для каждого задания:
     - формулировка, как у ученика;
     - **Цели:** предметная (`subject_goal`), метапредметная (`meta_goal`);
     - **Ответ:** `expected_answer`; решение по шагам (`solution_steps[].text`); «Допустимые другие способы» (`alternative_solutions`);
     - **Карта результатов** — таблица: *Тип · ID (внутр.) · Формулировка · Источник · Раздел · Стр. · Почему соответствует · ✓ проверено*;
     - **Наблюдаемые признаки УУД** — таблица: *Группа · Действие · Основание в задании (trigger) · Что считать проявлением (evidence) · Подсказки по шкале*;
     - **Личностная направленность** — `personal_orientation` + фраза «балл не ставится»;
     - **Типичные ошибки** — *Как выглядит · Слой · Возможное объяснение · Что сделать*;
     - **Устные вопросы**, **Приёмы** (названия по `technique_ids` из `data/techniques.json`), **Как проводить** (`conducting_note`);
     - **Автопроверки** (`checks`): «Арифметика проверена кодом ✓» и т.п.
   - **Рефлексия:** вопросы, `reading_guide`, `note`.
4. **Шкала наблюдений** (из кейса): «действие наблюдается / частично / не показано / нет возможности наблюдать» + 6 правил из `data/reference/sk01/observation_rules.json`.
5. **Источники:** `work.sources` — название, URL, примечание. Фраза: «Коды результатов — внутренние идентификаторы системы, не номера пунктов ФГОС.»

**`analysis_docx(result)`:** описание ошибки (ввод педагога) → summary → гипотезы (слой, группа УУД, формулировка, уверенность, что проверить) → приёмы → мини-задания → вопрос для рефлексии педагога → ограничения.

**`filenames(work)`:** `Задания_{grade}кл_{Предмет}_{тема≤40, без запрещённых символов}.docx` и `Методичка_…docx`.

**Тесты:**
- на фикстуре `data/fixtures/work_math3_all.json` оба файла создаются;
- **в тексте ученического docx нет ни одного `solution_steps[].text`, `expected_answer` в виде «Ответ:», слов «Лёгкий/Базовый/Сложный»**;
- в методичке есть все `outcome_id` и страницы.

## X6. Сценарий «Анализ типичной ошибки» · 🔴 · ~2 ч

Класс → предмет → тема (без проверки программы, но с `check_topic` для подсказки) → «Опишите, какую ошибку делают дети и в каких заданиях. **Без имён и фамилий.**» →
- `analyzer.check(req)` → WARN или BLOCK, как в X4;
- `analyzer.analyze(req, progress)` → сообщение, собранное из `ErrorAnalysisResult`:
  ```
  🔍 Вероятная картина: {summary}
  Гипотезы: 1) [Концептуальное заблуждение · познавательные УУД] ... (уверенность: средняя)
     Проверить: ...
  Приёмы на следующий урок: • «Лови ошибку» — как применить...
  Мини-проверка: ...
  Вопрос для вас: ...
  ⚠️ Ограничения: ...
  ```
- кнопка «📄 Скачать .docx» → `exporter.analysis_docx`;
- `usage.add(kind="analysis")`.

Длинные сообщения режь по 4000 символов. Используй `ParseMode.HTML` и экранируй ввод.

## X7. Обратная связь, перегенерация, разбор ответа · 🟡 · ~3 ч

- **👍/👎:**
  - на каждый вариант отдельное сообщение с рядами `A-1: 👍 👎`;
  - `callback_data = f"fb:{work_id}:{task_id}:{up|down}"` (≤ 64 байт);
  - после 👎 — необязательный комментарий «Что не так?» (кнопка «Пропустить»);
  - `feedback.add(...)`.
- **🔁 Перегенерировать:**
  - выбрать задание → необязательное пожелание текстом;
  - `generator.regenerate_task(work, level, number, wish, progress)`;
  - сохранить, прислать оба документа заново.
- **🧪 Разобрать ответ ученика:**
  - выбрать задание → «Вставьте ответ ученика **без имени**» → `observer.observe(task, text)` → `summary_ru` + строки по УУД (статус по-русски из `LABELS_RU`, основание, альтернативные объяснения) + `limitations`;
  - «Разобрать ответ на рефлексию» → `observer.observe_reflection(question, text)`.

## X8. Composition root `app.py` · 🔴 · ~1 ч

```python
async def build_services(settings: Settings) -> Services: ...
```
- `USE_FAKES=true` → все фейки из `socrat.testing.fakes`; репозитории — реальные SQLite, чтобы проверить вход.
- Иначе реальные модули:
  - `knowledge`: `socrat.knowledge.LocalKnowledgeBase(settings)`;
  - `generator`, `analyzer`, `observer`: `socrat.core.build_core(settings, knowledge, feedback_repo, usage_repo)`.
- Если реальный модуль ещё не смёржен (`ImportError`) — лог WARNING и фейк. Так интеграция не блокирует демо.

## X9. Docker, README, запуск · 🟡 · ~2 ч

- `Dockerfile` (python:3.12-slim, `pip install .`, `CMD ["python", "-m", "socrat.bot"]`).
- `docker-compose.yml`:
  - сервис `bot`, `env_file: .env`, том `./data:/app/data`;
  - закомментированный сервис `ollama` для локальной модели.
- **README** (финальный к сдаче):
  - команда T004 Fazbear Entertainment, Inc. — состав: Калюжный А. О., Редняков Д. А., Звонарёв И. С.;
  - задача;
  - что реализовано;
  - как запустить: локально и Docker; как получить токен бота и ключ OpenRouter;
  - **какой сценарий проверить первым**: «математика, 3 класс, умножение и деление, все три уровня, 4 задания»;
  - ссылки на ARCHITECTURE, отчёт тестов, examples;
  - ограничения; стоимость (Claude даст цифры).

## X10. Мёржи и интеграция · 🔴 постоянно

- Смотри PR каждые 1–2 часа. Мёрж, если: CI зелёный, PR не трогает чужие зоны (контракт — только от Claude), в описании есть «как проверить».
- При конфликтах в общих файлах (`pyproject.toml`, `.env.example`, `config.py`) сливай обе стороны.
- **24.09 к 18:00 — интеграционный прогон:** `USE_FAKES=false`, реальный токен и ключ, оба сценария + админка. Баги — issues с меткой владельца.

---

### Порядок работы

X0 → X1 → X2 → X8 → X4 + X5 (параллельно) → X6 → X3 → X7 → X9 → X10

**К вехе M1 (24.09, 12:00):** на фейках работают вход, «Создать работу» с двумя docx и «Анализ ошибки».
