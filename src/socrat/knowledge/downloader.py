"""Загрузчик нормативных документов.

Скачивает документы по sources.yaml, проверяет sha256, ведёт manifest.json.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pymupdf
import yaml

from socrat.config import Settings
from socrat.contracts import (
    ProgressCallback,
    UpdateReport,
)

logger = logging.getLogger(__name__)

USER_AGENT = "SocratBot/0.1 (education prototype)"
DOWNLOAD_TIMEOUT_S = 60.0
CONNECT_TIMEOUT_S = 10.0
MAX_RETRIES = 3
MAX_CONNECT_RETRIES = 2  # недоступный хост не должен держать обновление минутами (issue #24)
MAX_PARALLEL_DOWNLOADS = 4


def compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_sources_registry(sources_file: Path) -> list[dict[str, Any]]:
    if not sources_file.exists():
        logger.warning("Sources file not found: %s", sources_file)
        return []
    with open(sources_file, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("sources", [])


def load_manifest(manifest_path: Path) -> dict[str, dict[str, Any]]:
    if not manifest_path.exists():
        return {}
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            # Можем хранить {"documents": {id: doc}} или {id: doc}
            return data.get("documents", data)
    except Exception as e:
        logger.warning("Could not read manifest at %s: %e", manifest_path, e)
    return {}


def save_manifest(manifest_path: Path, manifest_docs: dict[str, dict[str, Any]]) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_data = {
        "updated_at": datetime.now(UTC).isoformat(),
        "documents": manifest_docs,
    }
    manifest_path.write_text(json.dumps(manifest_data, ensure_ascii=False, indent=2), encoding="utf-8")


async def download_single_source(
    client: httpx.AsyncClient,
    source: dict[str, Any],
    raw_dir: Path,
) -> tuple[str, bytes | None, str | None]:
    """Скачивает один документ с 3 попытками и экспоненциальной паузой.

    Возвращает (source_id, content, error_reason).
    """
    source_id = source["source_id"]
    url = source["url"]

    # Pravo.gov.ru или HTML страницы могут не отдавать прямой PDF
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                content = resp.content
                # Проверим, что это действительно PDF по сигнатуре, если ожидался PDF
                if not content.startswith(b"%PDF-"):
                    # HTML-страница (например, pravo.gov.ru Document/View): повторять бессмысленно
                    return (
                        source_id,
                        None,
                        f"Ответ не PDF (HTTP 200, {len(content)} байт) — нужна прямая ссылка на PDF",
                    )
                return source_id, content, None
            if resp.status_code in (401, 403, 404):
                return source_id, None, f"HTTP {resp.status_code}"
            # Для 5xx делаем повтор
            logger.warning("Attempt %d for %s failed with status %d", attempt, source_id, resp.status_code)
        except (httpx.ConnectTimeout, httpx.ConnectError) as exc:
            logger.warning("Attempt %d for %s: cannot connect: %s", attempt, source_id, exc)
            if attempt >= MAX_CONNECT_RETRIES:
                return source_id, None, f"Сайт недоступен: {exc.__class__.__name__}"
        except httpx.RequestError as exc:
            logger.warning("Attempt %d for %s failed with network error: %s", attempt, source_id, exc)
            if attempt == MAX_RETRIES:
                return source_id, None, f"Ошибка сети: {exc.__class__.__name__}"
        if attempt < MAX_RETRIES:
            await asyncio.sleep(1.0 * (2 ** (attempt - 1)))

    return source_id, None, "Превышено число попыток"


async def download_all(
    settings: Settings,
    progress: ProgressCallback | None = None,
    source_ids: list[str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> UpdateReport:
    """Скачивает источники по sources.yaml параллельно и обновляет manifest.json.

    Источники с `download: false` (ссылки на страницы просмотра) не скачиваются.
    Один недоступный сайт не блокирует остальные (issue #24).
    """
    start_time = datetime.now(UTC)
    raw_dir = settings.knowledge_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = settings.knowledge_dir / "manifest.json"

    sources = load_sources_registry(settings.sources_file)
    if source_ids:
        sources = [s for s in sources if s["source_id"] in source_ids]

    manifest_docs = load_manifest(manifest_path)

    downloaded: list[str] = []
    unchanged: list[str] = []
    failed: dict[str, str] = {}

    to_download = [s for s in sources if s.get("download", True) is not False]
    for s in sources:
        if s.get("download", True) is False:
            unchanged.append(s["source_id"])  # только ссылка, файл не нужен

    headers = {"User-Agent": USER_AGENT}
    timeout = httpx.Timeout(DOWNLOAD_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)
    sem = asyncio.Semaphore(MAX_PARALLEL_DOWNLOADS)
    done = 0
    total = len(to_download)
    lock = asyncio.Lock()

    async def one(client: httpx.AsyncClient, src: dict[str, Any]) -> None:
        nonlocal done
        sid = src["source_id"]
        local_file = raw_dir / f"{sid}.pdf"
        async with sem:
            _, content, err = await download_single_source(client, src, raw_dir)
        async with lock:
            done += 1
            if err:
                # Если сеть вернула ошибку, но файл уже есть локально, не ломаем локальное состояние
                if local_file.exists():
                    existing_data = local_file.read_bytes()
                    unchanged.append(sid)
                    if sid not in manifest_docs:
                        manifest_docs[sid] = _build_manifest_entry(
                            src, existing_data, compute_sha256(existing_data), local_file
                        )
                else:
                    failed[sid] = err
            else:
                assert content is not None
                file_sha = compute_sha256(content)
                old_sha = manifest_docs.get(sid, {}).get("sha256")
                if old_sha == file_sha and local_file.exists():
                    unchanged.append(sid)
                else:
                    local_file.write_bytes(content)
                    downloaded.append(sid)
                    manifest_docs[sid] = _build_manifest_entry(src, content, file_sha, local_file)
            if progress:
                status = "ошибка" if sid in failed else "готово"
                await progress(f"Скачивание {done}/{total}: {src.get('title', sid)} — {status}")

    async with httpx.AsyncClient(
        headers=headers, follow_redirects=True, timeout=timeout, transport=transport
    ) as client:
        if progress:
            await progress(f"Скачиваю {total} документов (до {MAX_PARALLEL_DOWNLOADS} одновременно)…")
        await asyncio.gather(*(one(client, s) for s in to_download))

    save_manifest(manifest_path, manifest_docs)
    duration_s = (datetime.now(UTC) - start_time).total_seconds()

    return UpdateReport(
        downloaded=sorted(downloaded),
        unchanged=sorted(unchanged),
        failed=failed,
        duration_s=duration_s,
    )


def _build_manifest_entry(
    src: dict[str, Any],
    content: bytes,
    file_sha: str,
    local_file: Path,
) -> dict[str, Any]:
    pages: int | None = None
    try:
        doc = pymupdf.open(stream=content, filetype="pdf")
        pages = doc.page_count
        doc.close()
    except Exception as e:
        logger.warning("Failed to count pages for %s: %s", src.get("source_id"), e)

    return {
        "source_id": src["source_id"],
        "title": src.get("title", src["source_id"]),
        "url": src["url"],
        "doc_type": src.get("doc_type", "frp"),
        "edu_levels": src.get("edu_levels", []),
        "subject_id": src.get("subject_id"),
        "grades": src.get("grades", []),
        "is_normative": src.get("is_normative", True),
        "sha256": file_sha,
        "bytes": len(content),
        "pages": pages,
        "checked_on": date.today().isoformat(),
        "local_path": f"raw/{local_file.name}",
        "note": src.get("note"),
    }
