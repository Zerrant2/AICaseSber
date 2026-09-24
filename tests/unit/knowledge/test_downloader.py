"""Issue #24: обновление базы не должно зависать на одном недоступном источнике."""

import time

import httpx
import yaml

from socrat.config import Settings
from socrat.knowledge import downloader

PDF = b"%PDF-1.4\n%fake\n"


def _settings(tmp_path, sources):
    sf = tmp_path / "sources.yaml"
    sf.write_text(yaml.safe_dump({"sources": sources}, allow_unicode=True), encoding="utf-8")
    return Settings(_env_file=None, knowledge_dir=tmp_path / "kb", sources_file=sf)


def _src(sid, url, **kw):
    return {"source_id": sid, "title": sid, "url": url, "doc_type": "frp", **kw}


async def test_html_page_fails_fast_and_others_continue(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader, "_build_manifest_entry", lambda src, c, sha, lf: {"sha256": sha})
    calls = []

    def handler(req: httpx.Request):
        calls.append(str(req.url))
        if "view" in str(req.url):
            return httpx.Response(200, html="<html>страница просмотра</html>")
        if "down" in str(req.url):
            raise httpx.ConnectTimeout("timeout", request=req)
        return httpx.Response(200, content=PDF)

    s = _settings(
        tmp_path,
        [
            _src("FGOS-REF", "https://pravo.example/view/1", download=False),
            _src("HTML", "https://pravo.example/view/2"),
            _src("DOWN", "https://down.example/a.pdf"),
            _src("OK", "https://edsoo.example/ok.pdf"),
        ],
    )
    messages = []

    async def progress(m):
        messages.append(m)

    t0 = time.monotonic()
    rep = await downloader.download_all(s, progress, transport=httpx.MockTransport(handler))
    assert time.monotonic() - t0 < 10
    assert rep.downloaded == ["OK"]
    assert "FGOS-REF" in rep.unchanged
    assert "не PDF" in rep.failed["HTML"]
    assert "недоступен" in rep.failed["DOWN"]
    assert not any("view/1" in c for c in calls)  # download: false не запрашивается
    assert sum("down.example" in c for c in calls) == downloader.MAX_CONNECT_RETRIES
    assert any("Скачивание" in m and "ошибка" in m for m in messages)
    assert (s.knowledge_dir / "raw" / "OK.pdf").exists()


def test_registry_fgos_sources_are_reference_only():
    s = Settings(_env_file=None)
    reg = {x["source_id"]: x for x in downloader.load_sources_registry(s.sources_file)}
    for sid in ("FGOS-NOO-286", "FGOS-OOO-287", "FGOS-SOO-413"):
        assert reg[sid].get("download") is False
