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
    dialogue: Optional[str] = None
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


class CharacterPortraitRequest(BaseModel):
    character_id: str
    character_name: str
    character_description: str
    style_preset: Optional[str] = "Điện ảnh 4K Hollywood"
    angle_index: int = 1  # 1: Cận cảnh mặt, 2: Bán thân 3/4, 3: Toàn thân đứng


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


def slugify_simple(text: str) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", text.strip().lower())
    return re.sub(r"[-\s]+", "_", cleaned) or "char"


def resolve_media_path(path_str: str) -> Optional[Path]:
    if not path_str or not isinstance(path_str, str):
        return None
    p = Path(path_str)
    try:
        if p.exists() and p.is_file():
            return p.resolve()
    except Exception:
        pass
    clean = path_str.replace("\\", "/").lstrip("/")
    if clean.startswith("outputs/"):
        target = OUTPUTS_DIR / clean[len("outputs/"):]
        if target.exists() and target.is_file():
            return target.resolve()
    if clean.startswith("uploads/"):
        target = UPLOADS_DIR / clean[len("uploads/"):]
        if target.exists() and target.is_file():
            return target.resolve()
    if clean.startswith("static/"):
        target = STATIC_DIR / clean[len("static/"):]
        if target.exists() and target.is_file():
            return target.resolve()
    fname = Path(path_str).name
    if fname:
        for folder in (OUTPUTS_DIR, UPLOADS_DIR, STATIC_DIR, STATIC_DIR / "characters"):
            cand = folder / fname
            if cand.exists() and cand.is_file():
                return cand.resolve()
    proj_cand = BASE_DIR / clean
    if proj_cand.exists() and proj_cand.is_file():
        return proj_cand.resolve()
    return None


def is_narration_dialogue(text: str) -> bool:
    if not text:
        return False
    t_lower = text.lower().strip()
    narration_keywords = [
        "dẫn chuyện", "người dẫn chuyện", "thuyết minh", "lời dẫn", "lời kể",
        "người kể chuyện", "narrator", "voiceover", "voice-over", "voice over",
        "lời bình", "bình luận viên", "kể lại:", "dẫn lời:", "off-screen", "tiếng dẫn"
    ]
    for kw in narration_keywords:
        if kw in t_lower:
            return True
    return False


def build_hidden_video_prompt(
    user_prompt: str,
    aspect_ratio: str,
    duration: Optional[int] = 5,
    is_continuation: bool = False,
    scene_index: int = 1,
    style_preset: Optional[str] = None,
    dialogue: Optional[str] = None,
    has_reference_images: bool = False,
) -> str:
    ratio_desc = RATIO_DESCRIPTIONS.get(aspect_ratio, f"tỉ lệ khung hình {aspect_ratio}")
    style_part = f" Phong cách chủ đạo: {style_preset}." if style_preset else ""
    duration_val = duration if (duration and duration > 0) else 5
    duration_desc = f"Thời lượng video: đúng {duration_val} giây ({duration_val} seconds duration clip, pacing & action timing calibrated for exactly {duration_val}s)."

    clean_user_prompt = user_prompt.strip()
    diag_clean = (dialogue or "").strip()
    is_narr = is_narration_dialogue(diag_clean) or is_narration_dialogue(clean_user_prompt)

    dialogue_part = ""
    if is_narr:
        # Strip out any contradictory lip-sync keywords from prompt
        clean_user_prompt = re.sub(r'(?i)spoken dialogue[^.]*\.?', '', clean_user_prompt).strip()
        clean_user_prompt = re.sub(r'(?i)with (clear |natural )?synchronized lip movements?[^.]*\.?', '', clean_user_prompt).strip()
        clean_user_prompt = re.sub(r'(?i)lip-?sync[^.]*\.?', '', clean_user_prompt).strip()

        narr_text = diag_clean if diag_clean else "Thuyết minh dẫn chuyện"
        dialogue_part = (
            f"\n[QUY TẮC BẮT BUỘC: GIỌNG DẪN CHUYỆN / NARRATOR VOICEOVER - KHÔNG MẤP MÁY MÔI]:\n"
            f"- Âm thanh giọng đọc thuyết minh dẫn chuyện ngoài khung hình (Off-screen Narrator Voiceover Audio): \"{narr_text}\".\n"
            f"- YÊU CẦU ĐẶC BIỆT BẮT BUỘC VỀ DIỄN XUẤT: Đây là GIỌNG ĐỌC DẪN CHUYỆN NGOÀI HÌNH ẢNH (Off-screen voiceover narration). "
            f"TẤT CẢ NHÂN VẬT TRÊN MÀN ẢNH TUYỆT ĐỐI KHÔNG NÓI, KHÔNG MẤP MÁY MÔI, KHÔNG CỬ ĐỘNG KHẨU HÌNH, MIỆNG PHẢI NGẬM TỰ NHIÊN "
            f"(CRITICAL ACTING INSTRUCTION: The narration is off-screen. On-screen characters DO NOT SPEAK, DO NOT MOVE THEIR LIPS, MOUTHS MUST REMAIN NATURALLY CLOSED WITH ZERO LIP-SYNC. Characters only perform silent natural acting, physical movements, thoughtful facial reactions and peaceful expressions while the off-screen narrator speaks)."
        )
    elif diag_clean:
        dialogue_part = (
            f"\n[LỜI THOẠI NHÂN VẬT & ĐỒNG BỘ KHẨU HÌNH (LIP-SYNC)]:\n"
            f"- Lời thoại nhân vật trong cảnh (Character Spoken Dialogue): \"{diag_clean}\".\n"
            f"- Yêu cầu diễn xuất & âm thanh: Chỉ nhân vật nói câu thoại này mới cử động khẩu hình môi và phát âm giọng nói đồng bộ "
            f"(Spoken character dialogue with natural synchronized lip-sync and clear audible voice: \"{diag_clean}\"). "
            f"Các nhân vật khác trong cảnh (nếu có) giữ miệng ngậm tự nhiên và im lặng lắng nghe (Other characters keep mouths naturally closed without speaking)."
        )

    ref_images_part = ""
    if has_reference_images:
        ref_images_part = (
            f"\n[YÊU CẦU ĐỒNG NHẤT NHÂN VẬT TỐI CAO TỪ ẢNH THAM CHIẾU - CRITICAL CHARACTER CONSISTENCY]:\n"
            f"- Cảnh quay này có đính kèm các hình ảnh tham chiếu nhân vật (Character Reference Images). "
            f"AI PHẢI GIỮ NGUYÊN 100% CÁC ĐẶC TRƯNG TẠO HÌNH CỦA NHÂN VẬT: khuôn mặt, đường nét ngũ quan, màu mắt, kiểu tóc, "
            f"chi tiết trang phục, màu sắc áo quần và phụ kiện từ các ảnh tham chiếu được gửi lên để bảo đảm sự đồng nhất tuyệt đối qua các phân cảnh.\n"
            f"- (MANDATORY INSTRUCTION: Maintain strict 100% visual identity consistency with the provided reference images. "
            f"The character in the video MUST have the exact same face, eyes, hair color and hairstyle, body proportions, clothing style, textures, and colors as shown in the attached character reference photos. Do not alter or randomize the character's appearance)."
        )

    if is_continuation:
        return (
            f"Hãy tạo một video chuyên nghiệp nối tiếp liền mạch với cảnh video trước đó (Cảnh nối tiếp #{scene_index}). "
            f"{duration_desc} "
            f"Yêu cầu bắt buộc: Giữ nguyên hoàn toàn sự đồng nhất về nhân vật, trang phục, khuôn mặt, bối cảnh môi trường, "
            f"ánh sáng, bảng màu và phong cách góc máy từ cảnh trước để tạo thành một thước phim liên tục mượt mà. "
            f"Chất lượng điện ảnh 4K Ultra HD, chuyển động vật lý tự nhiên, chi tiết sắc nét, âm thanh sống động đồng bộ hoàn hảo, "
            f"{ratio_desc}.{style_part}{dialogue_part}{ref_images_part}\n"
            f"Diễn biến cảnh tiếp theo: {clean_user_prompt}"
        )
    else:
        return (
            f"Hãy tạo một video chuyên nghiệp, {duration_desc} "
            f"Chất lượng điện ảnh 4K Ultra HD, bố cục nghệ thuật xuất sắc, "
            f"chuyển động camera và vật lý mượt mà chân thực, ánh sáng điện ảnh có chiều sâu, chi tiết cực kỳ sắc nét, "
            f"kèm âm thanh và hiệu ứng tiếng động sống động đồng bộ hoàn hảo, {ratio_desc}.{style_part}{dialogue_part}{ref_images_part}\n"
            f"Nội dung cảnh quay: {clean_user_prompt}"
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
        # Resolve all provided image paths reliably (supports /outputs/, /uploads/, /static/, and absolute paths)
        resolved_images: list[Path] = []
        for p in data.image_paths:
            resolved = resolve_media_path(p)
            if resolved and resolved not in resolved_images:
                resolved_images.append(resolved)

        # Inject invisible professional & continuation prompt with strict character consistency
        enhanced_prompt = build_hidden_video_prompt(
            user_prompt=data.prompt,
            aspect_ratio=data.aspect_ratio,
            duration=data.duration,
            is_continuation=data.is_continuation,
            scene_index=data.scene_index,
            style_preset=data.style_preset,
            dialogue=data.dialogue,
            has_reference_images=bool(resolved_images),
        )

        # Use existing session_id if continuing a video sequence
        target_session_id = data.session_id if (data.is_continuation and data.session_id) else None

        result = await client.generate_video(
            prompt=enhanced_prompt,
            images=resolved_images,
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
        dur_raw = sc.get("duration", 8)
        try:
            dur = int(dur_raw)
            if dur < 5:
                dur = 5
            elif dur > 20:
                dur = 20
        except (ValueError, TypeError):
            dur = 8

        diag = (sc.get("dialogue") or "").strip()
        cinematic_prompt = (sc.get("prompt") or f"Cinematic 4K Hollywood, {style_preset}, Scene {idx+1}: {sc.get('situation', '')}. 8k, photorealistic lighting, cinematic camera motion").strip()

        is_narr = is_narration_dialogue(diag) or is_narration_dialogue(cinematic_prompt)

        if is_narr:
            # Clean out any contradictory lip-sync keywords
            cinematic_prompt = re.sub(r'(?i)spoken dialogue[^.]*\.?', '', cinematic_prompt).strip()
            cinematic_prompt = re.sub(r'(?i)with (clear |natural )?synchronized lip movements?[^.]*\.?', '', cinematic_prompt).strip()
            cinematic_prompt = re.sub(r'(?i)lip-?sync[^.]*\.?', '', cinematic_prompt).strip()

            if "off-screen" not in cinematic_prompt.lower() and "narrator" not in cinematic_prompt.lower():
                narr_text = diag if diag else "Thuyết minh dẫn chuyện"
                cinematic_prompt += (
                    f' Off-screen voiceover narration: "{narr_text}". '
                    f'Critical acting constraint: The narration is off-screen. On-screen characters DO NOT SPEAK, DO NOT MOVE LIPS, mouths remain naturally closed with zero lip-sync, characters perform silent expressive acting only.'
                )
        else:
            if diag and "spoken dialogue" not in cinematic_prompt.lower() and "dialogue:" not in cinematic_prompt.lower():
                cinematic_prompt += f' Spoken character dialogue: "{diag}" with natural synchronized lip movement and clear audible voice.'

        formatted_scenes.append({
            "scene_index": idx + 1,
            "situation": sc.get("situation") or f"Diễn biến phân cảnh {idx+1}",
            "dialogue": diag or f"Lời thoại Cảnh #{idx+1}",
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
                    "images": [
                        {"id": "img_cobe_1", "name": "Ảnh 1: Cận Cảnh Mặt", "url": "/static/characters/cobe_1_closeup.png", "local_path": "static/characters/cobe_1_closeup.png"},
                        {"id": "img_cobe_2", "name": "Ảnh 2: Bán Thân 3/4", "url": "/static/characters/cobe_2_halfbody.png", "local_path": "static/characters/cobe_2_halfbody.png"},
                        {"id": "img_cobe_3", "name": "Ảnh 3: Toàn Thân Đứng", "url": "/static/characters/cobe_3_fullbody.png", "local_path": "static/characters/cobe_3_fullbody.png"}
                    ]
                },
                {
                    "id": "char_2",
                    "name": "Bà Ngoại",
                    "description": "Phụ nữ khoảng 70 tuổi, dáng người cao gầy, tóc bạc trắng dài buông xõa, khuôn mặt hiền hậu phúc hậu với nụ cười ấm áp, đôi mắt nâu dịu dàng. Mặc áo choàng dài màu trắng ngà phát sáng nhẹ, khăn choàng ren trắng, trông như một thiên thần, xuất hiện trong ánh sáng vàng ấm của que diêm.",
                    "color": "#f59e0b",
                    "images": [
                        {"id": "img_bangoai_1", "name": "Ảnh 1: Cận Cảnh Mặt", "url": "/static/characters/bangoai_1_closeup.png", "local_path": "static/characters/bangoai_1_closeup.png"},
                        {"id": "img_bangoai_2", "name": "Ảnh 2: Bán Thân 3/4", "url": "/static/characters/bangoai_2_halfbody.png", "local_path": "static/characters/bangoai_2_halfbody.png"},
                        {"id": "img_bangoai_3", "name": "Ảnh 3: Toàn Thân Đứng", "url": "/static/characters/bangoai_3_fullbody.png", "local_path": "static/characters/bangoai_3_fullbody.png"}
                    ]
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
                    "situation": "Đêm giao thừa, tuyết rơi dày trên phố cổ châu Âu, cô bé bán diêm chân trần run rẩy ôm giỏ diêm đi giữa dòng người vội vã về nhà đón năm mới, cố cất tiếng mời mọi người mua diêm.",
                    "dialogue": "Cô Bé Bán Diêm: \"Ai mua diêm không ạ? Diêm đây... chỉ một xu thôi ạ...\"",
                    "duration": 10,
                    "characters_involved": ["Cô Bé Bán Diêm", "Người Qua Đường"],
                    "prompt": "Cinematic 4K Hollywood film, wide shot of a snowy European old town street on New Year's Eve at dusk. A 9-year-old little girl with long messy blonde hair covered in snow, wearing an oversized torn dark brown coat and grey wool dress, barefoot with purple cold feet, hugs a wicker basket of matches and speaks with trembling lips. Spoken character dialogue: \"Ai mua diêm không ạ? Diêm đây... chỉ một xu thôi ạ...\" with natural synchronized lip-sync and clear trembling voice. Warm shop lights, photorealistic, 4K Ultra HD."
                },
                {
                    "scene_index": 2,
                    "situation": "Không bán được que diêm nào, cô bé co ro ngồi nép vào góc tường giữa hai ngôi nhà trong gió tuyết rít từng cơn, run rẩy quẹt que diêm đầu tiên để sưởi ấm đôi bàn tay tê cóng.",
                    "dialogue": "Dẫn chuyện: \"Đêm giao thừa giá rét, không bán được que diêm nào, em co ro nép vào góc tường lạnh buốt và quẹt que diêm đầu tiên để sưởi ấm đôi bàn tay tê cóng.\"",
                    "duration": 10,
                    "characters_involved": ["Cô Bé Bán Diêm"],
                    "prompt": "Cinematic 4K Hollywood film, intimate close-up and medium shot of a 9-year-old girl with blonde hair in snow, wearing a torn dark brown coat, crouching in a snowy corner between two brick houses at night. She strikes a match with trembling hands, the golden flame illuminating her pale hopeful face. Off-screen narrator voiceover: \"Đêm giao thừa giá rét, không bán được que diêm nào, em co ro nép vào góc tường lạnh buốt và quẹt que diêm đầu tiên để sưởi ấm đôi bàn tay tê cóng.\" Critical acting constraint: The narrator is off-screen. Characters on screen DO NOT SPEAK, DO NOT MOVE LIPS, mouths remain naturally closed with zero lip-sync, shivering silent acting, 4K Ultra HD."
                },
                {
                    "scene_index": 3,
                    "situation": "Trong ánh sáng ấm áp lung linh của ngọn lửa diêm, một lò sưởi ấm áp với ngọn lửa hồng rực rỡ và bàn tiệc thịnh soạn hiện ra kỳ diệu ngay trước mắt cô bé.",
                    "dialogue": "Dẫn chuyện: \"Trong ánh lửa que diêm bập bùng, một chiếc lò sưởi bằng đồng sáng loáng với ngọn lửa ấm áp và bàn tiệc thịnh soạn hiện ra kỳ diệu trước mắt em.\"",
                    "duration": 9,
                    "characters_involved": ["Cô Bé Bán Diêm"],
                    "prompt": "Cinematic 4K Hollywood film, magical medium shot in a snowy street corner. A 9-year-old girl gazes in pure wonder and joy at a glowing vision of a crackling warm fireplace and a delicious roast goose feast floating in warm golden light. Off-screen narrator voiceover: \"Trong ánh lửa que diêm bập bùng, một chiếc lò sưởi bằng đồng sáng loáng với ngọn lửa ấm áp và bàn tiệc thịnh soạn hiện ra kỳ diệu trước mắt em.\" Critical acting constraint: The narrator is off-screen. Characters on screen DO NOT SPEAK, DO NOT MOVE LIPS, mouths remain naturally closed with zero lip-sync, silent emotional acting, warm magical light against dark snow, 4K Ultra HD."
                },
                {
                    "scene_index": 4,
                    "situation": "Cô bé vội quẹt thêm nhiều que diêm, và trong ánh lửa rực rỡ nhất, hình ảnh bà ngoại hiền hậu hiện ra mỉm cười dang rộng vòng tay đón em, cô bé khẩn thiết gọi bà.",
                    "dialogue": "Cô Bé Bán Diêm: \"Bà ơi! Cho cháu đi với bà! Cháu biết que diêm tắt thì bà cũng biến mất như lò sưởi kia... Bà ở lại với cháu nhé!\"",
                    "duration": 10,
                    "characters_involved": ["Cô Bé Bán Diêm", "Bà Ngoại"],
                    "prompt": "Cinematic 4K Hollywood film, emotional medium shot in snowy corner. A 9-year-old girl with blonde hair kneels in snow, looking up with tears of joy at a kind 70-year-old grandmother with glowing silver hair in an ivory gown opening warm arms. The girl speaks with heartfelt longing. Spoken character dialogue: \"Bà ơi! Cho cháu đi với bà! Cháu biết que diêm tắt thì bà cũng biến mất như lò sưởi kia... Bà ở lại với cháu nhé!\" with natural synchronized lip-sync and clear emotional voice. Grandmother listens with kind smile and mouth closed. 4K Ultra HD."
                },
                {
                    "scene_index": 5,
                    "situation": "Bà ngoại dịu dàng cúi xuống, ôm chầm lấy cô bé trong vòng tay ấm áp và cất giọng yêu thương vỗ về.",
                    "dialogue": "Bà Ngoại: \"Đừng sợ cháu ngoan, bà đến đón cháu đây, chúng ta sẽ về nơi không còn đói rét và đau buồn nữa.\"",
                    "duration": 8,
                    "characters_involved": ["Cô Bé Bán Diêm", "Bà Ngoại"],
                    "prompt": "Cinematic 4K Hollywood film, warm radiant medium close-up of the 70-year-old grandmother in glowing ivory gown gently embracing the little girl in the snow. The grandmother speaks softly with deep maternal love. Spoken character dialogue: \"Đừng sợ cháu ngoan, bà đến đón cháu đây, chúng ta sẽ về nơi không còn đói rét và đau buồn nữa.\" with natural synchronized lip-sync and clear gentle voice. The little girl smiles peacefully with mouth closed. Golden celestial light, 4K Ultra HD."
                },
                {
                    "scene_index": 6,
                    "situation": "Sáng mùng một năm mới, tuyết ngừng rơi, người qua đường phát hiện cô bé ngồi bình yên bên góc tường, môi vẫn hé nụ cười tươi tắn — linh hồn em và bà ngoại đã cùng bay về thiên đường ngập tràn ánh sáng.",
                    "dialogue": "Dẫn chuyện: \"Sáng hôm sau, người ta thấy em ngồi mỉm cười thanh thản giữa tuyết trắng — em và bà ngoại đã nắm tay nhau bay về thiên đường hạnh phúc mãi mãi.\"",
                    "duration": 9,
                    "characters_involved": ["Cô Bé Bán Diêm", "Bà Ngoại", "Người Qua Đường"],
                    "prompt": "Cinematic 4K Hollywood film, poignant wide and crane-up shot on snowy European street at sunrise. A little girl sits peacefully smiling against a brick wall holding burnt matchsticks, while a radiant vision of grandmother and girl ascending together into golden heavenly clouds. Off-screen narrator voiceover: \"Sáng hôm sau, người ta thấy em ngồi mỉm cười thanh thản giữa tuyết trắng — em và bà ngoại đã nắm tay nhau bay về thiên đường hạnh phúc mãi mãi.\" Critical acting constraint: The narrator is off-screen. Characters on screen DO NOT SPEAK, DO NOT MOVE LIPS, mouths naturally closed with zero lip-sync, silent peaceful emotional acting, slow crane-up, 4K Ultra HD."
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
                    "images": [
                        {"id": "img_caube_1", "name": "Ảnh 1: Cận Cảnh Mặt", "url": "/static/characters/caube_1_closeup.png", "local_path": "static/characters/caube_1_closeup.png"},
                        {"id": "img_caube_2", "name": "Ảnh 2: Bán Thân 3/4", "url": "/static/characters/caube_2_halfbody.png", "local_path": "static/characters/caube_2_halfbody.png"},
                        {"id": "img_caube_3", "name": "Ảnh 3: Toàn Thân Đứng", "url": "/static/characters/caube_3_fullbody.png", "local_path": "static/characters/caube_3_fullbody.png"}
                    ]
                },
                {
                    "id": "char_2",
                    "name": "Bà Cụ",
                    "description": "Phụ nữ khoảng 75 tuổi, dáng người nhỏ gầy, lưng hơi còng, tóc bạc phơ dài búi gọn sau gáy, khuôn mặt phúc hậu nhiều nếp nhăn, đôi mắt hiền từ đầy sự từng trải. Mặc áo dài nâu sẫm, khăn choàng xám, váy dài màu xanh rêu, đi dép vải, tay cầm một thỏi sắt lớn.",
                    "color": "#f59e0b",
                    "images": [
                        {"id": "img_bacu_1", "name": "Ảnh 1: Cận Cảnh Mặt", "url": "/static/characters/bacu_1_closeup.png", "local_path": "static/characters/bacu_1_closeup.png"},
                        {"id": "img_bacu_2", "name": "Ảnh 2: Bán Thân 3/4", "url": "/static/characters/bacu_2_halfbody.png", "local_path": "static/characters/bacu_2_halfbody.png"},
                        {"id": "img_bacu_3", "name": "Ảnh 3: Toàn Thân Đứng", "url": "/static/characters/bacu_3_fullbody.png", "local_path": "static/characters/bacu_3_fullbody.png"}
                    ]
                }
            ],
            "scenes": [
                {
                    "scene_index": 1,
                    "situation": "Buổi sáng sớm trong góc học tập của ngôi nhà tranh ở làng quê xưa, cậu bé ngồi bên bàn gỗ chống cằm đọc sách với vẻ mặt chán ngán, ngáp dài ngáp ngắn vì lười học.",
                    "dialogue": "Dẫn chuyện: \"Ngày xửa ngày xưa, có một cậu bé rất ham chơi và lười học bài.\"",
                    "duration": 7,
                    "characters_involved": ["Cậu Bé"],
                    "prompt": "Cinematic 4K Hollywood film, intimate medium shot inside an old thatched hut in ancient Vietnam at early morning. A 10-year-old boy with short messy black hair, wearing a rough light brown cloth shirt, sits chin-in-hand at a small wooden desk, yawning and sighing lazily beside an open ancient book. Soft golden morning light through bamboo walls. Off-screen narrator voiceover: \"Ngày xửa ngày xưa, có một cậu bé rất ham chơi và lười học bài.\" Critical acting constraint: The narrator is off-screen. Characters on screen DO NOT SPEAK, DO NOT MOVE LIPS, mouths remain naturally closed with zero lip-sync, bored silent acting, 4K Ultra HD."
                },
                {
                    "scene_index": 2,
                    "situation": "Cậu bé bực dọc gấp mạnh cuốn sách lại, lắc đầu chán nản và cất tiếng than thở rồi bỏ học đi chơi.",
                    "dialogue": "Cậu Bé: \"Chữ nghĩa gì mà khó nhớ thế này, chán quá đi mất!\"",
                    "duration": 6,
                    "characters_involved": ["Cậu Bé"],
                    "prompt": "Cinematic 4K Hollywood film, medium shot of a 10-year-old boy in rough light brown cloth shirt at a wooden desk in ancient thatched room. He frowns, slams the book shut, pushes his chair back and speaks with frustration. Spoken character dialogue: \"Chữ nghĩa gì mà khó nhớ thế này, chán quá đi mất!\" with natural synchronized lip-sync and clear boy voice. Photorealistic, warm cinematic lighting, 4K Ultra HD."
                },
                {
                    "scene_index": 3,
                    "situation": "Trên đường làng ven bờ suối róc rách, trời nắng nhẹ, cậu bé đi chơi về thì tò mò dừng bước khi thấy một bà cụ tóc bạc đang cặm cụi mài một thỏi sắt lớn vào tảng đá ven bờ suối.",
                    "dialogue": "Dẫn chuyện: \"Một hôm đi chơi qua bờ suối, cậu thấy một bà cụ đang miệt mài mài một thỏi sắt lớn vào tảng đá.\"",
                    "duration": 8,
                    "characters_involved": ["Cậu Bé", "Bà Cụ"],
                    "prompt": "Cinematic 4K Hollywood film, wide and medium tracking shot of a village road beside a clear stream in ancient Vietnam under gentle sunlight. A 10-year-old boy stops curiously and tilts his head, watching a 75-year-old woman with silver hair bun in dark brown robe sitting diligently grinding a big iron bar on a stone by the water. Off-screen narrator voiceover: \"Một hôm đi chơi qua bờ suối, cậu thấy một bà cụ đang miệt mài mài một thỏi sắt lớn vào tảng đá.\" Critical acting constraint: The narrator is off-screen. Characters on screen DO NOT SPEAK, DO NOT MOVE LIPS, mouths naturally closed with zero lip movement, boy looks curious, grandmother grinds iron diligently, 4K Ultra HD."
                },
                {
                    "scene_index": 4,
                    "situation": "Cậu bé tiến lại gần tảng đá ven suối, chắp tay cúi đầu chào lễ phép rồi cất tiếng hỏi bà cụ với ánh mắt ngạc nhiên.",
                    "dialogue": "Cậu Bé: \"Bà ơi, bà đang làm gì thế ạ? Thỏi sắt to thế này làm sao mà mài thành kim được ạ?\"",
                    "duration": 7,
                    "characters_involved": ["Cậu Bé", "Bà Cụ"],
                    "prompt": "Cinematic 4K Hollywood film, intimate medium shot beside a large rock by the stream under sunlight. A 10-year-old boy in rough light brown shirt clasps his hands, bows respectfully and speaks with wide, curious eyes. Spoken character dialogue: \"Bà ơi, bà đang làm gì thế ạ? Thỏi sắt to thế này làm sao mà mài thành kim được ạ?\" with natural synchronized lip-sync and clear boy voice. The old woman listens with gentle smile and closed mouth. 4K Ultra HD."
                },
                {
                    "scene_index": 5,
                    "situation": "Bà cụ dừng tay mài, ngước lên nhìn cậu bé với ánh mắt hiền từ đầy tình thương và giải thích bài học kiên trì.",
                    "dialogue": "Bà Cụ: \"Mỗi ngày mài một tí thì thỏi sắt to cũng sẽ thành kim khâu thôi cháu ạ.\"",
                    "duration": 8,
                    "characters_involved": ["Cậu Bé", "Bà Cụ"],
                    "prompt": "Cinematic 4K Hollywood film, warm medium close-up of the 75-year-old Vietnamese grandmother with silver hair bun in dark brown robe holding the iron bar. She looks up with kind wrinkles and speaks gently with deep wisdom. Spoken character dialogue: \"Mỗi ngày mài một tí thì thỏi sắt to cũng sẽ thành kim khâu thôi cháu ạ.\" with natural synchronized lip-sync and clear gentle elder voice. The boy listens quietly with closed mouth. 4K Ultra HD."
                },
                {
                    "scene_index": 6,
                    "situation": "Cậu bé bừng sáng ánh mắt, ngộ ra chân lý, cúi gập người cảm ơn bà cụ rồi phấn khởi hứa sẽ chăm chỉ học hành.",
                    "dialogue": "Cậu Bé: \"Cháu hiểu rồi ạ! Cháu cảm ơn bà! Từ nay cháu sẽ chăm học, kiên trì như bà mài sắt vậy!\"",
                    "duration": 7,
                    "characters_involved": ["Cậu Bé", "Bà Cụ"],
                    "prompt": "Cinematic 4K Hollywood film, bright medium shot of the 10-year-old boy by the sunlit stream, smiling brightly with enlightened eyes, bowing deeply in gratitude and speaking enthusiastically. Spoken character dialogue: \"Cháu hiểu rồi ạ! Cháu cảm ơn bà! Từ nay cháu sẽ chăm học, kiên trì như bà mài sắt vậy!\" with natural synchronized lip-sync and expressive happy face. The grandmother smiles warmly in background with mouth closed. 4K Ultra HD."
                },
                {
                    "scene_index": 7,
                    "situation": "Những ngày sau đó, trong góc học tập thân quen, cậu bé ngồi ngay ngắn chăm chú đọc sách dưới ánh nắng sớm mai, bên cạnh là chiếc kim khâu sáng bóng như một minh chứng cho sự kiên trì.",
                    "dialogue": "Dẫn chuyện: \"Từ đó, cậu bé chăm chỉ học hành, không còn lười biếng nữa và đã trở thành người thành tài, đúng như câu tục ngữ: có công mài sắt, có ngày nên kim.\"",
                    "duration": 8,
                    "characters_involved": ["Cậu Bé"],
                    "prompt": "Cinematic 4K Hollywood film, inspiring medium shot of the study corner inside the thatched house at sunrise. The 10-year-old boy sits upright, attentively reading an ancient book with a determined, happy smile. Next to the book is a gleaming steel sewing needle reflecting golden morning rays. Off-screen narrator voiceover: \"Từ đó, cậu bé chăm chỉ học hành, không còn lười biếng nữa và đã trở thành người thành tài, đúng như câu tục ngữ: có công mài sắt, có ngày nên kim.\" Critical acting constraint: The narrator is off-screen. Characters on screen DO NOT SPEAK, DO NOT MOVE LIPS, mouths remain naturally closed with zero lip movement, peaceful focused expression, slow crane-up, 4K Ultra HD."
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
        scenes_instruction = (
            "QUY TẮC CỐT LÕI: Phân tích kịch bản theo nguyên tắc 1 LỜI THOẠI = 1 PHÂN CẢNH RIÊNG BIỆT. "
            "Cứ mỗi lời thoại của nhân vật (hoặc lời dẫn chuyển biến) là ĐÚNG 1 PHÂN CẢNH. "
            "Không sợ quá nhiều phân cảnh, hãy chia đầy đủ và chi tiết từng câu thoại!"
        )

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

YÊU CẦU ĐẶC BIỆT:
1. NGUYÊN TẮC VÀNG: 1 LỜI THOẠI = 1 PHÂN CẢNH RIÊNG BIỆT (TÁCH BIỆT HOÀN TOÀN DẪN CHUYỆN VÀ THOẠI NHÂN VẬT):
   - Cứ mỗi lời thoại của một nhân vật (hoặc mỗi đoạn thuyết minh / dẫn chuyện) phải tạo thành ĐÚNG 1 PHÂN CẢNH riêng biệt.
   - TUYỆT ĐỐI KHÔNG GỘP lời dẫn chuyện và lời thoại nhân vật trong cùng 1 phân cảnh!
   - Khi nhân vật A nói -> 1 cảnh riêng tập trung vào A đang nói với biểu cảm và cử động môi đồng bộ (lip-sync).
   - Khi nhân vật B nói -> 1 cảnh tiếp theo chuyển sang B đáp lại.
   - Khi có người dẫn chuyện (Voice-over) -> 1 cảnh riêng biệt dành cho người dẫn chuyện.
   - Không giới hạn số lượng phân cảnh (không sợ nhiều cảnh).
2. THỜI LƯỢNG MỖI PHÂN CẢNH (5 đến 20 GIÂY TÙY Ý):
   - Phân tích ngữ cảnh và độ dài câu thoại để chọn số giây chính xác: từ 5 đến 20 giây (5s, 6s, 8s, 10s, 12s, 15s, 18s, 20s).
   - Câu thoại ngắn: 5 - 8 giây.
   - Câu thoại vừa: 8 - 12 giây.
   - Câu thoại dài, giải thích hoặc cao trào: 12 - 20 giây.
3. QUY ĐỊNH BẮT BUỘC VỀ LỜI DẪN CHUYỆN VS THOẠI NHÂN VẬT TRONG 'prompt' (CHỐNG LỖI MẤP MÁY MÔI KHI DẪN CHUYỆN):
   - NẾU LÀ CẢNH DẪN CHUYỆN / THUYẾT MINH (dialogue bắt đầu bằng 'Dẫn chuyện:', 'Người dẫn chuyện:', 'Thuyết minh:'):
     'prompt' BẮT BUỘC PHẢI DÙNG CẤU TRÚC: Off-screen narrator voiceover: "...". Critical acting constraint: The narrator is off-screen. Characters on screen DO NOT SPEAK, DO NOT MOVE THEIR LIPS, zero lip-sync, mouths remain naturally closed, characters only perform silent natural acting, physical movements and facial reactions.
     TUYỆT ĐỐI KHÔNG DÙNG từ 'spoken dialogue' hay 'lip-sync' cho cảnh dẫn chuyện để tránh nhân vật bị mấp máy môi!
   - NẾU LÀ CẢNH NHÂN VẬT NÓI (dialogue của nhân vật):
     'prompt' DÙNG CẤU TRÚC: Spoken character dialogue: "..." with natural synchronized lip movements, audible voice and matching facial expressions. Other characters in the scene keep mouths closed and listen silently.
4. TRÍCH XUẤT NHÂN VẬT:
   - Trích xuất TẤT CẢ các nhân vật có trong câu chuyện. Với mỗi nhân vật, mô tả chi tiết ngoại hình, độ tuổi, trang phục để tạo ảnh tham chiếu.

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


@app.post("/api/movie/generate_character_portrait")
async def generate_character_portrait_endpoint(data: CharacterPortraitRequest):
    if not data.character_name.strip():
        raise HTTPException(status_code=400, detail="Tên nhân vật không được để trống.")

    client = await get_or_create_client()
    try:
        angle_info = {
            1: ("Cận cảnh mặt", "Chân dung cận cảnh khuôn mặt (close-up face portrait), nhìn trực diện máy quay, nét mặt rõ ràng, đôi mắt chân thực giàu cảm xúc, chi tiết tóc, làn da và phụ kiện sắc nét."),
            2: ("Bán thân 3/4", "Chân dung trung cảnh bán thân góc nghiêng 3/4 (medium half-body portrait), giữ nguyên hoàn toàn khuôn mặt và kiểu tóc từ góc cận cảnh. Rõ nét trang phục, màu sắc áo quần và tư thế tự nhiên."),
            3: ("Toàn thân đứng", "Ảnh toàn thân đứng tự nhiên (full-body shot), giữ nguyên 100% trang phục, khuôn mặt, mái tóc, giày dép và dáng người từ góc bán thân trong bối cảnh câu chuyện.")
        }
        angle_name, angle_desc = angle_info.get(data.angle_index, ("Chân dung", "Chân dung nhân vật sắc nét"))

        prompt_text = (
            f"Chân dung nghệ thuật chuẩn {angle_name} của nhân vật {data.character_name}. "
            f"Mô tả ngoại hình & trang phục: {data.character_description}. {angle_desc} "
            f"Phong cách {data.style_preset}, chất lượng điện ảnh 8K Ultra HD, ánh sáng nghệ thuật có chiều sâu, cực kỳ sắc nét, photorealistic."
        )

        prompt_full = build_hidden_image_prompt(
            user_prompt=prompt_text,
            aspect_ratio="1:1",
            style_preset=data.style_preset,
        )

        baseline_hist = await client.history(limit=50)
        baseline_media = {m["identity"] for m in extract_all_media_refs(baseline_hist)}

        events = await client.chat_stream(
            prompt=prompt_full,
            images=[],
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

        if not found_images:
            raise HTTPException(status_code=504, detail="H-AI chưa trả về ảnh trong thời gian quy định.")

        ref = found_images[0]
        ext = ".png" if "png" in (ref.get("mime_type") or "") else ".jpg"
        dest_name = f"char_{slugify_simple(data.character_name)}_{data.angle_index}_{int(time.time())}{ext}"
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
            except Exception:
                pass

        if data_bytes:
            dest_path.write_bytes(data_bytes)

        invalidate_subscription_cache()

        return {
            "status": "success",
            "character_id": data.character_id,
            "angle_index": data.angle_index,
            "angle_name": angle_name,
            "image": {
                "id": f"img_{data.character_id}_{data.angle_index}_{int(time.time())}",
                "name": f"Ảnh {data.angle_index}: {angle_name}",
                "url": f"/outputs/{dest_name}",
                "local_path": str(dest_path),
            }
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi tạo ảnh nhân vật: {exc}")


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
