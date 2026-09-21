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


def test_shared_pdf_routes_to_document_adapter():
    from app.ingest.adapters.base import route, IngestRequest
    req = IngestRequest(user_id=1, kind="file", local_path="C:/tmp/paper.pdf")
    adapter, resolved = route(req)
    assert adapter.name == "document_file"
    assert resolved["content_kind"] == "paper"


def test_shared_image_routes_to_document_adapter():
    from app.ingest.adapters.base import route, IngestRequest
    req = IngestRequest(user_id=1, kind="file", local_path="C:/tmp/shot.PNG")
    adapter, resolved = route(req)
    assert resolved["content_kind"] == "image_post"


def test_shared_video_still_video_adapter():
    """Regression: mp4 shares must keep the v0.1 video route."""
    from app.ingest.adapters.base import route, IngestRequest
    req = IngestRequest(user_id=1, kind="file", local_path="C:/tmp/clip.mp4")
    adapter, resolved = route(req)
    assert adapter.name != "document_file"


def test_stage_ingest_shared_pdf_extracts_body(tmp_db, sample_user, tmp_path):
    import sqlite3
    from app.db.schema import get_db
    from app.pipeline import stages

    pdf = tmp_path / "my paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    with get_db() as db:
        rid = db.execute(
            "INSERT INTO reels(user_id, source_kind, content_kind, media_path,"
            " status, current_stage) VALUES(?,'file','paper',?,'processing','ingest')",
            (sample_user, str(pdf))).lastrowid
    with patch("app.ingest.adapters.paper_adapter.pdf_text_from_bytes",
               return_value="p.1 first page content\n\np.2 second"):
        stages.stage_ingest(rid, {})

    db = sqlite3.connect(str(tmp_db))
    doc = db.execute("SELECT body_text FROM documents WHERE reel_id=?",
                     (rid,)).fetchone()
    title = db.execute("SELECT title FROM reels WHERE id=?", (rid,)).fetchone()
    db.close()
    assert doc and "first page content" in doc[0]
    assert title[0] == "my paper"


def test_stage_ingest_shared_image_makes_frame(tmp_db, sample_user, tmp_path):
    import sqlite3
    from app.db.schema import get_db
    from app.pipeline import stages

    img = tmp_path / "shot.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
    with get_db() as db:
        rid = db.execute(
            "INSERT INTO reels(user_id, source_kind, content_kind, media_path,"
            " status, current_stage) VALUES(?,'file','image_post',?,'processing','ingest')",
            (sample_user, str(img))).lastrowid
    stages.stage_ingest(rid, {})

    db = sqlite3.connect(str(tmp_db))
    fr = db.execute("SELECT path, t_s FROM frames WHERE reel_id=?", (rid,)).fetchone()
    thumb = db.execute("SELECT thumb_path FROM reels WHERE id=?", (rid,)).fetchone()
    db.close()
    assert fr and fr[0].endswith("shot.jpg") and fr[1] == 0.0
    assert thumb[0] == fr[0]


def test_stage_ingest_missing_file_fails_loudly(tmp_db, sample_user):
    from app.db.schema import get_db
    from app.pipeline import stages
    from app.db.queue import PermanentJobError

    with get_db() as db:
        rid = db.execute(
            "INSERT INTO reels(user_id, source_kind, content_kind, media_path,"
            " status, current_stage) VALUES(?,'file','paper','C:/nope/gone.pdf',"
            "'processing','ingest')", (sample_user,)).lastrowid
    with pytest.raises(PermanentJobError):
        stages.stage_ingest(rid, {})


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
