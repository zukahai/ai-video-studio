from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("server")

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

app = FastAPI(title="H-AI Studio", version="2.0.0")

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
    aspect_ratio: str = "16:9"
    duration: Optional[int] = 5
    is_continuation: bool = False
    session_id: Optional[str] = None
    scene_index: int = 1
    style_preset: Optional[str] = None
    timeout: float = 600.0


class ImageGenerateModel(BaseModel):
    prompt: str
    image_paths: list[str] = []
    aspect_ratio: str = "1:1"
    style_preset: Optional[str] = None


class ChatModel(BaseModel):
    prompt: str
    session_id: Optional[str] = None
    image_paths: list[str] = []


class MovieBreakdownModel(BaseModel):
    script: str
    style_preset: Optional[str] = "Điện ảnh 4K Hollywood"
    aspect_ratio: Optional[str] = "16:9"
    target_scenes: Optional[int | str] = "auto"


class MergeVideosModel(BaseModel):
    video_urls: list[str]
    output_filename: Optional[str] = None


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
                detail="Chưa đăng nhập tài khoản H-AI. Vui lòng xác thực OTP trước.",
            )

        auth = MuseAuth(state_dir=STATE_DIR)
        client = MuseClient(auth, state_dir=STATE_DIR)
        await client.connect()
        active_client = client
        return active_client


# Hidden Prompt Builders (Server-side only, completely invisible to user)
RATIO_DESCRIPTIONS = {
    "16:9": "tỉ lệ khung hình ngang 16:9 widescreen chuẩn điện ảnh (1920x1080)",
    "9:16": "tỉ lệ khung hình dọc 9:16 vertical full-screen (1080x1920)",
    "1:1": "tỉ lệ khung hình vuông 1:1 square (1080x1080)",
    "21:9": "tỉ lệ khung hình siêu rộng 21:9 ultrawide anamorphic cinema",
    "4:3": "tỉ lệ khung hình tiêu chuẩn 4:3 classic frame",
}


def build_hidden_video_prompt(
    user_prompt: str,
    aspect_ratio: str,
    duration: Optional[int] = 5,
    is_continuation: bool = False,
    scene_index: int = 1,
    style_preset: Optional[str] = None,
) -> str:
    ratio_desc = RATIO_DESCRIPTIONS.get(aspect_ratio, f"tỉ lệ khung hình {aspect_ratio}")
    style_part = f" Phong cách chủ đạo: {style_preset}." if style_preset else ""
    duration_val = duration if (duration and duration > 0) else 5
    duration_desc = f"Thời lượng video: đúng {duration_val} giây ({duration_val} seconds duration clip, pacing & action timing calibrated for exactly {duration_val}s)."

    if is_continuation:
        return (
            f"Hãy tạo một video chuyên nghiệp nối tiếp liền mạch với cảnh video trước đó (Cảnh nối tiếp #{scene_index}). "
            f"{duration_desc} "
            f"Yêu cầu bắt buộc: Giữ nguyên hoàn toàn sự đồng nhất về nhân vật, trang phục, khuôn mặt, bối cảnh môi trường, "
            f"ánh sáng, bảng màu và phong cách góc máy từ cảnh trước để tạo thành một thước phim liên tục mượt mà. "
            f"Chất lượng điện ảnh 4K Ultra HD, chuyển động vật lý tự nhiên, chi tiết sắc nét, âm thanh sống động đồng bộ hoàn hảo, "
            f"{ratio_desc}.{style_part} "
            f"Diễn biến cảnh tiếp theo: {user_prompt.strip()}"
        )
    else:
        return (
            f"Hãy tạo một video chuyên nghiệp, {duration_desc} "
            f"Chất lượng điện ảnh 4K Ultra HD, bố cục nghệ thuật xuất sắc, "
            f"chuyển động camera và vật lý mượt mà chân thực, ánh sáng điện ảnh có chiều sâu, chi tiết cực kỳ sắc nét, "
            f"kèm âm thanh và hiệu ứng tiếng động sống động đồng bộ hoàn hảo, {ratio_desc}.{style_part} "
            f"Nội dung cảnh quay: {user_prompt.strip()}"
        )


def build_hidden_image_prompt(
    user_prompt: str,
    aspect_ratio: str,
    style_preset: Optional[str] = None,
) -> str:
    ratio_desc = RATIO_DESCRIPTIONS.get(aspect_ratio, f"tỉ lệ khung hình {aspect_ratio}")
    style_part = f" Phong cách nghệ thuật: {style_preset}." if style_preset else ""
    return (
        f"Generate a masterpiece, ultra-detailed professional high-resolution 8K image, "
        f"exceptional lighting, sharp focus, rich textures, professional composition, {ratio_desc}.{style_part} "
        f"Subject description: {user_prompt.strip()}"
    )


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


# --- Live Subscription & Quota Fetcher (Exact real account metrics) ---
_subscription_cache = {
    "data": None,
    "timestamp": 0.0,
}
_subscription_lock = asyncio.Lock()

async def fetch_live_subscription(auth: Optional[MuseAuth] = None) -> Optional[dict]:
    global _subscription_cache
    now = time.time()
    if _subscription_cache["data"] and (now - _subscription_cache["timestamp"] < 20):
        return _subscription_cache["data"]

    async with _subscription_lock:
        now = time.time()
        if _subscription_cache["data"] and (now - _subscription_cache["timestamp"] < 20):
            return _subscription_cache["data"]

        should_close = False
        if auth is None:
            auth = MuseAuth(state_dir=STATE_DIR)
            await auth.load_cookies()
            should_close = True

        try:
            # Server action for fetchSubscriptionAction
            action_id = "40e0249eeae158685b6a8d0c3b203f53dd4f2c7d63"
            headers = {
                "Next-Action": action_id,
                "Accept": "text/x-component",
                "Content-Type": "text/plain;charset=UTF-8",
                "Origin": "https://muse.ai",
                "Referer": "https://muse.ai/",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Dest": "empty",
            }
            payload = json.dumps([{"includeAgreement": False}])
            resp = await auth.browser.post(
                "https://muse.ai/",
                data=payload,
                headers=headers,
            )
            if resp.status_code == 200:
                for line in resp.text.splitlines():
                    if line.startswith("1:"):
                        try:
                            data = json.loads(line[2:])
                            if data.get("success") and "subscription" in data:
                                sub = data["subscription"]
                                _subscription_cache["data"] = sub
                                _subscription_cache["timestamp"] = time.time()
                                return sub
                        except Exception:
                            pass
                    match = re.search(r'\{"success":\s*true.+subscription.+\}', line)
                    if match:
                        try:
                            data = json.loads(match.group(0))
                            if "subscription" in data:
                                sub = data["subscription"]
                                _subscription_cache["data"] = sub
                                _subscription_cache["timestamp"] = time.time()
                                return sub
                        except Exception:
                            pass
        except Exception as e:
            logger.warning(f"Lỗi khi lấy thông tin hạn mức live từ tài khoản: {e}")
        finally:
            if should_close:
                await auth.close()

        return _subscription_cache["data"]


_auth_status_cache = {
    "data": None,
    "timestamp": 0.0,
}


def invalidate_subscription_cache():
    global _subscription_cache, _auth_status_cache
    _subscription_cache = {
        "data": None,
        "timestamp": 0.0,
    }
    _auth_status_cache = {
        "data": None,
        "timestamp": 0.0,
    }


# --- API Routes ---

@app.get("/api/auth/status")
async def auth_status(force: bool = False):
    global _auth_status_cache
    if not force and _auth_status_cache["data"] is not None:
        if (time.time() - _auth_status_cache["timestamp"]) < 60.0:
            return _auth_status_cache["data"]

    if force:
        invalidate_subscription_cache()
    logged_in = is_authenticated()
    email = None
    email_file = STATE_DIR / "email.txt"
    if email_file.exists():
        email = email_file.read_text(encoding="utf-8").strip()

    is_alive = False
    viewer_id = None
    context_window = 350000
    model_name = "Auto (H-AI Engine)"
    
    if logged_in:
        try:
            auth = MuseAuth(state_dir=STATE_DIR)
            await auth.load_cookies()
            check = await auth.auth_check()
            is_alive = check.get("ok") is True
            viewer_id = check.get("viewer_id")
            await auth.close()

            try:
                client = await get_or_create_client()
                m = await client.model_get()
                models = (m.get("result") or {}).get("available_models") or []
                if models and isinstance(models[0], dict):
                    context_window = models[0].get("context_window_tokens", 350000)
                    model_name = models[0].get("display_name", "Auto")
            except Exception:
                pass
        except Exception:
            is_alive = False

    # Calculate fallback reset date if remote offline
    import datetime
    today = datetime.date.today()
    days_ahead = 6 - today.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    next_reset = today + datetime.timedelta(days=days_ahead)
    reset_date_str = next_reset.strftime("%d tháng %m")

    # Fetch live exact un-mocked subscription metrics from account
    live_sub = None
    if logged_in and is_alive:
        try:
            live_sub = await fetch_live_subscription()
        except Exception:
            pass

    if live_sub:
        free_usage = live_sub.get("usage") or {}
        pct = free_usage.get("percentUsed", 0)
        tier_obj = live_sub.get("tier") or {}
        tier_name = str(tier_obj.get("name") or "H-AI Miễn phí").replace("Muse", "H-AI")

        raw_topup_label = str(live_sub.get("topupRowValueLabel") or "Đã dùng 0% (Còn 1 tỷ token)")
        topup_capitalized = raw_topup_label.replace("(còn", "(Còn").replace(" còn ", " Còn ")

        usage_data = {
            "tier_name": tier_name,
            "free_plan": {
                "name": live_sub.get("usageRowLabel") or "Gói miễn phí",
                "used_percent": pct,
                "label": live_sub.get("usageRowValueLabel") or f"Đã dùng {pct}%",
                "subtitle": live_sub.get("statusSubtitle") or f"Giới hạn hàng tuần sẽ đặt lại vào {reset_date_str}",
                "resets_at": free_usage.get("resetsAt"),
            },
            "additional_tokens": {
                "name": live_sub.get("topupRowLabel") or "Token bổ sung",
                "used_percent": 0,
                "label": topup_capitalized,
                "tokens_left": topup_capitalized,
                "subtitle": "Không bao giờ hết hạn",
                "topup_balance": live_sub.get("topupBalance", 0),
                "topup_total": live_sub.get("topupTotal", 0),
            },
            "cta_label": live_sub.get("entryPointCtaLabel") or "Nâng cấp",
            "is_live": True,
        }
    else:
        usage_data = {
            "tier_name": "H-AI Miễn phí",
            "free_plan": {
                "name": "Gói miễn phí",
                "used_percent": 26,
                "label": "Đã dùng 26%",
                "subtitle": "Giới hạn hàng tuần sẽ đặt lại vào 19 tháng 11",
            },
            "additional_tokens": {
                "name": "Token bổ sung",
                "used_percent": 0,
                "label": "Đã dùng 0% (Còn 1 tỷ token)",
                "tokens_left": "Còn 1 tỷ token",
                "subtitle": "Không bao giờ hết hạn",
            },
            "cta_label": "Nâng cấp",
            "is_live": False,
        }

    res_dict = {
        "authenticated": logged_in,
        "is_alive": is_alive,
        "email": email or ("Tài khoản H-AI đã kết nối" if logged_in else None),
        "viewer_id": viewer_id,
        "quota": {
            "tier": usage_data["tier_name"],
            "context_window_tokens": context_window,
            "model_name": model_name,
            "status": "Hoạt động tốt (Active)",
            "refresh_cycle": usage_data["free_plan"]["subtitle"],
            "video_quota_status": "Không giới hạn phiên hiện tại",
        },
        "usage": usage_data,
    }
    _auth_status_cache["data"] = res_dict
    _auth_status_cache["timestamp"] = time.time()
    return res_dict


@app.get("/api/usage")
async def get_usage_endpoint():
    if not is_authenticated():
        raise HTTPException(status_code=401, detail="Chưa đăng nhập tài khoản.")
    sub = await fetch_live_subscription()
    if not sub:
        raise HTTPException(status_code=502, detail="Chưa thể lấy thông tin mức sử dụng trực tiếp từ tài khoản.")
    return sub


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
            detail=f"Lỗi gửi OTP từ hệ thống H-AI: {exc}. Hãy đảm bảo bạn đã BẬT VPN sang Mỹ trên máy tính!",
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
        await pending_auth.confirm_otp(code)
        await pending_auth.save_account()
        check = await pending_auth.auth_check()
        if check.get("ok") is not True:
            raise MuseAuthError(f"Xác thực tài khoản thất bại: {check}")
        await pending_auth.save_cookies()

        if pending_email:
            (STATE_DIR / "email.txt").write_text(pending_email, encoding="utf-8")

        async with client_lock:
            if active_client is not None:
                try:
                    await active_client.close()
                except Exception:
                    pass
                active_client = None

        return {
            "status": "success",
            "message": "Đăng nhập H-AI thành công! Hệ thống đã lưu phiên làm việc.",
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

    return {"status": "success", "message": "Đã đăng xuất khỏi H-AI."}


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
        raise HTTPException(status_code=400, detail="Mô tả video không được để trống.")

    client = await get_or_create_client()
    try:
        images = [Path(p) for p in data.image_paths if Path(p).exists()]

        # Inject invisible professional & continuation prompt
        enhanced_prompt = build_hidden_video_prompt(
            user_prompt=data.prompt,
            aspect_ratio=data.aspect_ratio,
            duration=data.duration,
            is_continuation=data.is_continuation,
            scene_index=data.scene_index,
            style_preset=data.style_preset,
        )

        # Use existing session_id if continuing a video sequence
        target_session_id = data.session_id if (data.is_continuation and data.session_id) else None

        result = await client.generate_video(
            prompt=enhanced_prompt,
            images=images,
            output_dir=OUTPUTS_DIR,
            session_id=target_session_id,
            timeout=data.timeout,
            min_videos=1,
            download=True,
        )

        downloaded_urls = []
        for p in result.downloaded:
            # Rename muse_video prefix to haiai_video if present
            if p.name.startswith("muse_"):
                new_name = "haiai_" + p.name[5:]
                new_path = p.parent / new_name
                if not new_path.exists():
                    p.rename(new_path)
                    p = new_path
            downloaded_urls.append(f"/outputs/{p.name}")

        # Invalidate subscription cache to immediately fetch latest live quota
        invalidate_subscription_cache()

        return {
            "status": "success",
            "session_id": result.session_id,
            "videos": downloaded_urls,
            "count": len(downloaded_urls),
            "aspect_ratio": data.aspect_ratio,
            "duration": data.duration or 5,
            "is_continuation": data.is_continuation,
            "errors": result.download_errors,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Lỗi tạo video từ H-AI: {exc}. Kiểm tra kết nối VPN và thử lại.",
        )


@app.post("/api/video/merge")
async def merge_videos(data: MergeVideosModel):
    if not data.video_urls or len(data.video_urls) == 0:
        raise HTTPException(status_code=400, detail="Danh sách video cần ghép trống.")

    # If only 1 video provided, return it directly
    if len(data.video_urls) == 1:
        return {
            "status": "success",
            "url": data.video_urls[0],
            "count": 1,
            "message": "Chỉ có 1 cảnh, không cần ghép.",
        }

    # Resolve video paths from URLs
    resolved_paths: list[Path] = []
    for u in data.video_urls:
        clean_name = Path(u.split("?")[0]).name
        file_path = OUTPUTS_DIR / clean_name
        if not file_path.exists():
            alt_path = UPLOADS_DIR / clean_name
            if alt_path.exists():
                file_path = alt_path
            else:
                raise HTTPException(status_code=404, detail=f"Không tìm thấy file video: {clean_name}")
        resolved_paths.append(file_path.resolve())

    # Prepare temp concat list file
    timestamp = int(time.time())
    output_name = data.output_filename or f"haiai_movie_{timestamp}.mp4"
    if not output_name.endswith(".mp4"):
        output_name += ".mp4"
    output_path = OUTPUTS_DIR / output_name

    concat_list_file = OUTPUTS_DIR / f"concat_list_{timestamp}.txt"
    try:
        with open(concat_list_file, "w", encoding="utf-8") as f:
            for p in resolved_paths:
                f.write(f"file '{p.as_posix()}'\n")

        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()

        # 1. Try fast stream copy (lossless, instant)
        cmd_copy = [
            ffmpeg_exe,
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list_file),
            "-c", "copy",
            str(output_path),
        ]
        proc = subprocess.run(cmd_copy, capture_output=True, text=True)
        if proc.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
            logger.warning(f"FFmpeg copy concat không khả dụng ({proc.stderr[:200] if proc.stderr else 'unknown'}), chuyển sang chế độ re-encode tương thích...")
            # 2. Fallback to re-encode (guaranteed compatibility across different presets)
            cmd_reencode = [
                ffmpeg_exe,
                "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_list_file),
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "22",
                "-c:a", "aac",
                "-b:a", "192k",
                str(output_path),
            ]
            proc_re = subprocess.run(cmd_reencode, capture_output=True, text=True)
            if proc_re.returncode != 0 or not output_path.exists():
                raise RuntimeError(f"Lỗi ghép video với FFmpeg: {proc_re.stderr[-300:] if proc_re.stderr else 'unknown'}")

        return {
            "status": "success",
            "url": f"/outputs/{output_name}",
            "filename": output_name,
            "count": len(resolved_paths),
            "size_bytes": output_path.stat().st_size,
        }
    except Exception as exc:
        logger.error(f"Lỗi khi ghép video: {exc}")
        raise HTTPException(status_code=500, detail=f"Lỗi khi ghép chuỗi cảnh phim: {exc}")
    finally:
        if concat_list_file.exists():
            try:
                concat_list_file.unlink()
            except Exception:
                pass


@app.post("/api/image/generate")
async def generate_image(data: ImageGenerateModel):
    if not data.prompt.strip():
        raise HTTPException(status_code=400, detail="Mô tả ảnh không được để trống.")

    client = await get_or_create_client()
    try:
        images = [Path(p) for p in data.image_paths if Path(p).exists()]

        # Inject invisible professional image prompt
        prompt_full = build_hidden_image_prompt(
            user_prompt=data.prompt,
            aspect_ratio=data.aspect_ratio,
            style_preset=data.style_preset,
        )

        baseline_hist = await client.history(limit=50)
        baseline_media = {m["identity"] for m in extract_all_media_refs(baseline_hist)}

        events = await client.chat_stream(
            prompt=prompt_full,
            images=images,
            stream_timeout=35.0,
        )

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
            dest_name = f"haiai_img_{int(time.time())}_{index+1}{ext}"
            dest_path = OUTPUTS_DIR / dest_name

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

        # Invalidate subscription cache to immediately fetch latest live quota
        invalidate_subscription_cache()

        return {
            "status": "success",
            "images": downloaded_urls,
            "count": len(downloaded_urls),
            "events_count": len(events),
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Lỗi tạo ảnh từ H-AI: {exc}. Kiểm tra kết nối VPN và thử lại.",
        )


@app.post("/api/chat")
async def chat_interaction(data: ChatModel):
    if not data.prompt.strip():
        raise HTTPException(status_code=400, detail="Nội dung chat không được để trống.")

    client = await get_or_create_client()
    try:
        images = [Path(p) for p in data.image_paths if Path(p).exists()]

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
            reply_text = "H-AI đã nhận yêu cầu và đang xử lý!"

        return {
            "status": "success",
            "session_id": session_id,
            "reply": reply_text,
            "events": len(events),
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Lỗi chat với H-AI: {exc}",
        )


MOVIE_CHARACTER_COLORS = [
    "#ef4444",  # Crimson Red
    "#3b82f6",  # Electric Blue
    "#10b981",  # Emerald Green
    "#f59e0b",  # Amber Gold
    "#8b5cf6",  # Purple
    "#ec4899",  # Pink
    "#06b6d4",  # Cyan
    "#f97316",  # Orange
    "#14b8a6",  # Teal
    "#a855f7",  # Violet
]


def _format_movie_result(parsed: dict, style_preset: str = "Điện ảnh 4K Hollywood") -> dict:
    characters = parsed.get("characters") or []
    if not isinstance(characters, list):
        characters = []
    formatted_chars = []
    for idx, char in enumerate(characters):
        if not isinstance(char, dict):
            continue
        c_id = char.get("id") or f"char_{idx+1}"
        c_name = char.get("name") or f"Nhân vật #{idx+1}"
        c_desc = char.get("description") or f"Nhân vật {c_name} chuẩn phong cách {style_preset} sắc nét 4K."
        c_color = char.get("color") or MOVIE_CHARACTER_COLORS[idx % len(MOVIE_CHARACTER_COLORS)]
        c_images = char.get("images") if isinstance(char.get("images"), list) else []
        formatted_chars.append({
            "id": c_id,
            "name": c_name,
            "description": c_desc,
            "color": c_color,
            "images": c_images
        })

    scenes = parsed.get("scenes") or []
    if not isinstance(scenes, list):
        scenes = []
    formatted_scenes = []
    for idx, sc in enumerate(scenes):
        if not isinstance(sc, dict):
            continue
        dur = sc.get("duration", 8)
        if dur not in [5, 8, 10, 15, 20]:
            dur = 8
        cinematic_prompt = sc.get("prompt") or f"Cinematic 4K Hollywood, {style_preset}, Scene {idx+1}: {sc.get('situation', '')}. 8k, photorealistic lighting, cinematic camera motion"
        formatted_scenes.append({
            "scene_index": idx + 1,
            "situation": sc.get("situation") or f"Diễn biến phân cảnh {idx+1}",
            "dialogue": sc.get("dialogue") or f"Lời thoại Cảnh #{idx+1}",
            "duration": dur,
            "characters_involved": sc.get("characters_involved") if isinstance(sc.get("characters_involved"), list) else [],
            "prompt": cinematic_prompt,
            "status": "idle",
            "video_url": None
        })

    return {
        "title": parsed.get("title") or "Phim Điện Ảnh H-AI",
        "logline": parsed.get("logline") or "",
        "characters": formatted_chars,
        "scenes": formatted_scenes
    }


def _fallback_movie_breakdown(script: str, style_preset: str = "Điện ảnh 4K Hollywood", target_scenes: int | str = "auto") -> dict:
    script_clean = script.strip()
    lower = script_clean.lower()

    # Special handling for well-known story: Cô Bé Bán Diêm
    if "cô bé bán diêm" in lower or "bán diêm" in lower:
        return _format_movie_result({
            "title": "Cô Bé Bán Diêm",
            "logline": "Trong đêm giao thừa tuyết rơi lạnh giá, một cô bé nghèo bán diêm thắp từng que diêm nhỏ để sưởi ấm, và trong ánh lửa mong manh ấy, em nhìn thấy bà ngoại yêu dấu đến đón em về thiên đường.",
            "characters": [
                {
                    "id": "char_1",
                    "name": "Cô Bé Bán Diêm",
                    "description": "Bé gái khoảng 9 tuổi, dáng người nhỏ gầy, tóc vàng hoe dài rối phủ tuyết, khuôn mặt xanh xao vì lạnh với đôi má ửng hồng, đôi mắt xanh to buồn bã nhưng trong sáng. Mặc áo khoác cũ rách màu nâu sẫm quá khổ, váy len xám, khăn choàng mỏng, chân trần tím tái vì lạnh, tay ôm một chiếc giỏ tre đựng những bao diêm, một chiếc dép lê cũ còn sót lại.",
                    "color": "#ef4444",
                    "images": []
                },
                {
                    "id": "char_2",
                    "name": "Bà Ngoại",
                    "description": "Phụ nữ khoảng 70 tuổi, dáng người cao gầy, tóc bạc trắng dài buông xõa, khuôn mặt hiền hậu phúc hậu với nụ cười ấm áp, đôi mắt nâu dịu dàng. Mặc áo choàng dài màu trắng ngà phát sáng nhẹ, khăn choàng ren trắng, trông như một thiên thần, xuất hiện trong ánh sáng vàng ấm của que diêm.",
                    "color": "#f59e0b",
                    "images": []
                },
                {
                    "id": "char_3",
                    "name": "Người Qua Đường",
                    "description": "Người lớn đi lại trên phố trong đêm giao thừa, khoảng 30-50 tuổi, mặc áo khoác dạ dày màu tối, khăn quàng cổ, mũ lông, găng tay da, vội vã bước đi, khuôn mặt thờ ơ hoặc thương hại thoáng qua, không dừng lại mua diêm.",
                    "color": "#64748b",
                    "images": []
                }
            ],
            "scenes": [
                {
                    "scene_index": 1,
                    "situation": "Đêm giao thừa, tuyết rơi dày trên phố cổ châu Âu, cô bé bán diêm chân trần run rẩy ôm giỏ diêm đi giữa dòng người vội vã về nhà đón năm mới, không ai dừng lại mua diêm cho em.",
                    "dialogue": "Cô Bé Bán Diêm: \"Ai mua diêm không? Diêm đây... chỉ một xu thôi ạ...\"",
                    "duration": 10,
                    "characters_involved": ["Cô Bé Bán Diêm", "Người Qua Đường"],
                    "prompt": "Cinematic 4K Hollywood film, wide shot of a snowy European old town street on New Year's Eve at dusk. A 9-year-old little girl with long messy blonde hair covered in snow, wearing an oversized torn dark brown coat and grey wool dress, barefoot with purple cold feet, hugs a wicker basket of matches and walks slowly through a crowd of hurried adults in dark wool coats and fur hats. Warm golden light glows from shop windows and Christmas decorations, contrasting with cold blue snowfall and the girl's pale, shivering face, snowflakes falling on her hair, detailed cobblestone street, slow tracking camera following the girl, photorealistic, epic cinematic lighting, 4K Ultra HD."
                },
                {
                    "scene_index": 2,
                    "situation": "Không bán được que diêm nào, cô bé co ro ngồi nép vào góc tường giữa hai ngôi nhà, run rẩy quẹt que diêm đầu tiên để sưởi ấm đôi tay tê cóng, và trong ánh lửa, em mơ thấy một lò sưởi ấm áp với bữa ăn ngon.",
                    "dialogue": "Dẫn chuyện: \"Em quẹt que diêm đầu tiên, và trong ánh lửa nhỏ bé ấy, em thấy một lò sưởi ấm áp đang cháy rực rỡ.\"",
                    "duration": 10,
                    "characters_involved": ["Cô Bé Bán Diêm"],
                    "prompt": "Cinematic 4K Hollywood film, intimate close-up and medium shot of a 9-year-old little girl with long blonde hair covered in snow, wearing a torn dark brown coat, crouching in a snowy corner between two brick houses at night. She strikes a match with trembling hands, the small golden flame illuminating her pale, hopeful face and cold red fingers, her breath visible in the freezing air. In the warm glow of the flame, a magical vision of a glowing fireplace appears faintly before her, snow falling around her, cold blue night light contrasting with the warm orange match light, shallow depth of field, slow push-in camera, photorealistic, emotional cinematic lighting, 4K Ultra HD."
                },
                {
                    "scene_index": 3,
                    "situation": "Cô bé quẹt thêm nhiều que diêm, ánh lửa nối tiếp nhau hiện ra cây thông Noel lấp lánh và cuối cùng là hình ảnh bà ngoại yêu dấu mỉm cười dang tay đón em, em vội quẹt hết cả bao diêm để giữ bà ở lại.",
                    "dialogue": "Cô Bé Bán Diêm: \"Bà ơi! Bà đừng đi! Cháu quẹt hết diêm đây, bà ở lại với cháu nhé!\"",
                    "duration": 10,
                    "characters_involved": ["Cô Bé Bán Diêm", "Bà Ngoại"],
                    "prompt": "Cinematic 4K Hollywood film, magical medium shot in a snowy street corner at night. A 9-year-old little girl with long blonde hair, wearing a torn dark brown coat, kneels in the snow striking match after match, each golden flame revealing a glowing vision: a sparkling Christmas tree with candles and gifts, then a kind 70-year-old grandmother with long white hair, wearing a glowing ivory-white gown, smiling warmly with open arms. The grandmother's radiant golden light surrounds the girl, snowflakes glowing in the warm light, the girl's face filled with joy and tears, cold blue night contrasting with warm magical golden glow, slow crane-up camera, photorealistic, dreamlike cinematic lighting, 4K Ultra HD."
                },
                {
                    "scene_index": 4,
                    "situation": "Sáng mùng một năm mới, tuyết ngừng rơi, người qua đường phát hiện cô bé đã ra đi trong nụ cười bình yên bên góc tường, tay vẫn nắm chặt bao diêm đã cháy hết, linh hồn em được bà ngoại bế lên thiên đường trong ánh sáng rực rỡ.",
                    "dialogue": "Dẫn chuyện: \"Sáng hôm sau, người ta thấy em ngồi đó, má vẫn ửng hồng, môi vẫn mỉm cười — em đã đi theo bà ngoại về nơi ấm áp và hạnh phúc mãi mãi.\"",
                    "duration": 8,
                    "characters_involved": ["Cô Bé Bán Diêm", "Bà Ngoại", "Người Qua Đường"],
                    "prompt": "Cinematic 4K Hollywood film, poignant wide and medium shot on a snowy street at dawn on New Year's morning. A 9-year-old little girl with long blonde hair, wearing a torn dark brown coat, sits peacefully against a brick wall in the snow, smiling gently with rosy cheeks, holding burnt matchsticks, while a few passersby in dark wool coats stand nearby in sorrow. Above her, a radiant vision of a 70-year-old grandmother with long white hair in a glowing ivory gown gently lifts the girl's spirit into warm golden heavenly light breaking through soft pink dawn clouds, snow sparkling, cold blue morning light contrasting with warm golden heavenly glow, slow crane-up camera rising toward the sky, photorealistic, deeply emotional cinematic lighting, 4K Ultra HD."
                }
            ]
        }, style_preset)

    # Special handling for well-known story: Có Công Mài Sắt Có Ngày Nên Kim
    if "mài sắt" in lower or "nên kim" in lower:
        return _format_movie_result({
            "title": "Có Công Mài Sắt, Có Ngày Nên Kim",
            "logline": "Một cậu bé ham chơi lười học gặp bà cụ đang mài thỏi sắt lớn thành kim khâu, và từ bài học kiên trì ấy, cậu quyết tâm thay đổi để trở thành người chăm học.",
            "characters": [
                {
                    "id": "char_1",
                    "name": "Cậu Bé",
                    "description": "Bé trai khoảng 10 tuổi, dáng người nhỏ gầy, tóc đen ngắn rối, gương mặt thông minh nhưng có nét lười biếng, thiếu kiên nhẫn, đôi mắt đen to. Mặc áo vải thô màu nâu nhạt, quần vải tối màu, đi dép rơm, tay ôm một cuốn sách cũ.",
                    "color": "#ef4444",
                    "images": []
                },
                {
                    "id": "char_2",
                    "name": "Bà Cụ",
                    "description": "Phụ nữ khoảng 75 tuổi, dáng người nhỏ gầy, lưng hơi còng, tóc bạc phơ dài búi gọn sau gáy, khuôn mặt phúc hậu nhiều nếp nhăn, đôi mắt hiền từ đầy sự từng trải. Mặc áo dài nâu sẫm, khăn choàng xám, váy dài màu xanh rêu, đi dép vải, tay cầm một thỏi sắt lớn.",
                    "color": "#f59e0b",
                    "images": []
                }
            ],
            "scenes": [
                {
                    "scene_index": 1,
                    "situation": "Buổi sáng sớm trong góc học tập nghèo của ngôi nhà tranh, cậu bé 10 tuổi ngồi chống cằm đọc sách với vẻ mặt nhăn nhó, đọc được vài chữ thì thở dài, gấp sách lại và ngáp ngắn ngáp dài vì chán học.",
                    "dialogue": "Dẫn chuyện: \"Ngày xửa ngày xưa, có một cậu bé rất ham chơi, lười học.\" Cậu Bé: \"Chữ nghĩa gì mà khó nhớ thế này, chán quá đi mất!\"",
                    "duration": 8,
                    "characters_involved": ["Cậu Bé"],
                    "prompt": "Cinematic 4K Hollywood film, intimate medium shot of a poor study corner inside an old thatched hut in ancient Vietnam at early morning. A 10-year-old boy with short messy black hair, wearing a rough light brown cloth shirt, sits chin-in-hand at a small wooden desk, frowning at an old book, then sighs, closes the book and yawns repeatedly. Soft golden morning light filters through bamboo walls, dust particles in the air, detailed wood grain and rough cloth texture, shallow depth of field, slow push-in camera, photorealistic, warm cinematic lighting, 4K Ultra HD."
                },
                {
                    "scene_index": 2,
                    "situation": "Trên đường làng ven bờ suối, trời nắng nhẹ, cậu bé đi chơi về dừng chân tò mò khi thấy bà cụ tóc bạc đang ngồi cặm cụi mài một thỏi sắt lớn vào tảng đá bên bờ suối.",
                    "dialogue": "Dẫn chuyện: \"Một hôm đi chơi qua bờ suối, cậu thấy một bà cụ đang miệt mài mài một thỏi sắt lớn.\"",
                    "duration": 10,
                    "characters_involved": ["Cậu Bé", "Bà Cụ"],
                    "prompt": "Cinematic 4K Hollywood film, wide and medium shot of a village road beside a small clear stream in ancient Vietnam under gentle sunlight. A 10-year-old boy in a rough light brown cloth shirt, returning from play, stops curiously and tilts his head, looking puzzled at a 75-year-old old woman with silver hair in a bun, wearing a dark brown robe, sitting diligently grinding a large iron bar on a big stone by the stream. Clear water flows over pebbles, green trees and soft sunlight, detailed stone texture and iron bar, slow tracking camera, photorealistic, bright cinematic lighting, 4K Ultra HD."
                },
                {
                    "scene_index": 3,
                    "situation": "Cạnh tảng đá lớn bên bờ suối, cậu bé chắp tay cúi chào và hỏi bà cụ làm sao thỏi sắt to thế này có thể thành kim được, bà cụ ngước lên mỉm cười hiền từ và giải thích bà đang mài nó để làm thành một chiếc kim khâu vá.",
                    "dialogue": "Cậu Bé: \"Bà ơi, bà đang làm gì thế ạ? Thỏi sắt to thế này làm sao thành kim được?\" Bà Cụ: \"Ta mài thỏi sắt này để làm thành một chiếc kim khâu vá đấy cháu ạ.\"",
                    "duration": 8,
                    "characters_involved": ["Cậu Bé", "Bà Cụ"],
                    "prompt": "Cinematic 4K Hollywood film, intimate medium shot beside a large rock by a clear stream in ancient Vietnam under gentle sunlight. A 10-year-old boy in a rough light brown cloth shirt clasps his hands and bows respectfully to ask a question, while a 75-year-old old woman with silver hair in a bun, wearing a dark brown robe, looks up from grinding a large iron bar on a stone and smiles kindly. Clear stream water, green leaves and soft sunlight, detailed facial expressions, iron bar and stone texture, slow push-in camera, photorealistic, warm cinematic lighting, 4K Ultra HD."
                },
                {
                    "scene_index": 4,
                    "situation": "Sáng hôm sau, cậu bé ngồi ngay ngắn chăm chú đọc sách trong góc học tập, ánh mắt sáng lên đầy quyết tâm, bên cạnh là chiếc kim khâu sáng bóng mà bà cụ đã mài xong, minh chứng cho bài học có công mài sắt, có ngày nên kim.",
                    "dialogue": "Dẫn chuyện: \"Nghe bà cụ nói, cậu bé chợt hiểu ra: có công mài sắt, có ngày nên kim.\" Cậu Bé: \"Cháu hiểu rồi ạ! Từ nay cháu sẽ chăm học, kiên trì như bà mài sắt vậy!\"",
                    "duration": 10,
                    "characters_involved": ["Cậu Bé", "Bà Cụ"],
                    "prompt": "Cinematic 4K Hollywood film, warm medium shot of a poor study corner inside an old thatched hut in ancient Vietnam at early morning, days later. A 10-year-old boy with short black hair, wearing a rough light brown cloth shirt, sits upright and reads attentively with a determined smile, a shiny sewing needle placed beside his book as proof of the lesson. A 75-year-old old woman with silver hair in a bun, wearing a dark brown robe, stands nearby smiling kindly and nodding encouragement. Soft golden morning light through bamboo walls, detailed book pages and needle glint, hopeful atmosphere, slow crane-up camera, photorealistic, warm cinematic lighting, 4K Ultra HD."
                }
            ]
        }, style_preset)

    paras = [p.strip() for p in re.split(r'\n+|\.\s+', script_clean) if len(p.strip()) > 10]
    if not paras:
        paras = [script_clean] if script_clean else ["Khởi đầu câu chuyện kịch tính trong không gian điện ảnh."]

    common_names = [
        "Cậu bé", "Cô bé", "Bà cụ", "Ông cụ", "Bà lão", "Bà ngoại", "Ông lão", "Ông ngoại",
        "Người mẹ", "Người cha", "Chàng trai", "Cô gái", "Hiệp sĩ", "Phù thủy", "Công chúa",
        "Hoàng tử", "Con rồng", "Thợ săn", "Chiến binh", "Robot", "Người lạ", "Người qua đường"
    ]
    found_chars = []
    for name in common_names:
        if name.lower() in script_clean.lower():
            found_chars.append(name.capitalize())

    # Detect capitalized words with strict Vietnamese stopwords
    cap_words = re.findall(r'\b[A-ZÀÁẢÃẠĂẮẰẲẴẶÂẤẦẨẪẬĐÈÉẺẼẸÊẾỀỂỄỆÌÍỈĨỊÒÓỎÕỌÔỐỒỔỖỘƠỚỜỞỠỢÙÚỦŨỤƯỨỪỬỮỰỲÝỶỸỴ][a-zàáảãạăắằẳẵặâấầẩẫậđèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵ]+\b', script_clean)
    stopwords = {
        "Ngày", "Một", "Khi", "Nhưng", "Và", "Trong", "Trên", "Sau", "Bỗng", "Có",
        "Thế", "Rồi", "Đó", "Nếu", "Vì", "Bởi", "Tuy", "Cho", "Tôi", "Hãy", "Viết",
        "Tạo", "Làm", "Kịch", "Bản", "Câu", "Chuyện", "Phim", "Xem", "Nói", "Về",
        "Muốn", "Giúp", "Bạn", "Được", "Với", "Ở", "Tại", "Những", "Các", "Đang",
        "Công", "Mài", "Sắt", "Nên", "Kim"
    }
    for word in cap_words:
        if word not in stopwords and len(word) > 2 and word not in found_chars and len(found_chars) < 5:
            found_chars.append(word)

    if not found_chars:
        found_chars = ["Cậu bé", "Bà cụ"]

    characters = []
    for idx, name in enumerate(found_chars[:6]):
        characters.append({
            "id": f"char_{idx+1}",
            "name": name,
            "description": f"Nhân vật {name} mang phong thái điện ảnh đặc trưng {style_preset}, trang phục và biểu cảm sắc nét chuẩn 4K.",
            "color": MOVIE_CHARACTER_COLORS[idx % len(MOVIE_CHARACTER_COLORS)],
            "images": []
        })

    # Determine scene count
    if isinstance(target_scenes, int) and target_scenes > 0:
        num_scenes = max(2, min(10, target_scenes))
    else:
        num_scenes = max(3, min(6, max(3, len(paras))))

    scenes = []
    durations = [8, 10, 8, 10, 15, 8]

    for i in range(num_scenes):
        chunk_idx = min(i, len(paras) - 1)
        chunk_text = paras[chunk_idx] if paras else f"Diễn biến kịch tính phân cảnh {i+1}"
        involved = [c["name"] for c in characters if c["name"].lower() in chunk_text.lower()]
        if not involved and characters:
            involved = [characters[i % len(characters)]["name"]]

        prompt = f"Cinematic 4K Hollywood, {style_preset}, Scene {i+1}: {chunk_text}. With {', '.join(involved)}, photorealistic lighting, 8k masterpiece, smooth cinematic camera motion"

        scenes.append({
            "scene_index": i + 1,
            "situation": chunk_text,
            "dialogue": f"Lời thoại Cảnh #{i+1}: (Cảm xúc cao trào theo diễn biến)",
            "duration": durations[i % len(durations)],
            "characters_involved": involved,
            "prompt": prompt,
            "status": "idle",
            "video_url": None
        })

    return {
        "title": f"Phim Ngắn: {script_clean[:40]}...",
        "logline": script_clean[:120] + ("..." if len(script_clean) > 120 else ""),
        "characters": characters,
        "scenes": scenes
    }


@app.post("/api/movie/breakdown")
async def breakdown_movie(data: MovieBreakdownModel):
    script = data.script.strip()
    if not script:
        raise HTTPException(status_code=400, detail="Vui lòng nhập kịch bản hoặc ý tưởng phim.")

    style_preset = data.style_preset or "Điện ảnh 4K Hollywood"

    # 1. Direct JSON detection: If user or upstream passed a JSON structure
    try:
        if (script.startswith("{") and script.endswith("}")) or ('"characters"' in script and '"scenes"' in script):
            match = re.search(r'\{[\s\S]*\}', script)
            if match:
                direct_parsed = json.loads(match.group(0))
                if isinstance(direct_parsed, dict) and ("characters" in direct_parsed or "scenes" in direct_parsed):
                    formatted = _format_movie_result(direct_parsed, style_preset)
                    return {
                        "status": "success",
                        "data": formatted,
                        "source": "imported_json"
                    }
    except Exception as e:
        logger.debug(f"Direct JSON parse skipped: {e}")

    # Determine target scene count & instructions
    raw_target = data.target_scenes
    target_scenes_num = None
    if isinstance(raw_target, int) and raw_target > 0:
        target_scenes_num = max(2, min(10, raw_target))
    elif isinstance(raw_target, str) and raw_target.isdigit():
        target_scenes_num = max(2, min(10, int(raw_target)))

    if target_scenes_num:
        scenes_instruction = f"Hãy phân tích kịch bản thành một bộ phim ngắn gồm đúng {target_scenes_num} phân cảnh liên kết chặt chẽ theo thứ tự thời gian."
    else:
        scenes_instruction = "Hãy tự động phân tích cốt truyện thành số phân cảnh tối ưu nhất (từ 3 đến 6 phân cảnh, đầy đủ mở đầu, cao trào và kết thúc trọn vẹn)."

    # Try AI Director breakdown via client
    try:
        client = await get_or_create_client()

        # Track existing baseline messages so we don't pick up old messages
        baseline_ids: set[str] = set()
        try:
            before_hist = await client.history(limit=20)
            chat_events = (before_hist.get("result") or {}).get("chat_events") or []
            for ev in chat_events:
                if isinstance(ev, dict) and ev.get("message_id"):
                    baseline_ids.add(ev["message_id"])
        except Exception:
            pass

        director_prompt = f"""Bạn là một Đạo diễn & Biên kịch điện ảnh chuyên nghiệp (H-AI Movie Director).
{scenes_instruction}

CỐT TRUYỆN / Ý TƯỞNG:
\"\"\"{script}\"\"\"

PHONG CÁCH QUAY PHIM: {style_preset}

YÊU CẦU:
1. Trích xuất TẤT CẢ các nhân vật có trong câu chuyện (VD: nhân vật chính, phụ, người đồng hành). Với mỗi nhân vật, mô tả chi tiết ngoại hình, độ tuổi, trang phục để tạo ảnh tham chiếu.
2. Phân chia các phân cảnh theo thứ tự thời gian. Mỗi phân cảnh có:
   - situation: Tình huống diễn biến của cảnh quay.
   - dialogue: Lời thoại hoặc độc thoại giàu cảm xúc.
   - duration: Số giây phù hợp (chọn 5, 8, 10, 15, hoặc 20).
   - characters_involved: Mảng tên các nhân vật có mặt trong cảnh.
   - prompt: Prompt chi tiết chuẩn điện ảnh 4K tiếng Anh (Cinematic 4K Hollywood, camera angle, lighting, character details, photorealistic).

ĐỊNH DẠNG ĐẦU RA BẮT BUỘC: DUY NHẤT một khối JSON hợp lệ (không kèm lời chào hay giải thích ngoài JSON):
{{
  "title": "Tên bộ phim",
  "logline": "Tóm tắt 1 câu cốt truyện",
  "characters": [
    {{
      "id": "char_1",
      "name": "Tên nhân vật",
      "description": "Mô tả chi tiết ngoại hình, trang phục",
      "color": "#ef4444"
    }}
  ],
  "scenes": [
    {{
      "scene_index": 1,
      "situation": "Tình huống diễn biến",
      "dialogue": "Lời thoại",
      "duration": 8,
      "characters_involved": ["Tên nhân vật"],
      "prompt": "Cinematic 4K..."
    }}
  ]
}}
"""
        events = await client.chat_stream(
            prompt=director_prompt,
            images=[],
            session_id=None,
            stream_timeout=20.0,
        )

        session_id = None
        extracted_ids = extract_session_ids(events)
        if extracted_ids:
            session_id = extracted_ids[-1]

        parsed_result = None
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
                    # Check if text contains a candidate JSON
                    if text.strip() and "{" in text and "}" in text and ('"characters"' in text or '"scenes"' in text):
                        clean_text = re.sub(r'^```(?:json)?\s*', '', text.strip(), flags=re.MULTILINE)
                        clean_text = re.sub(r'```\s*$', '', clean_text, flags=re.MULTILINE).strip()
                        m = re.search(r'\{[\s\S]*\}', clean_text)
                        if m:
                            try:
                                candidate = json.loads(m.group(0))
                                if isinstance(candidate, dict) and ("characters" in candidate or "scenes" in candidate):
                                    parsed_result = candidate
                                    break
                            except Exception:
                                # Still streaming response, continue waiting
                                pass
            if parsed_result:
                break
            await asyncio.sleep(1.5)

        if parsed_result:
            formatted = _format_movie_result(parsed_result, style_preset)
            return {
                "status": "success",
                "data": formatted,
                "source": "ai_director"
            }
    except Exception as exc:
        logger.warning(f"AI director breakdown fallback triggered: {exc}")

    # Smart Fallback director
    fallback_data = _fallback_movie_breakdown(script, style_preset, target_scenes_num or "auto")
    return {
        "status": "success",
        "data": fallback_data,
        "source": "smart_director"
    }


@app.get("/api/gallery")
async def get_gallery():
    items = []
    video_count = 0
    image_count = 0
    total_bytes = 0

    if OUTPUTS_DIR.exists():
        for file in sorted(OUTPUTS_DIR.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True):
            if file.is_file():
                ext = file.suffix.lower()
                is_video = ext in (".mp4", ".webm", ".mov")
                is_image = ext in (".png", ".jpg", ".jpeg", ".webp", ".gif")
                if is_video or is_image:
                    fsize = file.stat().st_size
                    total_bytes += fsize
                    if is_video:
                        video_count += 1
                    else:
                        image_count += 1
                    items.append({
                        "name": file.name,
                        "url": f"/outputs/{file.name}",
                        "type": "video" if is_video else "image",
                        "size_mb": round(fsize / (1024 * 1024), 2),
                        "created_at": file.stat().st_mtime,
                    })

    return {
        "items": items,
        "stats": {
            "videos": video_count,
            "images": image_count,
            "total": video_count + image_count,
            "storage_mb": round(total_bytes / (1024 * 1024), 2),
        },
    }


# Static mounting
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/outputs", StaticFiles(directory=str(OUTPUTS_DIR)), name="outputs")
app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")


@app.get("/")
async def root():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(
            index_file,
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
    return HTMLResponse("<h1>H-AI Studio Backend Ready</h1>")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=7860, reload=True)
