"""v2 PDF body extraction + page locator tests (mocked; real arXiv PDF
covered by live verification)."""
import sys
import types
from unittest.mock import patch, MagicMock

import pytest


def _fake_pypdf(pages):
    mod = types.ModuleType("pypdf")
    mod.PdfReader = lambda data: pages
    return mod


def _fake_reader(texts):
    class P:
        def __init__(self, t):
            self._t = t
        def extract_text(self):
            return self._t
    class R:
        def __init__(self, texts):
            self.pages = [P(t) for t in texts]
    return R(texts)


def _stream_mock(content: bytes):
    class R:
        def raise_for_status(self):
            pass
        def iter_bytes(self):
            yield content
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
    return R()


def test_pdf_body_page_markers():
    from app.ingest.adapters import paper_adapter
    fake = _fake_pypdf(_fake_reader(["Quantum intro", "Methods section", ""]))
    with patch.object(paper_adapter.httpx, "stream",
                      return_value=_stream_mock(b"%PDF-fake")), \
         patch.dict(sys.modules, {"pypdf": fake}):
        out = paper_adapter._pdf_body("https://arxiv.org/pdf/1.0")
    assert out == "p.1 Quantum intro\n\np.2 Methods section"


def test_pdf_body_size_cap():
    from app.ingest.adapters import paper_adapter
    big = b"x" * (25 * 1024 * 1024 + 4096)
    with patch.object(paper_adapter.httpx, "stream",
                      return_value=_stream_mock(big)):
        assert paper_adapter._pdf_body("https://x/y") == ""


def test_pdf_body_error_is_empty():
    from app.ingest.adapters import paper_adapter
    with patch.object(paper_adapter.httpx, "stream",
                      side_effect=RuntimeError("boom")):
        assert paper_adapter._pdf_body("https://x/y") == ""
    assert paper_adapter._pdf_body("") == ""


def test_build_spans_page_attribution():
    from app.pipeline.stages import build_spans
    spans = build_spans([], [], "",
                        "Title line\n\np.1 first page text\n\np.2 second page text")
    assert spans[0].page is None and "Title" in spans[0].text
    assert spans[1].page == 1 and spans[1].text == "first page text"
    assert spans[2].page == 2
    assert all(s.source == "document" for s in spans)


def test_paper_resolve_includes_pdf_text():
    from app.ingest.adapters.paper_adapter import PaperAdapter
    from app.ingest.adapters.base import IngestRequest
    a = PaperAdapter()
    req = IngestRequest(user_id=1, kind="url",
                        url="https://arxiv.org/abs/1706.03762")
    xml = ("<feed><title>junk</title><entry><title>Att</title>"
           "<summary>softmax attention</summary>"
           "<author><name>X</name></author></entry></feed>")
    resp = MagicMock()
    resp.status_code = 200
    resp.text = xml
    resp.raise_for_status = MagicMock()
    with patch("app.ingest.adapters.paper_adapter.httpx.get", return_value=resp), \
         patch("app.ingest.adapters.paper_adapter._pdf_body",
               return_value="p.1 full paper text here"):
        result = a.resolve(req)
    assert "p.1 full paper text here" in result["meta"]["body_text"]
