from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from muse_ai.auth import MuseAuth
from muse_ai.client import MuseClient
from muse_ai.errors import MuseAuthError
from muse_ai.media import extract_session_ids, extract_video_refs, VideoRef

BASE_DIR = Path(__file__).resolve().parent
STATE_DIR = BASE_DIR / ".muse-state"
OUTPUTS_DIR = BASE_DIR / "outputs"
UPLOADS_DIR = BASE_DIR / "uploads"
STATIC_DIR = BASE_DIR / "static"

OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)
STATE_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Muse AI Studio", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global auth session tracking for OTP login
pending_auth: Optional[MuseAuth] = None
pending_email: Optional[str] = None
active_client: Optional[MuseClient] = None
client_lock = asyncio.Lock()


# Models
class RequestOtpModel(BaseModel):
    email: str
    region: str = "US"


class VerifyOtpModel(BaseModel):
    otp_code: str


class VideoGenerateModel(BaseModel):
    prompt: str
    image_paths: list[str] = []
    timeout: float = 600.0


class ImageGenerateModel(BaseModel):
    prompt: str
    image_paths: list[str] = []
    aspect_ratio: str = "1:1"  # "1:1", "16:9", "9:16"


class ChatModel(BaseModel):
    prompt: str
    session_id: Optional[str] = None
    image_paths: list[str] = []


def is_authenticated() -> bool:
    cookie_file = STATE_DIR / "cookies.json"
    return cookie_file.exists() and cookie_file.stat().st_size > 10


async def get_or_create_client() -> MuseClient:
    global active_client
    async with client_lock:
        if active_client is not None and active_client.conn is not None:
            try:
                await active_client.ping()
                return active_client
            except Exception:
                try:
                    await active_client.close()
                except Exception:
                    pass
                active_client = None

        if not is_authenticated():
            raise HTTPException(
                status_code=401,
                detail="Chưa đăng nhập tài khoản Muse. Vui lòng xác thực OTP trước.",
            )

        auth = MuseAuth(state_dir=STATE_DIR)
        client = MuseClient(auth, state_dir=STATE_DIR)
        await client.connect()
        active_client = client
        return active_client


# Helper: Extract all media (videos and images) from history
def extract_all_media_refs(value: Any) -> list[dict]:
    found: list[dict] = []
    seen: set[str] = set()

    def walk(node: Any):
        if isinstance(node, dict):
            mime = (
                node.get("mime_type")
                or node.get("mimeType")
                or node.get("content_type")
                or node.get("contentType")
                or ""
            )
            kind = node.get("kind") or node.get("type") or ""
            path = node.get("path") or ""
            url = node.get("url") or ""
            media_handle = node.get("media_handle") or node.get("mediaHandle") or ""
            resource_id = node.get("resource_id") or node.get("resourceId") or ""
            variants = node.get("variants") or {}
            original = variants.get("original") if isinstance(variants, dict) else ""

            is_video = (
                kind == "video"
                or mime.startswith("video/")
                or any(str(x).lower().endswith((".mp4", ".webm", ".mov")) for x in (path, url, original) if x)
            )
            is_image = (
                kind in ("image", "photo")
                or mime.startswith("image/")
                or any(str(x).lower().endswith((".png", ".jpg", ".jpeg", ".webp")) for x in (path, url, original) if x)
            )

            if is_video or is_image:
                identity = str(path or resource_id or media_handle or original or url)
                if identity and identity not in seen:
                    seen.add(identity)
                    found.append({
                        "type": "video" if is_video else "image",
                        "path": path or None,
                        "url": original or url or None,
                        "mime_type": mime or ("video/mp4" if is_video else "image/jpeg"),
                        "media_handle": media_handle or None,
                        "resource_id": resource_id or None,
                        "identity": identity,
                    })

            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(value)
    return found


# --- API Routes ---

@app.get("/api/auth/status")
async def auth_status():
    logged_in = is_authenticated()
    email = None
    email_file = STATE_DIR / "email.txt"
    if email_file.exists():
        email = email_file.read_text(encoding="utf-8").strip()

    is_alive = False
    if logged_in:
        try:
            auth = MuseAuth(state_dir=STATE_DIR)
            await auth.load_cookies()
            check = await auth.auth_check()
            is_alive = check.get("ok") is True
            await auth.close()
        except Exception:
            is_alive = False

    return {
        "authenticated": logged_in,
        "is_alive": is_alive,
        "email": email or ("Tài khoản Muse đã kết nối" if logged_in else None),
    }


@app.post("/api/auth/request-otp")
async def request_otp(data: RequestOtpModel):
    global pending_auth, pending_email
    email = data.email.strip()
    if not email:
        raise HTTPException(status_code=400, detail="Vui lòng nhập email hợp lệ.")

    if pending_auth is not None:
        try:
            await pending_auth.close()
        except Exception:
            pass
        pending_auth = None

    auth = MuseAuth(state_dir=STATE_DIR)
    try:
        await auth.restart_login()
        res = await auth.send_otp(email, region=data.region)
        pending_auth = auth
        pending_email = email
        return {
            "status": "success",
            "message": f"Mã OTP đã được gửi về email {email}. Vui lòng kiểm tra hộp thư!",
            "details": res,
        }
    except Exception as exc:
        await auth.close()
        raise HTTPException(
            status_code=500,
            detail=f"Lỗi gửi OTP từ Muse: {exc}. Hãy đảm bảo bạn đã BẬT VPN sang Mỹ trên máy tính!",
        )


@app.post("/api/auth/verify-otp")
async def verify_otp(data: VerifyOtpModel):
    global pending_auth, pending_email, active_client
    if pending_auth is None:
        raise HTTPException(
            status_code=400,
            detail="Phiên gửi OTP chưa được bắt đầu hoặc đã hết hạn. Vui lòng bấm gửi lại OTP.",
        )

    code = data.otp_code.strip()
    if not code:
        raise HTTPException(status_code=400, detail="Vui lòng nhập mã OTP.")

    try:
        result = await pending_auth.confirm_otp(code)
        await pending_auth.save_account()
        check = await pending_auth.auth_check()
        if check.get("ok") is not True:
            raise MuseAuthError(f"Xác thực tài khoản thất bại: {check}")
        await pending_auth.save_cookies()

        if pending_email:
            (STATE_DIR / "email.txt").write_text(pending_email, encoding="utf-8")

        # Invalidate existing client so new connection uses fresh auth
        async with client_lock:
            if active_client is not None:
                try:
                    await active_client.close()
                except Exception:
                    pass
                active_client = None

        return {
            "status": "success",
            "message": "Đăng nhập Muse thành công! Hệ thống đã lưu phiên làm việc.",
            "email": pending_email,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Xác nhận OTP thất bại: {exc}",
        )
    finally:
        await pending_auth.close()
        pending_auth = None


@app.post("/api/auth/logout")
async def logout():
    global active_client
    async with client_lock:
        if active_client is not None:
            try:
                await active_client.close()
            except Exception:
                pass
            active_client = None

    for fname in ["cookies.json", "email.txt"]:
        fpath = STATE_DIR / fname
        if fpath.exists():
            fpath.unlink()

    return {"status": "success", "message": "Đã đăng xuất thành công."}


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    filename = f"{uuid.uuid4().hex[:8]}_{file.filename}"
    file_path = UPLOADS_DIR / filename
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    return {
        "status": "success",
        "filename": filename,
        "local_path": str(file_path),
        "url": f"/uploads/{filename}",
    }


@app.post("/api/video/generate")
async def generate_video(data: VideoGenerateModel):
    if not data.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt tạo video không được để trống.")

    client = await get_or_create_client()
    try:
        images = [Path(p) for p in data.image_paths if Path(p).exists()]
        result = await client.generate_video(
            prompt=data.prompt,
            images=images,
            output_dir=OUTPUTS_DIR,
            timeout=data.timeout,
            min_videos=1,
            download=True,
        )

        downloaded_urls = []
        for p in result.downloaded:
            downloaded_urls.append(f"/outputs/{p.name}")

        return {
            "status": "success",
            "session_id": result.session_id,
            "videos": downloaded_urls,
            "count": len(downloaded_urls),
            "errors": result.download_errors,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Lỗi tạo video từ Muse: {exc}. Kiểm tra kết nối VPN và thử lại.",
        )


@app.post("/api/image/generate")
async def generate_image(data: ImageGenerateModel):
    if not data.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt tạo ảnh không được để trống.")

    client = await get_or_create_client()
    try:
        images = [Path(p) for p in data.image_paths if Path(p).exists()]
        
        # Enrich prompt with aspect ratio instruction
        ratio_note = f" (Aspect ratio: {data.aspect_ratio})" if data.aspect_ratio else ""
        prompt_full = f"Generate an image: {data.prompt}{ratio_note}"

        # Get baseline history
        baseline_hist = await client.history(limit=50)
        baseline_media = {m["identity"] for m in extract_all_media_refs(baseline_hist)}

        # Stream the chat/generation request
        events = await client.chat_stream(
            prompt=prompt_full,
            images=images,
            stream_timeout=35.0,
        )

        # Wait up to 90 seconds for the generated image
        deadline = time.monotonic() + 90.0
        found_images = []
        while time.monotonic() < deadline:
            hist = await client.history(limit=50)
            media_list = extract_all_media_refs(hist)
            new_images = [
                m for m in media_list
                if m["type"] == "image" and m["identity"] not in baseline_media
            ]
            if new_images:
                found_images = new_images
                break
            await asyncio.sleep(2.5)

        downloaded_urls = []
        for index, ref in enumerate(found_images):
            ext = ".png" if "png" in (ref["mime_type"] or "") else ".jpg"
            dest_name = f"muse_img_{int(time.time())}_{index+1}{ext}"
            dest_path = OUTPUTS_DIR / dest_name

            # Download using filesystem helper or browser
            data_bytes = None
            if ref.get("url") and "/idea-cards/media/" in ref["url"]:
                try:
                    data_bytes = await client._fs().idea_media_from_url(ref["url"])
                except Exception:
                    pass
            elif ref.get("media_handle"):
                try:
                    data_bytes = await client._fs().idea_media(ref["media_handle"])
                except Exception:
                    pass
            elif ref.get("url") and ref["url"].startswith(("http://", "https://")):
                try:
                    r = await client.auth.browser.get(ref["url"])
                    if r.status_code < 400:
                        data_bytes = r.content
                except Exception:
                    pass
            elif ref.get("path"):
                try:
                    dest_path = await client._fs().download(ref["path"], dest_path)
                    downloaded_urls.append(f"/outputs/{dest_name}")
                    continue
                except Exception:
                    pass

            if data_bytes:
                dest_path.write_bytes(data_bytes)
                downloaded_urls.append(f"/outputs/{dest_name}")

        return {
            "status": "success",
            "images": downloaded_urls,
            "count": len(downloaded_urls),
            "events_count": len(events),
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Lỗi tạo ảnh từ Muse: {exc}. Kiểm tra kết nối VPN và thử lại.",
        )


@app.post("/api/chat")
async def chat_interaction(data: ChatModel):
    if not data.prompt.strip():
        raise HTTPException(status_code=400, detail="Nội dung chat không được để trống.")

    client = await get_or_create_client()
    try:
        images = [Path(p) for p in data.image_paths if Path(p).exists()]

        # Snapshot existing assistant message_ids before sending
        baseline_ids: set[str] = set()
        try:
            before_hist = await client.history(session_id=data.session_id, limit=20)
            chat_events = (before_hist.get("result") or {}).get("chat_events") or []
            for ev in chat_events:
                if isinstance(ev, dict) and ev.get("message_id"):
                    baseline_ids.add(ev["message_id"])
        except Exception:
            pass

        events = await client.chat_stream(
            prompt=data.prompt,
            images=images,
            session_id=data.session_id,
            stream_timeout=15.0,
        )

        session_id = data.session_id
        extracted_ids = extract_session_ids(events)
        if extracted_ids:
            session_id = extracted_ids[-1]

        # Poll history for new message.assistant response
        reply_text = ""
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            hist = await client.history(session_id=session_id, limit=20)
            chat_events = (hist.get("result") or {}).get("chat_events") or []
            for ev in reversed(chat_events):
                if not isinstance(ev, dict):
                    continue
                msg_id = ev.get("message_id", "")
                if msg_id in baseline_ids:
                    continue
                if ev.get("event_name") == "message.assistant":
                    text = ev.get("display_text") or (ev.get("payload") or {}).get("content") or ""
                    if text.strip() and ev.get("display_text_ready", True):
                        reply_text = text.strip()
                        break
            if reply_text:
                break
            await asyncio.sleep(1.5)

        if not reply_text:
            reply_text = "Muse đã nhận yêu cầu và đang xử lý!"

        return {
            "status": "success",
            "session_id": session_id,
            "reply": reply_text,
            "events": len(events),
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Lỗi chat với Muse: {exc}",
        )


@app.get("/api/gallery")
async def get_gallery():
    items = []
    if OUTPUTS_DIR.exists():
        for file in sorted(OUTPUTS_DIR.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True):
            if file.is_file():
                ext = file.suffix.lower()
                is_video = ext in (".mp4", ".webm", ".mov")
                is_image = ext in (".png", ".jpg", ".jpeg", ".webp", ".gif")
                if is_video or is_image:
                    items.append({
                        "name": file.name,
                        "url": f"/outputs/{file.name}",
                        "type": "video" if is_video else "image",
                        "size_mb": round(file.stat().st_size / (1024 * 1024), 2),
                        "created_at": file.stat().st_mtime,
                    })
    return {"items": items}


# Static mounting
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/outputs", StaticFiles(directory=str(OUTPUTS_DIR)), name="outputs")
app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")


@app.get("/")
async def root():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return HTMLResponse("<h1>Muse AI Studio Backend Ready</h1>")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=7860, reload=True)
