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

app = FastAPI(title="HaiAI Studio", version="2.0.0")

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
                detail="Chưa đăng nhập tài khoản HaiAI. Vui lòng xác thực OTP trước.",
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


def invalidate_subscription_cache():
    global _subscription_cache
    _subscription_cache = {
        "data": None,
        "timestamp": 0.0,
    }


# --- API Routes ---

@app.get("/api/auth/status")
async def auth_status(force: bool = False):
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
    model_name = "Auto (HaiAI Engine)"
    
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
        tier_name = str(tier_obj.get("name") or "HaiAI Miễn phí").replace("Muse", "HaiAI")

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
            "tier_name": "HaiAI Miễn phí",
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

    return {
        "authenticated": logged_in,
        "is_alive": is_alive,
        "email": email or ("Tài khoản HaiAI đã kết nối" if logged_in else None),
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
            detail=f"Lỗi gửi OTP từ hệ thống HaiAI: {exc}. Hãy đảm bảo bạn đã BẬT VPN sang Mỹ trên máy tính!",
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
            "message": "Đăng nhập HaiAI thành công! Hệ thống đã lưu phiên làm việc.",
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

    return {"status": "success", "message": "Đã đăng xuất khỏi HaiAI."}


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
            detail=f"Lỗi tạo video từ HaiAI: {exc}. Kiểm tra kết nối VPN và thử lại.",
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
            detail=f"Lỗi tạo ảnh từ HaiAI: {exc}. Kiểm tra kết nối VPN và thử lại.",
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
            reply_text = "HaiAI đã nhận yêu cầu và đang xử lý!"

        return {
            "status": "success",
            "session_id": session_id,
            "reply": reply_text,
            "events": len(events),
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Lỗi chat với HaiAI: {exc}",
        )


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
        return FileResponse(index_file)
    return HTMLResponse("<h1>HaiAI Studio Backend Ready</h1>")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=7860, reload=True)
