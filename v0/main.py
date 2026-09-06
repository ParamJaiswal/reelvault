"""ReelVault v0 — lean API.

Phase 1 scope: /ingest/upload, /ingest/url, /reels/{id}, /healthz.
Processing is inline: upload never breaks, failures become statuses.
"""
from fastapi import FastAPI, File, UploadFile
from pydantic import BaseModel

from v0 import db, fetch, media
from v0.config import HOST, MEDIA_DIR, PORT

app = FastAPI(title="ReelVault v0")


class UrlBody(BaseModel):
    url: str


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.post("/ingest/upload")
async def ingest_upload(file: UploadFile = File(...)) -> dict:
    reel_id = db.create_reel("upload")
    folder = MEDIA_DIR / str(reel_id)
    folder.mkdir(parents=True, exist_ok=True)
    video = folder / "video.mp4"
    video.write_bytes(await file.read())
    db.set_status(reel_id, "queued", video_path=str(video),
                  title=file.filename)
    status = media.extract_media(reel_id)
    return {"id": reel_id, "status": status}


@app.post("/ingest/url")
def ingest_url(body: UrlBody) -> dict:
    reel_id = db.create_reel("url", url=body.url)
    db.set_status(reel_id, "fetching")
    if fetch.fetch_url(reel_id, body.url) == "fetched":
        status = media.extract_media(reel_id)
    else:
        status = "fetch_failed"
    return {"id": reel_id, "status": status}


@app.get("/reels/{reel_id}")
def reel_detail(reel_id: int) -> dict:
    reel = db.get_reel(reel_id)
    if reel is None:
        return {"error": "not found"}
    return reel


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
