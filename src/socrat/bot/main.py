"""Start aiogram long polling with persistent login state."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher

from socrat.config import get_settings
from socrat.storage import SessionStore, create_engine, init_db, session_factory

from . import admin_panel, analysis_flow, auth, feedback_flow, work_flow
from .middleware import AuthMiddleware

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    token = settings.telegram_bot_token.get_secret_value()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
    logging.basicConfig(level=settings.log_level)
    from socrat.app import build_services, close_services

    services = await build_services(settings)
    engine = create_engine(settings)
    await init_db(engine)
    sessions = SessionStore(session_factory(engine), settings)
    dispatcher = Dispatcher()
    middleware = AuthMiddleware(sessions)
    dispatcher.message.outer_middleware(middleware)
    dispatcher.callback_query.outer_middleware(middleware)
    dispatcher.include_router(auth.router)
    dispatcher.include_router(admin_panel.router)
    dispatcher.include_router(work_flow.router)
    dispatcher.include_router(analysis_flow.router)
    dispatcher.include_router(feedback_flow.router)
    dispatcher["services"] = services
    dispatcher["settings"] = settings
    dispatcher["generation_controller"] = work_flow.GenerationController(settings.max_concurrent_generations)
    bot = Bot(token=token)
    try:
        logger.info("Starting Telegram long polling")
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()
        await engine.dispose()
        await close_services(services)


def run() -> None:
    asyncio.run(main())
