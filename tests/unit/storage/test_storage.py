"""Storage behavior on SQLite in memory, with no Telegram or LLM calls."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from socrat.config import get_settings
from socrat.contracts import (
    DiagnosticWork,
    Feedback,
    FeedbackRepository,
    Rating,
    Role,
    SessionInfo,
    TeacherRepository,
    UsageRecord,
    UsageRepository,
    WorkRepository,
)
from socrat.storage import (
    SessionStore,
    SqlFeedbackRepository,
    SqlTeacherRepository,
    SqlUsageRepository,
    SqlWorkRepository,
    create_engine,
    init_db,
    session_factory,
)
from socrat.storage.models import SessionRow, TeacherRow

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
async def storage(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple]:
    monkeypatch.setenv("APP_SECRET", "test-only-secret-for-storage")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("LOGIN_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("LOGIN_LOCKOUT_MINUTES", "10")
    monkeypatch.setenv("LLM_PRICE_IN_PER_1M", "0")
    monkeypatch.setenv("LLM_PRICE_OUT_PER_1M", "0")
    get_settings.cache_clear()
    settings = get_settings()
    engine = create_engine(settings)
    await init_db(engine)
    sessions = session_factory(engine)
    yield (
        sessions,
        SqlTeacherRepository(sessions, settings),
        SqlWorkRepository(sessions),
        SqlFeedbackRepository(sessions),
        SqlUsageRepository(sessions),
        SessionStore(sessions, settings),
    )
    await engine.dispose()
    get_settings.cache_clear()


def fixture_work() -> DiagnosticWork:
    return DiagnosticWork.model_validate_json(
        (ROOT / "data" / "fixtures" / "work_math3_all.json").read_text(encoding="utf-8")
    )


async def test_teacher_create_authenticate_reset_and_disable(storage: tuple) -> None:
    sessions, teachers, _, _, _, _ = storage
    assert isinstance(teachers, TeacherRepository)
    credentials = await teachers.create(" teacher ", Role.TEACHER)
    assert credentials.account.nick == "teacher"
    assert len(credentials.plain_password) == get_settings().generated_password_length
    assert await teachers.authenticate("wrong password") is None
    assert await teachers.authenticate(credentials.plain_password) == credentials.account

    async with sessions() as session:
        row = await session.scalar(select(TeacherRow))
        assert row is not None
        assert row.password_lookup != credentials.plain_password
        assert row.password_hash.startswith("$argon2id$")
        assert row.created_by == "admin"

    reset = await teachers.reset_password(credentials.account.teacher_id)
    assert reset.plain_password != credentials.plain_password
    assert await teachers.authenticate(credentials.plain_password) is None
    assert await teachers.authenticate(reset.plain_password) is not None
    await teachers.set_active(credentials.account.teacher_id, False)
    assert await teachers.authenticate(reset.plain_password) is None
    assert await teachers.list() == []
    assert len(await teachers.list(include_inactive=True)) == 1
    await teachers.set_role(credentials.account.teacher_id, Role.METHODIST)
    assert (await teachers.get(credentials.account.teacher_id)).role == Role.METHODIST


async def test_work_feedback_filters_downvotes_and_wrong_subject(storage: tuple) -> None:
    _, _, works, feedback, _, _ = storage
    assert isinstance(works, WorkRepository)
    assert isinstance(feedback, FeedbackRepository)
    work = fixture_work()
    await works.save(work, teacher_id=None)
    assert (await works.get(work.work_id)).work_id == work.work_id

    other = work.model_copy(deep=True)
    other.work_id = "other-grade"
    other.request.grade = 4
    await works.save(other, teacher_id=None)

    now = datetime.now(UTC)
    await feedback.add(Feedback(work_id=work.work_id, task_id="A-1", rating=Rating.UP, created_at=now))
    await feedback.add(Feedback(work_id=work.work_id, task_id="A-1", rating=Rating.DOWN, created_at=now))
    await feedback.add(Feedback(work_id=work.work_id, task_id="A-2", rating=Rating.UP, created_at=now))
    await feedback.add(
        Feedback(
            work_id=other.work_id,
            task_id="A-3",
            rating=Rating.UP,
            created_at=now + timedelta(seconds=1),
        )
    )
    assert [task.task_id for task in await feedback.top_examples(3, "math")] == ["A-2"]
    assert [task.task_id for task in await feedback.top_examples(4, "math")] == ["A-3"]
    assert await feedback.stats() == {Rating.UP: 3, Rating.DOWN: 1}


async def test_session_expiry_hmac_and_login_lockout(storage: tuple) -> None:
    sessions, _, _, _, _, session_store = storage
    chat_id = 123456789
    now = datetime.now(UTC)
    info = SessionInfo(role=Role.ADMIN, nick="Администратор", expires_at=now + timedelta(hours=1))
    await session_store.create(chat_id, info)
    assert (await session_store.get(chat_id)).role == Role.ADMIN
    async with sessions() as session:
        row = await session.scalar(select(SessionRow))
        assert row is not None
        assert row.chat_key != str(chat_id)
        assert len(row.chat_key) == 64
        assert str(chat_id) not in json.dumps(row.__dict__, default=str)

    assert not await session_store.register_failed_attempt(chat_id)
    assert not await session_store.register_failed_attempt(chat_id)
    assert await session_store.register_failed_attempt(chat_id)
    assert await session_store.is_locked(chat_id)
    await session_store.clear_failed_attempts(chat_id)
    assert not await session_store.is_locked(chat_id)

    expired = info.model_copy(update={"expires_at": now - timedelta(seconds=1)})
    await session_store.create(chat_id, expired)
    assert await session_store.get(chat_id) is None
    await session_store.create(chat_id, info)
    await session_store.delete(chat_id)
    assert await session_store.get(chat_id) is None


async def test_usage_summary_respects_period(storage: tuple) -> None:
    _, _, _, _, usage, _ = storage
    assert isinstance(usage, UsageRepository)
    now = datetime.now(UTC)
    await usage.add(UsageRecord(kind="generation", model="fake", tokens_in=100, tokens_out=40, cost_usd=0.02))
    await usage.add(UsageRecord(kind="analysis", model="fake", tokens_in=20, tokens_out=10, cost_usd=None))
    await usage.add(
        UsageRecord(kind="generation", model="fake", tokens_in=1000, created_at=now - timedelta(days=40))
    )
    summary = await usage.summary(30)
    assert (summary.requests, summary.tokens_in, summary.tokens_out) == (2, 120, 50)
    assert summary.cost_usd == pytest.approx(0.02)
    assert summary.by_kind == {"generation": 1, "analysis": 1}
