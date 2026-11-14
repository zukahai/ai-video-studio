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
import unicodedata
from pathlib import Path
from typing import Any, Optional

import httpx

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
MOVIES_DIR = OUTPUTS_DIR / "movies"

OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)
STATE_DIR.mkdir(parents=True, exist_ok=True)
MOVIES_DIR.mkdir(parents=True, exist_ok=True)

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
    previous_video_url: Optional[str] = None
    session_id: Optional[str] = None
    scene_index: int = 1
    style_preset: Optional[str] = None
    character_voices: Optional[dict[str, str]] = None
    enable_narration: bool = False
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
    style_preset: Optional[str] = "Điện ảnh HD Hollywood"
    aspect_ratio: Optional[str] = "16:9"
    target_scenes: Optional[int | str] = "auto"
    enable_narration: bool = False


class MergeVideosModel(BaseModel):
    video_urls: list[str]
    output_filename: Optional[str] = None


class CharacterPortraitRequest(BaseModel):
    character_id: str
    character_name: str
    character_description: str
    style_preset: Optional[str] = "Điện ảnh HD Hollywood"
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


def remove_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    res = "".join([c for c in nfkd if not unicodedata.combining(c)])
    return res.replace("đ", "d").replace("Đ", "D")


def slugify_simple(text: str) -> str:
    t = remove_accents(text)
    cleaned = re.sub(r"[^\w\s-]", "", t.strip().lower())
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


_NARRATION_PREFIX_RE = re.compile(
    r'^\s*(?:người\s+dẫn\s+chuyện|dẫn\s+chuyện|người\s+kể\s+chuyện|thuyết\s+minh|lời\s+dẫn|lời\s+kể|lời\s+bình|bình\s+luận\s+viên|kể\s+lại|dẫn\s+lời|narrator|voice[- ]?over|off[- ]?screen)\s*[:\-–—]\s*',
    flags=re.IGNORECASE,
)


def extract_speaker_and_spoken_text(text: str) -> tuple[Optional[str], str]:
    """Split 'Speaker: \"Spoken words\"' into (speaker, clean_spoken_words) so video TTS never reads speaker labels aloud."""
    raw = (text or "").strip()
    if not raw:
        return None, ""
    m_narr = _NARRATION_PREFIX_RE.match(raw)
    if m_narr:
        spoken = raw[m_narr.end():].strip().strip('"').strip("'").strip()
        return None, spoken
    m_spk = re.match(r'^([^:\n]{1,40})\s*:\s*(.+)$', raw, flags=re.DOTALL)
    if m_spk:
        speaker = m_spk.group(1).strip()
        spoken = m_spk.group(2).strip().strip('"').strip("'").strip()
        return speaker, spoken
    return None, raw.strip('"').strip("'").strip()


def clean_narration_prefixes_in_prompt(prompt: str) -> str:
    """Remove literal narrator prefixes inside quoted voiceover strings in a prompt."""
    if not prompt:
        return ""
    return re.sub(
        r'(["\'])\s*(?:người\s+dẫn\s+chuyện|dẫn\s+chuyện|người\s+kể\s+chuyện|thuyết\s+minh|lời\s+dẫn|lời\s+kể|narrator|voice[- ]?over)\s*[:\-–—]\s*',
        r'\1',
        prompt,
        flags=re.IGNORECASE,
    )


def _lookup_character_voice(speaker_name: Optional[str], character_voices: Optional[dict[str, str]]) -> str:
    """Find a matching voice prompt for speaker_name (case-insensitive)."""
    if not speaker_name or not character_voices:
        return ""
    s_norm = speaker_name.strip().lower()
    for k, v in character_voices.items():
        if not v or not str(v).strip():
            continue
        k_norm = str(k).strip().lower()
        if k_norm == s_norm or s_norm in k_norm or k_norm in s_norm:
            return str(v).strip()
    return ""


def extract_last_frame_from_video(video_path: Path) -> Optional[Path]:
    """Extract the last frame of a video file using imageio_ffmpeg and return the saved image path."""
    if not video_path or not video_path.exists():
        return None
    try:
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        out_path = OUTPUTS_DIR / f"lastframe_{video_path.stem}_{int(time.time() * 1000)}.jpg"
        for seek_offset in ("-0.15", "-0.5"):
            proc = subprocess.run(
                [ffmpeg_exe, "-sseof", seek_offset, "-i", str(video_path), "-frames:v", "1", "-q:v", "2", "-y", str(out_path)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0:
                return out_path
        # Fallback: extract first/near-end frame without sseof if video is ultra short
        proc2 = subprocess.run(
            [ffmpeg_exe, "-i", str(video_path), "-vframes", "1", "-q:v", "2", "-y", str(out_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc2.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0:
            return out_path
    except Exception as exc:
        logger.warning(f"Failed to extract last frame from {video_path}: {exc}")
    return None


def build_hidden_video_prompt(
    user_prompt: str,
    aspect_ratio: str,
    duration: Optional[int] = 5,
    is_continuation: bool = False,
    scene_index: int = 1,
    style_preset: Optional[str] = None,
    dialogue: Optional[str] = None,
    has_reference_images: bool = False,
    character_voices: Optional[dict[str, str]] = None,
    enable_narration: bool = False,
    has_last_frame: bool = False,
) -> str:
    ratio_desc = RATIO_DESCRIPTIONS.get(aspect_ratio, f"tỉ lệ khung hình {aspect_ratio}")
    style_part = f" Phong cách chủ đạo: {style_preset}." if style_preset else ""
    duration_val = duration if (duration and duration > 0) else 5
    duration_desc = f"Thời lượng video: đúng {duration_val} giây ({duration_val} seconds duration clip, pacing & action timing calibrated for exactly {duration_val}s)."

    clean_user_prompt = clean_narration_prefixes_in_prompt(user_prompt.strip())
    diag_clean = (dialogue or "").strip()
    is_narr = is_narration_dialogue(diag_clean) or is_narration_dialogue(clean_user_prompt)

    dialogue_part = ""
    if is_narr:
        # Strip out any contradictory lip-sync keywords from prompt
        clean_user_prompt = re.sub(r'(?i)spoken dialogue[^.]*\.?', '', clean_user_prompt).strip()
        clean_user_prompt = re.sub(r'(?i)with (clear |natural )?synchronized lip movements?[^.]*\.?', '', clean_user_prompt).strip()
        clean_user_prompt = re.sub(r'(?i)lip-?sync[^.]*\.?', '', clean_user_prompt).strip()

        if enable_narration:
            _, narr_spoken = extract_speaker_and_spoken_text(diag_clean)
            narr_text = narr_spoken if narr_spoken else "Thuyết minh dẫn chuyện"
            narr_voice = (
                _lookup_character_voice("Người dẫn chuyện", character_voices)
                or _lookup_character_voice("Dẫn chuyện", character_voices)
                or _lookup_character_voice("Narrator", character_voices)
                or "Giọng người dẫn chuyện trầm ấm, truyền cảm, phát âm tiếng Việt rõ ràng, nhịp độ vừa phải và đồng nhất cùng một chất giọng xuyên suốt bộ phim (Consistent warm, clear, expressive Vietnamese narrator voice throughout the entire film)"
            )
            dialogue_part = (
                f"\n[QUY TẮC BẮT BUỘC: GIỌNG DẪN CHUYỆN / NARRATOR VOICEOVER - KHÔNG MẤP MÁY MÔI]:\n"
                f"- Hồ sơ giọng đọc dẫn chuyện (Consistent Narrator Voice Profile): {narr_voice}.\n"
                f"- Âm thanh giọng đọc thuyết minh dẫn chuyện ngoài khung hình (Off-screen Narrator Voiceover Audio — CHỈ ĐỌC ĐÚNG NỘI DUNG TRONG NGOẶC KÉP, TUYỆT ĐỐI KHÔNG ĐỌC CHỮ 'Người dẫn chuyện' hay 'Dẫn chuyện'): \"{narr_text}\".\n"
                f"- YÊU CẦU ĐẶC BIỆT BẮT BUỘC VỀ DIỄN XUẤT: Đây là GIỌNG ĐỌC DẪN CHUYỆN NGOÀI HÌNH ẢNH (Off-screen voiceover narration). "
                f"TẤT CẢ NHÂN VẬT TRÊN MÀN ẢNH TUYỆT ĐỐI KHÔNG NÓI, KHÔNG MẤP MÁY MÔI, KHÔNG CỬ ĐỘNG KHẨU HÌNH, MIỆNG PHẢI NGẬM TỰ NHIÊN "
                f"(CRITICAL ACTING INSTRUCTION: The narration is off-screen. Speak ONLY the exact quoted narration text \"{narr_text}\" using the consistent narrator voice profile above, without saying any label like 'Narrator' or 'Người dẫn chuyện'. On-screen characters DO NOT SPEAK, DO NOT MOVE THEIR LIPS, MOUTHS MUST REMAIN NATURALLY CLOSED WITH ZERO LIP-SYNC. Characters only perform silent natural acting, physical movements, thoughtful facial reactions and peaceful expressions while the off-screen narrator speaks)."
            )
        else:
            clean_user_prompt = re.sub(r'(?i)off-screen\s+(?:narrator\s+)?voiceover(?:\s+narration)?[^.]*\.?', '', clean_user_prompt).strip()
            dialogue_part = (
                "\n[QUY TẮC ÂM THANH: KHÔNG ĐỌC DẪN CHUYỆN (NO NARRATION)]:\n"
                "- Cảnh quay này KHÔNG có giọng người dẫn chuyện hay thuyết minh ngoài khung hình. "
                "Chỉ sử dụng âm thanh môi trường, tiếng động tự nhiên và diễn xuất hình thể chân thực (Pure visual cinematic scene with natural ambient sound effects only, NO narrator voiceover, NO off-screen narration, characters keep mouths naturally closed)."
            )
    elif diag_clean:
        speaker_name, spoken_line = extract_speaker_and_spoken_text(diag_clean)
        spoken_only = spoken_line or diag_clean
        speaker_label = f"Nhân vật {speaker_name}" if speaker_name else "Nhân vật trong cảnh"
        speaker_en = speaker_name if speaker_name else "the speaking character"
        matched_voice = _lookup_character_voice(speaker_name, character_voices)
        voice_consistency_block = ""
        if matched_voice:
            voice_consistency_block = (
                f"- ĐỒNG BỘ GIỌNG NÓI NHÂN VẬT (MANDATORY VOICE IDENTITY FOR {speaker_en}): {matched_voice}. "
                f"(STRICTLY maintain this exact vocal timbre, age, gender, pitch, accent, and speaking cadence consistently across all scenes).\n"
            )
        elif character_voices:
            all_voices = "; ".join(f"{k}: {v}" for k, v in character_voices.items() if v)
            if all_voices:
                voice_consistency_block = f"- ĐỒNG BỘ GIỌNG NÓI CÁC NHÂN VẬT TRONG CẢNH: {all_voices}.\n"
        dialogue_part = (
            f"\n[LỜI THOẠI NHÂN VẬT & ĐỒNG BỘ GIỌNG NÓI / KHẨU HÌNH (VOICE & LIP-SYNC)]:\n"
            f"{voice_consistency_block}"
            f"- {speaker_label} nói câu thoại (CHỈ ĐỌC ĐÚNG LỜI THOẠI TRONG NGOẶC KÉP, KHÔNG ĐỌC TÊN NHÂN VẬT): \"{spoken_only}\".\n"
            f"- Yêu cầu diễn xuất & âm thanh: Chỉ {speaker_label} mới cử động khẩu hình môi và phát âm giọng nói đồng bộ "
            f"(Spoken character dialogue by {speaker_en} with natural synchronized lip-sync and clear audible voice, speaking ONLY these exact words without saying the character's name prefix: \"{spoken_only}\"). "
            f"Các nhân vật khác trong cảnh (nếu có) giữ miệng ngậm tự nhiên và im lặng lắng nghe (Other characters keep mouths naturally closed without speaking)."
        )

    ref_images_part = ""
    if has_last_frame:
        ref_images_part += (
            "\n[KHUNG HÌNH NỐI TIẾP TỪ CẢNH TRƯỚC - PREVIOUS SCENE LAST FRAME]:\n"
            "- ẢNH ĐẦU TIÊN đính kèm là KHUNG HÌNH CUỐI CÙNG của phân cảnh ngay trước đó. "
            "BẮT BUỘC mở đầu cảnh quay này nối tiếp mượt mà trực tiếp từ bố cục, vị trí nhân vật, ánh sáng, bối cảnh và chuyển động của khung hình cuối cùng đó "
            "(CRITICAL CONTINUATION: The FIRST attached image is the exact last frame of the previous scene. Start this clip smoothly continuing from that exact frame, matching lighting, environment, camera angle, and character pose).\n"
        )
    if has_reference_images:
        ref_images_part += (
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
            f"Chất lượng điện ảnh HD, chuyển động vật lý tự nhiên, chi tiết sắc nét, âm thanh sống động đồng bộ hoàn hảo, "
            f"{ratio_desc}.{style_part}{dialogue_part}{ref_images_part}\n"
            f"Diễn biến cảnh tiếp theo: {clean_user_prompt}"
        )
    else:
        return (
            f"Hãy tạo một video chuyên nghiệp, {duration_desc} "
            f"Chất lượng điện ảnh HD, bố cục nghệ thuật xuất sắc, "
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


async def _poll_for_image_refs(
    client: MuseClient,
    baseline_hist: dict,
    events: list,
    timeout: float = 90.0,
) -> list[dict]:
    """Poll history and stream events for newly returned image refs (including reused file refs in new messages)."""
    stream_images = [m for m in extract_all_media_refs(events) if m["type"] == "image"]
    if stream_images:
        return stream_images

    baseline_media = {m["identity"] for m in extract_all_media_refs(baseline_hist)}
    baseline_events = (baseline_hist.get("result") or {}).get("chat_events") or []
    baseline_msg_ids = {
        ev.get("message_id")
        for ev in baseline_events
        if isinstance(ev, dict) and ev.get("message_id")
    }

    extracted_ids = extract_session_ids(events)
    target_session_id = extracted_ids[-1] if extracted_ids else None

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        hist = await client.history(session_id=target_session_id, limit=50)
        chat_events = (hist.get("result") or {}).get("chat_events") or []
        new_events = [
            ev for ev in chat_events
            if isinstance(ev, dict) and ev.get("message_id") not in baseline_msg_ids
        ]
        if new_events:
            event_images = [m for m in extract_all_media_refs(new_events) if m["type"] == "image"]
            if event_images:
                return event_images

        media_list = extract_all_media_refs(hist)
        new_images = [
            m for m in media_list
            if m["type"] == "image" and m["identity"] not in baseline_media
        ]
        if new_images:
            return new_images

        await asyncio.sleep(2.5)
    return []


async def download_media_ref(client: MuseClient, ref: dict, dest_path: Path) -> bool:
    """Download a media reference reliably to dest_path, returning True if successful."""
    # 1. Primary & most reliable: download sandbox path directly via RPC
    if ref.get("path"):
        try:
            tmp_ext = Path(ref["path"]).suffix or ".webp"
            tmp_path = dest_path.with_suffix(tmp_ext)
            await client._fs().download(ref["path"], tmp_path)
            if tmp_path.exists() and tmp_path.stat().st_size > 0:
                try:
                    from PIL import Image
                    im = Image.open(tmp_path)
                    im.save(dest_path, "PNG")
                    if tmp_path != dest_path:
                        tmp_path.unlink(missing_ok=True)
                except Exception:
                    if tmp_path != dest_path:
                        shutil.move(str(tmp_path), str(dest_path))
                return True
        except Exception as exc:
            logger.warning(f"Failed download via client._fs().download: {exc}")

    # 2. Idea-cards media URL
    if ref.get("url") and "/idea-cards/media/" in ref["url"]:
        try:
            data = await client._fs().idea_media_from_url(ref["url"])
            if data and len(data) > 0:
                dest_path.write_bytes(data)
                return True
        except Exception as exc:
            logger.warning(f"Failed idea_media_from_url: {exc}")

    # 3. Media handle
    if ref.get("media_handle"):
        try:
            data = await client._fs().idea_media(ref["media_handle"])
            if data and len(data) > 0:
                dest_path.write_bytes(data)
                return True
        except Exception as exc:
            logger.warning(f"Failed idea_media: {exc}")

    # 4. Fallback: try public HTTP URL
    if ref.get("url") and ref["url"].startswith(("http://", "https://")):
        try:
            r = await client.auth.browser.get(ref["url"])
            if r.status_code < 400 and len(r.content) > 0:
                dest_path.write_bytes(r.content)
                return True
        except Exception:
            pass

    return False


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

        topup_balance = live_sub.get("topupBalance")
        topup_total = live_sub.get("topupTotal")
        if topup_balance is not None and topup_total and topup_total > 0:
            millions = round((topup_balance / topup_total) * 1000)
            vip_tokens_str = "1 tỷ" if millions >= 1000 else f"{millions:,} triệu".replace(",", ".")
        else:
            norm_label = raw_topup_label.replace('\xa0', ' ')
            m = re.search(r'còn\s+([\d.,]+\s*(?:triệu|tỷ|nghìn|k|m|b)?(?:\s*token)?)', norm_label, re.IGNORECASE)
            vip_tokens_str = m.group(1).replace('token', '').strip() if m else "982 triệu"

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
                "name": "Token VIP",
                "vip_tokens": vip_tokens_str,
                "used_percent": 0,
                "label": f"Token VIP: {vip_tokens_str}",
                "tokens_left": vip_tokens_str,
                "subtitle": "Không bao giờ hết hạn",
                "topup_balance": topup_balance or 0,
                "topup_total": topup_total or 0,
            },
            "cta_label": live_sub.get("entryPointCtaLabel") or "Nâng cấp",
            "is_live": True,
        }
    else:
        vip_tokens_str = "982 triệu"
        usage_data = {
            "tier_name": "H-AI Miễn phí",
            "free_plan": {
                "name": "Gói miễn phí",
                "used_percent": 26,
                "label": "Đã dùng 26%",
                "subtitle": "Giới hạn hàng tuần sẽ đặt lại vào 19 tháng 11",
            },
            "additional_tokens": {
                "name": "Token VIP",
                "vip_tokens": vip_tokens_str,
                "used_percent": 0,
                "label": f"Token VIP: {vip_tokens_str}",
                "tokens_left": vip_tokens_str,
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
            "vip_tokens": vip_tokens_str,
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

        # If continuing from a previous video, extract its last frame and prepend it as the primary visual anchor
        last_frame_path: Optional[Path] = None
        if data.previous_video_url:
            prev_video_path = resolve_media_path(data.previous_video_url)
            if prev_video_path and prev_video_path.exists():
                last_frame_path = await asyncio.to_thread(extract_last_frame_from_video, prev_video_path)
                if last_frame_path and last_frame_path.exists():
                    resolved_images = [last_frame_path] + [p for p in resolved_images if p != last_frame_path]

        # Cap reference images to at most 3 images to prevent Noise WebSocket payload overflow
        if len(resolved_images) > 3:
            resolved_images = resolved_images[:3]

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
            character_voices=data.character_voices,
            enable_narration=data.enable_narration,
            has_last_frame=bool(last_frame_path),
        )

        # Use existing session_id if continuing a video sequence
        target_session_id = data.session_id if (data.is_continuation and data.session_id) else None

        logger.info(
            f"[VideoGenerate] scene={data.scene_index} cont={data.is_continuation} "
            f"images={len(resolved_images)} prompt={data.prompt[:100]!r}"
        )

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

        events = await client.chat_stream(
            prompt=prompt_full,
            images=images,
            stream_timeout=35.0,
        )

        found_images = await _poll_for_image_refs(client, baseline_hist, events, timeout=90.0)

        downloaded_urls = []
        for index, ref in enumerate(found_images):
            dest_name = f"haiai_img_{int(time.time())}_{index+1}.png"
            dest_path = OUTPUTS_DIR / dest_name
            ok = await download_media_ref(client, ref, dest_path)
            if ok and dest_path.exists() and dest_path.stat().st_size > 0:
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


def _split_long_dialogue_into_chunks(dialogue: str, max_words: int = 24) -> list[str]:
    """Split a long dialogue (> max_words) into <= max_words chunks while preserving the speaker/narrator label."""
    raw = (dialogue or "").strip()
    if not raw:
        return []
    m_prefix = re.match(r'^([^:\n]{1,40}\s*:\s*)(.+)$', raw, flags=re.DOTALL)
    prefix = m_prefix.group(1) if m_prefix else ""
    body = (m_prefix.group(2) if m_prefix else raw).strip().strip('"').strip("'").strip()
    words = body.split()
    if len(words) <= max_words:
        return [raw]

    parts = [p.strip() for p in re.split(r'(?<=[.!?;,])\s+', body) if p.strip()]
    chunks: list[str] = []
    cur: list[str] = []
    cur_words = 0

    for part in parts:
        p_words = part.split()
        if len(p_words) > max_words:
            if cur:
                chunks.append(" ".join(cur))
                cur = []
                cur_words = 0
            for i in range(0, len(p_words), max_words):
                chunks.append(" ".join(p_words[i:i + max_words]))
        elif cur_words + len(p_words) > max_words and cur:
            chunks.append(" ".join(cur))
            cur = [part]
            cur_words = len(p_words)
        else:
            cur.append(part)
            cur_words += len(p_words)

    if cur:
        chunks.append(" ".join(cur))

    return [f'{prefix}"{c}"' if prefix else c for c in chunks]


def _format_movie_result(parsed: dict, style_preset: str = "Điện ảnh HD Hollywood", enable_narration: bool = False) -> dict:
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
        c_voice = (char.get("voice_prompt") or "").strip() or (
            f"Giọng nói đặc trưng phù hợp với {c_name} ({c_desc[:70]}), giữ nguyên âm sắc, cao độ, độ tuổi và nhịp nói đồng nhất qua mọi phân cảnh."
        )
        c_images = char.get("images") if isinstance(char.get("images"), list) else []
        formatted_chars.append({
            "id": c_id,
            "name": c_name,
            "description": c_desc,
            "voice_prompt": c_voice,
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
            elif dur > 10:
                dur = 10
        except (ValueError, TypeError):
            dur = 8

        diag = (sc.get("dialogue") or "").strip()
        diag_chunks = _split_long_dialogue_into_chunks(diag, max_words=24) if diag else [""]
        base_prompt = clean_narration_prefixes_in_prompt(
            (sc.get("prompt") or f"Cinematic HD Hollywood, {style_preset}, Scene {idx+1}: {sc.get('situation', '')}. photorealistic lighting, cinematic camera motion").strip()
        )
        # Strip any embedded spoken/voiceover text if we are splitting into multiple chunks so each chunk gets its own exact audio
        if len(diag_chunks) > 1:
            base_prompt = re.sub(r'(?i)(?:off-screen\s+(?:narrator\s+)?voiceover(?:\s+narration)?|spoken\s+(?:character\s+)?dialogue)[^.]*\.?', '', base_prompt).strip()

        for chunk_idx, chunk_diag in enumerate(diag_chunks):
            scene_num = len(formatted_scenes) + 1
            cinematic_prompt = base_prompt
            is_narr = is_narration_dialogue(chunk_diag) or is_narration_dialogue(cinematic_prompt)

            if is_narr:
                cinematic_prompt = re.sub(r'(?i)spoken dialogue[^.]*\.?', '', cinematic_prompt).strip()
                cinematic_prompt = re.sub(r'(?i)with (clear |natural )?synchronized lip movements?[^.]*\.?', '', cinematic_prompt).strip()
                cinematic_prompt = re.sub(r'(?i)lip-?sync[^.]*\.?', '', cinematic_prompt).strip()

                if enable_narration:
                    if "off-screen" not in cinematic_prompt.lower() and "narrator" not in cinematic_prompt.lower():
                        _, narr_spoken = extract_speaker_and_spoken_text(chunk_diag)
                        narr_text = narr_spoken if narr_spoken else "Thuyết minh dẫn chuyện"
                        cinematic_prompt += (
                            f' Off-screen voiceover narration (speak ONLY these exact words, do NOT say "Người dẫn chuyện"): "{narr_text}". '
                            f'Critical acting constraint: The narration is off-screen. On-screen characters DO NOT SPEAK, DO NOT MOVE LIPS, mouths remain naturally closed with zero lip-sync, characters perform silent expressive acting only.'
                        )
                else:
                    cinematic_prompt = re.sub(r'(?i)off-screen\s+(?:narrator\s+)?voiceover(?:\s+narration)?[^.]*\.?', '', cinematic_prompt).strip()
                    chunk_diag = ""
            else:
                if chunk_diag and "spoken dialogue" not in cinematic_prompt.lower() and "dialogue:" not in cinematic_prompt.lower():
                    _, spoken_line = extract_speaker_and_spoken_text(chunk_diag)
                    cinematic_prompt += f' Spoken character dialogue: "{spoken_line or chunk_diag}" with natural synchronized lip movement and clear audible voice.'

            part_suffix = f" (Phần {chunk_idx + 1}/{len(diag_chunks)})" if len(diag_chunks) > 1 else ""
            formatted_scenes.append({
                "scene_index": scene_num,
                "situation": f"{sc.get('situation') or f'Diễn biến phân cảnh {scene_num}'}{part_suffix}",
                "dialogue": chunk_diag,
                "duration": min(10, max(5, dur)),
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


def _fallback_movie_breakdown(script: str, style_preset: str = "Điện ảnh HD Hollywood", target_scenes: int | str = "auto") -> dict:
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


def _try_parse_movie_json_from_text(text: str) -> Optional[dict]:
    """Extract and parse a movie breakdown JSON object containing characters/scenes from assistant text."""
    if not text or not text.strip():
        return None
    if "{" not in text or "}" not in text:
        return None
    if '"characters"' not in text and '"scenes"' not in text:
        return None
    clean_text = re.sub(r'^```(?:json)?\s*', '', text.strip(), flags=re.MULTILINE)
    clean_text = re.sub(r'```\s*$', '', clean_text, flags=re.MULTILINE).strip()
    m = re.search(r'\{[\s\S]*\}', clean_text)
    if not m:
        return None
    try:
        candidate = json.loads(m.group(0))
        if isinstance(candidate, dict) and ("characters" in candidate or "scenes" in candidate):
            return candidate
    except Exception:
        pass
    return None


@app.post("/api/movie/breakdown")
async def breakdown_movie(data: MovieBreakdownModel):
    script = data.script.strip()
    if not script:
        raise HTTPException(status_code=400, detail="Vui lòng nhập kịch bản hoặc ý tưởng phim.")

    style_preset = data.style_preset or "Điện ảnh HD Hollywood"

    # 1. Direct JSON detection: If user or upstream passed a JSON structure
    try:
        if (script.startswith("{") and script.endswith("}")) or ('"characters"' in script and '"scenes"' in script):
            match = re.search(r'\{[\s\S]*\}', script)
            if match:
                direct_parsed = json.loads(match.group(0))
                if isinstance(direct_parsed, dict) and ("characters" in direct_parsed or "scenes" in direct_parsed):
                    formatted = _format_movie_result(direct_parsed, style_preset, enable_narration=data.enable_narration)
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

        narration_rule = (
            "   - ĐƯỢC PHÉP tạo cảnh Người dẫn chuyện (Voice-over) nếu cần thiết cho mạch truyện. NẾU LÀ CẢNH DẪN CHUYỆN / THUYẾT MINH (dialogue bắt đầu bằng 'Dẫn chuyện:', 'Người dẫn chuyện:', 'Thuyết minh:'):\n"
            "     'prompt' BẮT BUỘC PHẢI DÙNG CẤU TRÚC: Off-screen narrator voiceover: \"...\". Critical acting constraint: The narrator is off-screen. Characters on screen DO NOT SPEAK, DO NOT MOVE THEIR LIPS, zero lip-sync, mouths remain naturally closed, characters only perform silent natural acting, physical movements and facial reactions.\n"
            "     TUYỆT ĐỐI KHÔNG DÙNG từ 'spoken dialogue' hay 'lip-sync' cho cảnh dẫn chuyện để tránh nhân vật bị mấp máy môi!"
            if data.enable_narration
            else "   - TUYỆT ĐỐI KHÔNG TẠO PHÂN CẢNH DẪN CHUYỆN / THUYẾT MINH (NO NARRATOR / NO VOICEOVER): Không dùng 'Người dẫn chuyện:', 'Dẫn chuyện:', hay 'Thuyết minh:'. Nếu là cảnh mở đầu hoặc chuyển cảnh không có nhân vật nói, hãy để 'dialogue' rỗng (\"\") và chỉ mô tả hình ảnh điện ảnh + âm thanh môi trường tự nhiên (no voiceover, no narrator)!"
        )

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
2. THỜI LƯỢNG MỖI PHÂN CẢNH (TỐI ĐA 10 GIÂY - BẮT BUỘC CHIA NHỎ CÂU THOẠI DÀI):
   - Mỗi phân cảnh chỉ được phép tối đa 10 giây (chỉ chọn: 5, 6, 8 hoặc 10 giây). TUYỆT ĐỐI KHÔNG tạo cảnh > 10 giây.
   - Câu thoại ngắn: 5 - 6 giây. Câu thoại vừa: 8 - 10 giây.
   - Nếu có câu thoại hoặc đoạn dẫn chuyện dài (quá ~22-25 từ, đọc mất hơn 10 giây), BẮT BUỘC chia nhỏ thành nhiều phân cảnh nối tiếp nhau (mỗi cảnh <= 10 giây) để giọng đọc tự nhiên, không bị gấp hay cắt chữ!
3. QUY ĐỊNH VỀ LỜI DẪN CHUYỆN VS THOẠI NHÂN VẬT TRONG 'prompt':
{narration_rule}
   - NẾU LÀ CẢNH NHÂN VẬT NÓI (dialogue của nhân vật):
     'prompt' DÙNG CẤU TRÚC: Spoken character dialogue: "..." with natural synchronized lip movements, audible voice and matching facial expressions. Other characters in the scene keep mouths closed and listen silently.
4. TRÍCH XUẤT NHÂN VẬT & HỒ SƠ GIỌNG NÓI ĐỒNG BỘ:
   - Trích xuất TẤT CẢ các nhân vật có trong câu chuyện. Với mỗi nhân vật, mô tả chi tiết ngoại hình, độ tuổi, trang phục để tạo ảnh tham chiếu.
   - BẮT BUỘC tạo thêm trường "voice_prompt" cho MỖI nhân vật (mô tả cụ thể giới tính, độ tuổi giọng, âm sắc trầm/bổng, nhịp nói, cảm xúc đặc trưng bằng cả tiếng Việt và tiếng Anh ngắn gọn) để giọng nói của nhân vật đó đồng nhất 100% qua mọi phân cảnh!

ĐỊNH DẠNG ĐẦU RA BẮT BUỘC: DUY NHẤT một khối JSON hợp lệ (không kèm lời chào hay giải thích ngoài JSON):
{{
  "title": "Tên bộ phim",
  "logline": "Tóm tắt 1 câu cốt truyện",
  "characters": [
    {{
      "id": "char_1",
      "name": "Tên nhân vật",
      "description": "Mô tả chi tiết ngoại hình, trang phục",
      "voice_prompt": "Giọng nam/nữ..., độ tuổi..., âm sắc..., nhịp nói... (English voice profile)",
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
            stream_timeout=45.0,
        )

        session_id = None
        extracted_ids = extract_session_ids(events)
        if extracted_ids:
            session_id = extracted_ids[-1]

        parsed_result = None
        # Check if stream events already contain the complete JSON response
        stream_chunks: list[str] = []
        for ev in events:
            if not isinstance(ev, dict):
                continue
            txt = ev.get("display_text") or (ev.get("payload") or {}).get("content") or ev.get("delta") or ev.get("text") or ""
            if isinstance(txt, str) and txt:
                stream_chunks.append(txt)
                parsed_result = _try_parse_movie_json_from_text(txt)
                if parsed_result:
                    break
        if not parsed_result and stream_chunks:
            parsed_result = _try_parse_movie_json_from_text("".join(stream_chunks))

        deadline = time.monotonic() + 150.0
        max_deadline = time.monotonic() + 210.0
        while not parsed_result and time.monotonic() < deadline:
            hist = await client.history(session_id=session_id, limit=30)
            chat_events = (hist.get("result") or {}).get("chat_events") or []
            for ev in reversed(chat_events):
                if not isinstance(ev, dict):
                    continue
                msg_id = ev.get("message_id", "")
                if msg_id in baseline_ids:
                    continue
                if ev.get("event_name") == "message.assistant":
                    text = ev.get("display_text") or (ev.get("payload") or {}).get("content") or ""
                    parsed_result = _try_parse_movie_json_from_text(text)
                    if parsed_result:
                        break
                    # If MuseAI is actively streaming partial JSON, give it extra grace time
                    if text.strip() and "{" in text and ('"characters"' in text or '"scenes"' in text):
                        if deadline - time.monotonic() < 15.0 and deadline < max_deadline:
                            deadline = min(max_deadline, time.monotonic() + 30.0)
            if parsed_result:
                break
            await asyncio.sleep(1.5)

        if parsed_result:
            formatted = _format_movie_result(parsed_result, style_preset, enable_narration=data.enable_narration)
            return {
                "status": "success",
                "data": formatted,
                "source": "ai_director"
            }

        raise HTTPException(
            status_code=504,
            detail="MuseAI chưa trả về kịch bản JSON hoàn chỉnh trong thời gian chờ (150s). Vui lòng kiểm tra trạng thái MuseAI và bấm Phân Tích Kịch Bản lại."
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(f"AI director breakdown error: {exc}")
        raise HTTPException(
            status_code=500,
            detail=f"Lỗi phân tích kịch bản từ MuseAI: {exc}"
        )


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

        events = await client.chat_stream(
            prompt=prompt_full,
            images=[],
            stream_timeout=35.0,
        )

        found_images = await _poll_for_image_refs(client, baseline_hist, events, timeout=90.0)

        if not found_images:
            raise HTTPException(status_code=504, detail="H-AI chưa trả về ảnh trong thời gian quy định.")

        ref = found_images[0]
        dest_name = f"char_{slugify_simple(data.character_name)}_{data.angle_index}_{int(time.time())}.png"
        dest_path = OUTPUTS_DIR / dest_name

        ok = await download_media_ref(client, ref, dest_path)
        if not ok or not dest_path.exists() or dest_path.stat().st_size == 0:
            raise HTTPException(status_code=500, detail="Không thể tải file ảnh từ máy chủ AI về máy cục bộ.")

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
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi tạo ảnh nhân vật: {exc}")


@app.get("/api/movie/existing_character_images")
async def get_existing_character_images(name: str):
    slug = slugify_simple(name)
    slug_dash = slug.replace("_", "-")
    found = []
    if OUTPUTS_DIR.exists():
        for file in sorted(OUTPUTS_DIR.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True):
            if file.is_file() and file.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                fname_lower = file.name.lower()
                # Exclude video frames or full movies
                if fname_lower.startswith("haiai_movie") or "fullmovie" in fname_lower:
                    continue
                # Exact word-boundary awareness (e.g. don't mix "nguoi_em" with "vo_nguoi_em")
                if slug == "nguoi_em" and ("vo_nguoi_em" in fname_lower or "vo-nguoi-em" in fname_lower):
                    continue
                if slug in fname_lower or (len(slug) > 3 and slug_dash in fname_lower):
                    found.append({
                        "id": f"img_{file.stem}",
                        "name": file.name,
                        "url": f"/outputs/{file.name}",
                        "local_path": str(file.resolve()),
                    })
    return {"status": "success", "images": found[:6]}


# ==================== MOVIE PROJECTS PERSISTENCE API ====================

class MovieProjectModel(BaseModel):
    id: Optional[str] = None
    title: Optional[str] = "Kịch bản Phim Mới"
    logline: Optional[str] = ""
    script: Optional[str] = ""
    stylePreset: Optional[str] = "Điện ảnh HD Hollywood"
    aspectRatio: Optional[str] = "16:9"
    targetScenes: Optional[Any] = "auto"
    characters: Optional[list] = []
    scenes: Optional[list] = []
    sessionId: Optional[str] = None
    createdAt: Optional[float] = None
    updatedAt: Optional[float] = None


def _seed_initial_movie_projects():
    """Auto-discovers prior generated movies from outputs/ and seeds project JSON files."""
    try:
        MOVIES_DIR.mkdir(parents=True, exist_ok=True)
        now_ms = time.time() * 1000

        # 1. Co Be Ban Diem
        co_be_files = sorted(OUTPUTS_DIR.glob("*co-be-ban-diem*canh-*.mp4"))
        if co_be_files:
            scenes = []
            situations = [
                "Đêm giao thừa, tuyết rơi dày trên phố cổ châu Âu, cô bé bán diêm chân trần run rẩy ôm giỏ diêm đi giữa dòng người vội vã về nhà đón năm mới.",
                "Cô bé ngồi nép vào góc tường giữa hai ngôi nhà lạnh giá, run rẩy quẹt que diêm đầu tiên để sưởi ấm đôi tay tê cóng.",
                "Trong ánh lửa que diêm đầu tiên, cô bé nhìn thấy một lò sưởi bằng sắt rực sáng, tỏa hơi ấm ngập tràn.",
                "Em quẹt que diêm thứ hai, bức tường biến thành một tấm rèm trong suốt mở ra bàn tiệc thịnh soạn với đĩa ngỗng quay thơm phức.",
                "Em quẹt que diêm thứ ba, hiện ra một cây thông Noel khổng lồ lấp lánh ngàn ngọn nến sáng rực trên cành lá xanh tươi.",
                "Que diêm thứ tư bừng sáng, người bà ngoại hiền hậu hiện về mỉm cười dịu dàng ôm em vào lòng.",
                "Em vội quẹt hết cả bao diêm để giữ bà ở lại bên mình thật lâu trong đêm đông băng giá.",
                "Bà ngoại nắm tay cô bé bay lên cao, vượt qua mọi đói rét và đau khổ, cùng bay về chốn thiên đường an lành."
            ]
            for i, f in enumerate(co_be_files):
                idx = i + 1
                sit = situations[i] if i < len(situations) else f"Phân cảnh #{idx}"
                scenes.append({
                    "scene_index": idx,
                    "situation": sit,
                    "dialogue": f"Cô Bé Bán Diêm: 'Que diêm sưởi ấm...' (Cảnh #{idx})",
                    "duration": 10,
                    "characters_involved": ["Cô Bé Bán Diêm", "Bà Ngoại"] if idx >= 6 else ["Cô Bé Bán Diêm"],
                    "prompt": f"Cinematic 4K Hollywood film, scene {idx} of The Little Match Girl on snowy European street...",
                    "status": "done",
                    "video_url": f"/outputs/{f.name}"
                })
            
            p_cobe = {
                "id": "mov_co_be_ban_diem",
                "title": "Cô Bé Bán Diêm",
                "logline": "Trong đêm giao thừa tuyết rơi lạnh giá, một cô bé nghèo bán diêm thắp từng que diêm nhỏ để sưởi ấm, và trong ánh lửa mong manh ấy, em nhìn thấy bà ngoại yêu dấu đến đón em về thiên đường.",
                "script": "Đêm giao thừa, tuyết rơi lạnh buốt. Một cô bé đầu trần, chân đất co ro bước đi trên đường phố vắng vẻ...",
                "stylePreset": "Điện ảnh HD Hollywood",
                "aspectRatio": "16:9",
                "targetScenes": len(scenes),
                "characters": [
                    {
                        "id": "char_1",
                        "name": "Cô Bé Bán Diêm",
                        "description": "Bé gái khoảng 9 tuổi, tóc vàng hoe phủ tuyết, mặc áo rách nâu, ôm giỏ diêm.",
                        "color": "#ef4444",
                        "images": []
                    },
                    {
                        "id": "char_2",
                        "name": "Bà Ngoại",
                        "description": "Bà cụ khoảng 70 tuổi, tóc bạc trắng, nụ cười hiền hậu, ánh mắt ấm áp.",
                        "color": "#f59e0b",
                        "images": []
                    }
                ],
                "scenes": scenes,
                "createdAt": now_ms - 86400000,
                "updatedAt": now_ms - 3600000
            }
            with open(MOVIES_DIR / "mov_co_be_ban_diem.json", "w", encoding="utf-8") as f:
                json.dump(p_cobe, f, ensure_ascii=False, indent=2)

        # 2. Co Cong Mai Sat
        mai_sat_files = sorted(OUTPUTS_DIR.glob("*mai-sat-canh-*.mp4"))
        if mai_sat_files:
            m_scenes = []
            m_sits = [
                "Góc học tập nghèo, cậu bé 10 tuổi nhăn nhó gập sách, ngáp ngắn ngáp dài vì chán học.",
                "Bên bờ suối nhỏ, cậu bé thấy một bà cụ tóc bạc đang miệt mài mài một thỏi sắt lớn vào tảng đá.",
                "Cậu bé tò mò hỏi thăm, bà cụ mỉm cười dịu dàng giải thích bà đang mài thỏi sắt thành một chiếc kim khâu.",
                "Cậu bé hiểu ra bài học kiên trì, trở về nhà chăm chỉ đọc sách với nụ cười rạng rỡ."
            ]
            for i, f in enumerate(mai_sat_files):
                idx = i + 1
                sit = m_sits[i] if i < len(m_sits) else f"Phân cảnh #{idx}"
                m_scenes.append({
                    "scene_index": idx,
                    "situation": sit,
                    "dialogue": f"Lời thoại Cảnh #{idx}",
                    "duration": 10,
                    "characters_involved": ["Cậu Bé", "Bà Cụ"] if idx in (2, 3) else ["Cậu Bé"],
                    "prompt": f"Cinematic 4K Hollywood film, ancient Vietnam, scene {idx} of boy learning diligence...",
                    "status": "done",
                    "video_url": f"/outputs/{f.name}"
                })
            
            p_maisat = {
                "id": "mov_co_cong_mai_sat",
                "title": "Có Công Mài Sắt, Có Ngày Nên Kim",
                "logline": "Một cậu bé ham chơi lười học gặp bà cụ đang mài thỏi sắt lớn thành kim khâu, và từ bài học kiên trì ấy, cậu quyết tâm thay đổi để trở thành người chăm học.",
                "script": "Ngày xửa ngày xưa, có một cậu bé rất lười học. Một hôm cậu đi chơi gặp bà cụ mài sắt thành kim...",
                "stylePreset": "Điện ảnh HD Hollywood",
                "aspectRatio": "16:9",
                "targetScenes": len(m_scenes),
                "characters": [
                    {
                        "id": "char_1",
                        "name": "Cậu Bé",
                        "description": "Bé trai 10 tuổi, tóc đen ngắn rối, áo nâu vải thô, đôi mắt thông minh.",
                        "color": "#ef4444",
                        "images": []
                    },
                    {
                        "id": "char_2",
                        "name": "Bà Cụ",
                        "description": "Bà cụ khoảng 75 tuổi, lưng hơi còng, tóc búi sau gáy, áo dài nâu sẫm.",
                        "color": "#f59e0b",
                        "images": []
                    }
                ],
                "scenes": m_scenes,
                "createdAt": now_ms - 72000000,
                "updatedAt": now_ms - 1800000
            }
            with open(MOVIES_DIR / "mov_co_cong_mai_sat.json", "w", encoding="utf-8") as f:
                json.dump(p_maisat, f, ensure_ascii=False, indent=2)

        # 3. Chuyen Nha Thien
        thien_imgs = sorted(OUTPUTS_DIR.glob("char_*179455*.png"))
        if thien_imgs:
            char_map = {
                "Thiện": [f for f in thien_imgs if "thien" in f.name.lower() and "me_thien" not in f.name.lower()],
                "Ông Nội": [f for f in thien_imgs if "ong_noi" in f.name.lower()],
                "Mẹ Thiện": [f for f in thien_imgs if "me_thien" in f.name.lower()],
                "Bác Cả": [f for f in thien_imgs if "bac_ca" in f.name.lower() or "char_bac_1" in f.name.lower()],
                "Hải Lượng": [f for f in thien_imgs if "hai_luong" in f.name.lower()]
            }
            p_chars = []
            palette = ["#ef4444", "#06b6d4", "#10b981", "#f59e0b", "#8b5cf6"]
            for idx, (cname, files) in enumerate(char_map.items()):
                imgs = []
                for j, f in enumerate(files):
                    imgs.append({
                        "id": f"img_{f.stem}",
                        "name": f"Ảnh {j + 1}: Chân dung {cname}",
                        "url": f"/outputs/{f.name}",
                        "local_path": str(f.resolve())
                    })
                p_chars.append({
                    "id": f"char_{idx + 1}",
                    "name": cname,
                    "description": f"Nhân vật {cname} mang phong thái điện ảnh đặc trưng Điện ảnh HD Hollywood.",
                    "color": palette[idx % len(palette)],
                    "images": imgs
                })

            p_thien = {
                "id": "mov_chuyen_nha_thien",
                "title": "Chuyện Nhà Thiện",
                "logline": "Câu chuyện gia đình ấm áp giữa Thiện, Ông Nội, Mẹ Thiện, Bác Cả và Hải Lượng qua những biến cố thâm tình.",
                "script": "Kịch bản về gia đình của Thiện cùng Ông Nội, Bác Cả, Mẹ Thiện và Hải Lượng...",
                "stylePreset": "Điện ảnh HD Hollywood",
                "aspectRatio": "16:9",
                "targetScenes": 4,
                "characters": p_chars,
                "scenes": [
                    {
                        "scene_index": 1,
                        "situation": "Phòng khách ấm cúng của ngôi nhà gia đình, Thiện trò chuyện cùng Ông Nội và Mẹ Thiện.",
                        "dialogue": "Thiện: 'Con chào ông nội và mẹ, hôm nay nhà mình đông đủ quá ạ!'",
                        "duration": 8,
                        "characters_involved": ["Thiện", "Ông Nội", "Mẹ Thiện"],
                        "prompt": "Cinematic 4K Hollywood film, cozy living room in Vietnam, Thiện talks warmly with Grandfather and Mother...",
                        "status": "idle",
                        "video_url": None
                    }
                ],
                "createdAt": now_ms - 3600000,
                "updatedAt": now_ms
            }
            with open(MOVIES_DIR / "mov_chuyen_nha_thien.json", "w", encoding="utf-8") as f:
                json.dump(p_thien, f, ensure_ascii=False, indent=2)

        # 4. An Khe Tra Vang
        em_imgs = sorted(OUTPUTS_DIR.glob("char_*_rec.png"))
        if em_imgs:
            khe_chars = [
                {
                    "id": "char_1",
                    "name": "Người Em",
                    "description": "Người em hiền lành, áo vải nâu thô, chân thật, giàu tình cảm.",
                    "color": "#ef4444",
                    "images": [
                        {"id": f"img_{f.stem}", "name": f.name, "url": f"/outputs/{f.name}", "local_path": str(f.resolve())}
                        for f in em_imgs if "nguoi_em" in f.name and "vo_nguoi_em" not in f.name
                    ]
                },
                {
                    "id": "char_2",
                    "name": "Người Anh",
                    "description": "Người anh tham lam, áo gấm hoa lệ, vẻ mặt toan tính.",
                    "color": "#f59e0b",
                    "images": [
                        {"id": f"img_{f.stem}", "name": f.name, "url": f"/outputs/{f.name}", "local_path": str(f.resolve())}
                        for f in em_imgs if "nguoi_anh" in f.name
                    ]
                },
                {
                    "id": "char_3",
                    "name": "Vợ Người Em",
                    "description": "Vợ người em dịu dàng, đảm đang, nụ cười hiền hậu.",
                    "color": "#06b6d4",
                    "images": [
                        {"id": f"img_{f.stem}", "name": f.name, "url": f"/outputs/{f.name}", "local_path": str(f.resolve())}
                        for f in em_imgs if "vo_nguoi_em" in f.name
                    ]
                }
            ]
            p_khe = {
                "id": "mov_an_khe_tra_vang",
                "title": "Ăn Khế Trả Vàng",
                "logline": "Hai anh em mồ côi cha mẹ, người anh chia cho em cây khế cằn cỗi. Chim phượng hoàng đến ăn khế và bảo may túi ba gang mang đi mà đựng vàng.",
                "script": "Cây khế chín rộ, chim thần đáp xuống ăn khế và trả vàng...",
                "stylePreset": "Điện ảnh HD Hollywood",
                "aspectRatio": "16:9",
                "targetScenes": 4,
                "characters": khe_chars,
                "scenes": [
                    {
                        "scene_index": 1,
                        "situation": "Dưới gốc cây khế sai trĩu quả vàng, hai vợ chồng người em ngước nhìn chim phượng hoàng khổng lồ đáp xuống.",
                        "dialogue": "Người Em: 'Chim ơi khế này gia tài duy nhất của chúng tôi, chim ăn hết lấy gì sinh sống?'",
                        "duration": 10,
                        "characters_involved": ["Người Em", "Vợ Người Em"],
                        "prompt": "Cinematic 4K Hollywood film, ancient Vietnamese countryside, giant mythical phoenix bird perches on starfruit tree...",
                        "status": "idle",
                        "video_url": None
                    }
                ],
                "createdAt": now_ms - 10000000,
                "updatedAt": now_ms - 500000
            }
            with open(MOVIES_DIR / "mov_an_khe_tra_vang.json", "w", encoding="utf-8") as f:
                json.dump(p_khe, f, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.warning(f"Lỗi seed initial movie projects: {e}")


@app.get("/api/movie/projects")
async def list_movie_projects():
    MOVIES_DIR.mkdir(parents=True, exist_ok=True)
    existing_files = list(MOVIES_DIR.glob("*.json"))
    if not existing_files:
        _seed_initial_movie_projects()

    projects = []
    for file in sorted(MOVIES_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            with open(file, "r", encoding="utf-8") as f:
                data = json.load(f)

            chars = data.get("characters") or []
            scenes = data.get("scenes") or []
            rendered_count = sum(1 for s in scenes if s.get("video_url") or s.get("status") == "done")

            thumbnail_url = None
            for c in chars:
                imgs = c.get("images") or []
                if imgs and imgs[0].get("url"):
                    thumbnail_url = imgs[0].get("url")
                    break
            if not thumbnail_url:
                for s in scenes:
                    if s.get("video_url"):
                        thumbnail_url = s.get("video_url")
                        break

            projects.append({
                "id": data.get("id") or file.stem,
                "title": data.get("title") or "Kịch bản Phim Mới",
                "logline": data.get("logline") or "",
                "stylePreset": data.get("stylePreset") or "Điện ảnh HD Hollywood",
                "aspectRatio": data.get("aspectRatio") or "16:9",
                "targetScenes": data.get("targetScenes") or len(scenes),
                "character_count": len(chars),
                "characters_summary": [c.get("name") for c in chars if c.get("name")][:5],
                "scene_count": len(scenes),
                "rendered_count": rendered_count,
                "thumbnail_url": thumbnail_url,
                "createdAt": data.get("createdAt") or file.stat().st_ctime * 1000,
                "updatedAt": data.get("updatedAt") or file.stat().st_mtime * 1000,
            })
        except Exception as e:
            logger.warning(f"Lỗi đọc movie project {file.name}: {e}")

    return {"status": "success", "projects": projects}


@app.get("/api/movie/projects/{project_id}")
async def get_movie_project(project_id: str):
    file_path = MOVIES_DIR / f"{project_id}.json"
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Không tìm thấy dự án phim.")
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {"status": "success", "project": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi đọc dự án phim: {e}")


@app.post("/api/movie/projects")
async def save_movie_project(project: MovieProjectModel):
    data = project.dict()
    now_ms = time.time() * 1000

    pid = data.get("id")
    if not pid or not pid.strip():
        slug = slugify_simple(data.get("title") or "phim")[:20]
        pid = f"mov_{slug}_{int(time.time())}"
        data["id"] = pid
        data["createdAt"] = now_ms

    if not data.get("createdAt"):
        data["createdAt"] = now_ms
    data["updatedAt"] = now_ms

    file_path = MOVIES_DIR / f"{pid}.json"
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return {"status": "success", "project": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi lưu dự án phim: {e}")


@app.delete("/api/movie/projects/{project_id}")
async def delete_movie_project(project_id: str):
    file_path = MOVIES_DIR / f"{project_id}.json"
    if file_path.exists():
        try:
            file_path.unlink()
            return {"status": "success", "deleted_id": project_id}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Lỗi xoá dự án phim: {e}")
    return {"status": "success", "deleted_id": project_id}


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


CHARACTER_LIBRARY_FILE = STATE_DIR / "character_library.json"


def _load_character_library() -> list[dict]:
    if CHARACTER_LIBRARY_FILE.exists():
        try:
            data = json.loads(CHARACTER_LIBRARY_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception as exc:
            logger.warning(f"Failed to read character_library.json: {exc}")
    return []


def _save_character_library(items: list[dict]) -> list[dict]:
    CHARACTER_LIBRARY_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    return items


class CharacterLibraryUpsertModel(BaseModel):
    id: str = ""
    source_key: str = ""
    display_name: str = ""
    name: str = ""
    movie_title: str = ""
    description: str = ""
    voice_prompt: str = ""
    color: str = "#06b6d4"
    images: list[dict[str, Any]] = []
    custom_renamed: Optional[bool] = None


@app.get("/api/characters")
async def list_character_library():
    items = _load_character_library()
    return {"status": "success", "characters": items}


@app.post("/api/characters")
async def upsert_character_library_item(data: CharacterLibraryUpsertModel):
    items = _load_character_library()
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S")
    payload = data.model_dump()
    name = (payload.get("name") or "").strip()
    movie_title = (payload.get("movie_title") or "").strip()
    default_display = f"{name} - {movie_title}" if (name and movie_title) else (name or payload.get("display_name") or "Nhân vật mới")
    incoming_display = (payload.get("display_name") or "").strip() or default_display

    existing_idx = -1
    if payload.get("id"):
        existing_idx = next((i for i, x in enumerate(items) if x.get("id") == payload["id"]), -1)
    if existing_idx < 0 and payload.get("source_key"):
        existing_idx = next((i for i, x in enumerate(items) if x.get("source_key") == payload["source_key"]), -1)

    if existing_idx >= 0:
        cur = items[existing_idx]
        is_custom_renamed = bool(cur.get("custom_renamed"))
        if payload.get("custom_renamed") is True:
            is_custom_renamed = True
        cur["name"] = name or cur.get("name", "")
        cur["movie_title"] = movie_title or cur.get("movie_title", "")
        if not is_custom_renamed:
            cur["display_name"] = incoming_display
        elif payload.get("custom_renamed") is True and incoming_display:
            cur["display_name"] = incoming_display
        cur["custom_renamed"] = is_custom_renamed
        if payload.get("description") is not None:
            cur["description"] = payload["description"]
        if payload.get("voice_prompt") is not None:
            cur["voice_prompt"] = payload["voice_prompt"]
        if payload.get("color"):
            cur["color"] = payload["color"]
        if isinstance(payload.get("images"), list):
            cur["images"] = payload["images"]
        cur["updated_at"] = now_iso
        items[existing_idx] = cur
        _save_character_library(items)
        return {"status": "success", "character": cur, "created": False}

    new_item = {
        "id": payload.get("id") or f"libchar_{uuid.uuid4().hex[:10]}",
        "source_key": payload.get("source_key") or "",
        "display_name": incoming_display,
        "name": name,
        "movie_title": movie_title,
        "description": payload.get("description") or "",
        "voice_prompt": payload.get("voice_prompt") or "",
        "color": payload.get("color") or "#06b6d4",
        "images": payload.get("images") or [],
        "custom_renamed": bool(payload.get("custom_renamed")),
        "created_at": now_iso,
        "updated_at": now_iso,
    }
    items.insert(0, new_item)
    _save_character_library(items)
    return {"status": "success", "character": new_item, "created": True}


@app.put("/api/characters/{char_id}")
async def update_character_library_item(char_id: str, data: CharacterLibraryUpsertModel):
    items = _load_character_library()
    idx = next((i for i, x in enumerate(items) if x.get("id") == char_id), -1)
    if idx < 0:
        raise HTTPException(status_code=404, detail="Không tìm thấy nhân vật trong kho.")
    cur = items[idx]
    payload = data.model_dump()
    if payload.get("display_name") and payload["display_name"].strip() != cur.get("display_name"):
        cur["display_name"] = payload["display_name"].strip()
        cur["custom_renamed"] = True
    if payload.get("name") is not None:
        cur["name"] = payload["name"].strip()
    if payload.get("movie_title") is not None:
        cur["movie_title"] = payload["movie_title"].strip()
    cur["description"] = payload.get("description", cur.get("description", ""))
    cur["voice_prompt"] = payload.get("voice_prompt", cur.get("voice_prompt", ""))
    if payload.get("color"):
        cur["color"] = payload["color"]
    if isinstance(payload.get("images"), list):
        cur["images"] = payload["images"]
    cur["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    items[idx] = cur
    _save_character_library(items)
    return {"status": "success", "character": cur}


@app.delete("/api/characters/{char_id}")
async def delete_character_library_item(char_id: str):
    items = _load_character_library()
    new_items = [x for x in items if x.get("id") != char_id]
    if len(new_items) == len(items):
        raise HTTPException(status_code=404, detail="Không tìm thấy nhân vật trong kho.")
    _save_character_library(new_items)
    return {"status": "success"}


TELEGRAM_SETTINGS_FILE = STATE_DIR / "telegram_settings.json"


def _default_telegram_settings() -> dict:
    return {
        "bot_token": "",
        "recipients": [],
        "events": {
            "scene_done": True,
            "movie_done": True,
            "breakdown_done": False,
            "characters_done": False,
            "scene_error": True,
            "single_video_done": False,
            "image_done": False,
        },
    }


def _load_telegram_settings() -> dict:
    base = _default_telegram_settings()
    if TELEGRAM_SETTINGS_FILE.exists():
        try:
            saved = json.loads(TELEGRAM_SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                base["bot_token"] = str(saved.get("bot_token") or "").strip()
                recs = saved.get("recipients")
                if isinstance(recs, list):
                    base["recipients"] = [
                        {"id": str(r.get("id") or "").strip(), "note": str(r.get("note") or "").strip()}
                        for r in recs
                        if isinstance(r, dict) and str(r.get("id") or "").strip()
                    ]
                evs = saved.get("events")
                if isinstance(evs, dict):
                    for k in base["events"]:
                        if k in evs:
                            base["events"][k] = bool(evs[k])
        except Exception as exc:
            logger.warning(f"Failed to read telegram settings: {exc}")
    return base


def _save_telegram_settings(data: dict) -> dict:
    clean = _default_telegram_settings()
    clean["bot_token"] = str(data.get("bot_token") or "").strip()
    recs = data.get("recipients") or []
    clean["recipients"] = [
        {"id": str(r.get("id") or "").strip(), "note": str(r.get("note") or "").strip()}
        for r in recs
        if isinstance(r, dict) and str(r.get("id") or "").strip()
    ]
    evs = data.get("events") or {}
    if isinstance(evs, dict):
        for k in clean["events"]:
            if k in evs:
                clean["events"][k] = bool(evs[k])
    TELEGRAM_SETTINGS_FILE.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    return clean


async def _send_telegram_message(bot_token: str, chat_id: str, text: str) -> tuple[bool, str]:
    import httpx
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=15.0) as http_client:
            resp = await http_client.post(url, json={"chat_id": chat_id, "text": text})
            body = resp.json()
            if resp.status_code == 200 and body.get("ok"):
                return True, "OK"
            return False, body.get("description") or f"HTTP {resp.status_code}"
    except Exception as exc:
        return False, str(exc)


class TelegramRecipientModel(BaseModel):
    id: str = ""
    note: str = ""


class TelegramSettingsModel(BaseModel):
    bot_token: str = ""
    recipients: list[TelegramRecipientModel] = []
    events: dict[str, bool] = {}


class TelegramNotifyModel(BaseModel):
    event_key: str
    message: str


class TelegramBotInfoRequest(BaseModel):
    bot_token: str = ""


@app.post("/api/telegram/bot-info")
async def get_telegram_bot_info(data: Optional[TelegramBotInfoRequest] = None):
    token = (data.bot_token if data else "").strip()
    if not token:
        token = (_load_telegram_settings().get("bot_token") or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="Vui lòng nhập Bot Token Telegram.")

    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            body = resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Không thể kết nối Telegram API: {exc}")

    if not body.get("ok"):
        desc = body.get("description") or "Bot Token không hợp lệ."
        raise HTTPException(status_code=400, detail=desc)

    result = body.get("result") or {}
    username = result.get("username") or ""

    # Persist valid token immediately so the /start poller can respond right away
    current_cfg = _load_telegram_settings()
    if current_cfg.get("bot_token") != token:
        current_cfg["bot_token"] = token
        _save_telegram_settings(current_cfg)

    return {
        "status": "success",
        "bot": {
            "id": result.get("id"),
            "first_name": result.get("first_name") or "",
            "username": username,
            "link": f"https://t.me/{username}" if username else "",
        },
    }


@app.get("/api/telegram/settings")
async def get_telegram_settings():
    return {"status": "success", "settings": _load_telegram_settings()}


@app.post("/api/telegram/settings")
async def save_telegram_settings(data: TelegramSettingsModel):
    saved = _save_telegram_settings(data.model_dump())
    return {"status": "success", "settings": saved}


@app.post("/api/telegram/test")
async def test_telegram_notification(data: Optional[TelegramSettingsModel] = None):
    cfg = _save_telegram_settings(data.model_dump()) if data else _load_telegram_settings()
    bot_token = cfg.get("bot_token", "").strip()
    recipients = cfg.get("recipients") or []
    if not bot_token:
        raise HTTPException(status_code=400, detail="Vui lòng nhập Bot Token Telegram trước khi gửi thử.")
    if not recipients:
        raise HTTPException(status_code=400, detail="Vui lòng thêm ít nhất 1 Chat ID nhận thông báo.")

    test_text = "🎬 [H-AI Studio] Kết nối thông báo Telegram thành công!"
    results = []
    sent_count = 0
    for r in recipients:
        cid = r["id"]
        ok, err = await _send_telegram_message(bot_token, cid, test_text)
        if ok:
            sent_count += 1
        results.append({"id": cid, "note": r.get("note", ""), "ok": ok, "detail": err})

    if sent_count == 0:
        first_err = results[0]["detail"] if results else "Không thể gửi tin nhắn."
        raise HTTPException(status_code=400, detail=f"Gửi thử thất bại: {first_err}")

    return {"status": "success", "sent": sent_count, "total": len(recipients), "results": results}


@app.post("/api/telegram/notify")
async def trigger_telegram_notification(data: TelegramNotifyModel):
    cfg = _load_telegram_settings()
    bot_token = cfg.get("bot_token", "").strip()
    recipients = cfg.get("recipients") or []
    events = cfg.get("events") or {}

    if not bot_token or not recipients:
        return {"status": "skipped", "reason": "not_configured"}
    if not events.get(data.event_key, False):
        return {"status": "skipped", "reason": "event_disabled"}

    tasks = [_send_telegram_message(bot_token, r["id"], data.message) for r in recipients]
    outcomes = await asyncio.gather(*tasks, return_exceptions=True)
    sent = sum(1 for o in outcomes if isinstance(o, tuple) and o[0])
    return {"status": "success", "sent": sent, "total": len(recipients)}


_telegram_poller_task: Optional[asyncio.Task] = None


async def _telegram_start_poller():
    """Long-poll Telegram getUpdates so /start replies with a connected confirmation and Chat ID."""
    last_token = ""
    offset = 0
    while True:
        try:
            cfg = _load_telegram_settings()
            token = (cfg.get("bot_token") or "").strip()
            if not token:
                last_token = ""
                offset = 0
                await asyncio.sleep(3)
                continue

            if token != last_token:
                last_token = token
                offset = 0

            url = f"https://api.telegram.org/bot{token}/getUpdates"
            params = {
                "offset": offset,
                "timeout": 10,
                "allowed_updates": json.dumps(["message"]),
            }
            async with httpx.AsyncClient(timeout=18.0) as client:
                resp = await client.get(url, params=params)
                if resp.status_code != 200:
                    await asyncio.sleep(5)
                    continue
                data = resp.json()

            if not data.get("ok"):
                await asyncio.sleep(5)
                continue

            for upd in data.get("result") or []:
                uid = upd.get("update_id")
                if isinstance(uid, int) and uid >= offset:
                    offset = uid + 1
                msg = upd.get("message") or {}
                text = (msg.get("text") or "").strip()
                if text.startswith("/start"):
                    chat = msg.get("chat") or {}
                    chat_id = chat.get("id")
                    if chat_id is not None:
                        reply = (
                            "✅ Đã kết nối với H-AI Studio!\n"
                            f"🆔 Chat ID của bạn là: {chat_id}\n"
                            "Hãy copy ID này dán vào mục Danh sách ID cần thông báo trên web."
                        )
                        await _send_telegram_message(token, str(chat_id), reply)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.debug(f"Telegram poller warning: {exc}")
            await asyncio.sleep(4)


@app.on_event("startup")
async def start_background_tasks():
    global _telegram_poller_task
    if _telegram_poller_task is None or _telegram_poller_task.done():
        _telegram_poller_task = asyncio.create_task(_telegram_start_poller())


@app.on_event("shutdown")
async def stop_background_tasks():
    global _telegram_poller_task
    if _telegram_poller_task and not _telegram_poller_task.done():
        _telegram_poller_task.cancel()
        try:
            await _telegram_poller_task
        except asyncio.CancelledError:
            pass
        _telegram_poller_task = None


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
