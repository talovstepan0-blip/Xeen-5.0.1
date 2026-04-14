"""
agents/apollo.py — Агент Аполлон (видеомейкер). Сиен 3.0.0.

Изменения против 2.0 Beta:
  • Добавлен верхнеуровневый app = FastAPI() (раньше падал с "Attribute app not found").
  • Абсолютный путь VIDEO_DIR (корень/data/videos).
  • Субтитры через Whisper (модель tiny) — опциональный импорт.
  • Эмоциональный TTS через Piper (несколько голосов, выбор по типу видео).
  • JSON-шаблоны сцен (templates/apollo/<name>.json).
  • Health-эндпоинт.
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("apollo")
logging.basicConfig(level=logging.INFO, format="[АПОЛЛОН] %(message)s")

BASE_DIR = Path(__file__).resolve().parent.parent
VIDEO_DIR = BASE_DIR / "data" / "videos"
VIDEO_DIR.mkdir(parents=True, exist_ok=True)
SCENE_TEMPLATES_DIR = BASE_DIR / "templates" / "apollo"
SCENE_TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

router = APIRouter(prefix="/video", tags=["apollo"])

VideoType = Literal["shorts", "novel", "motivation", "custom"]

# ══════════════════════════════════════════════════════════════════
# Опциональные зависимости
# ══════════════════════════════════════════════════════════════════

try:
    import whisper  # type: ignore
    WHISPER_OK = True
except ImportError:
    WHISPER_OK = False

try:
    from moviepy.editor import (  # type: ignore
        VideoFileClip, TextClip, CompositeVideoClip, AudioFileClip,
        ImageSequenceClip, concatenate_videoclips,
    )
    MOVIEPY_OK = True
except ImportError:
    MOVIEPY_OK = False


# ══════════════════════════════════════════════════════════════════
# TTS (Piper с голосами в зависимости от типа)
# ══════════════════════════════════════════════════════════════════

# Голос подбирается под настроение видео
VOICE_MAP = {
    "shorts":     "ru_RU-dmitri-medium",     # энергичный
    "motivation": "ru_RU-ruslan-medium",     # тёплый/глубокий
    "novel":      "ru_RU-irina-medium",      # спокойный женский
    "custom":     "ru_RU-ruslan-medium",
}


def tts_piper(text: str, output_path: str, voice: str) -> bool:
    """Эмоциональный TTS через Piper. Fallback → пустой файл."""
    try:
        r = subprocess.run(
            ["piper", "--model", voice, "--output_file", output_path],
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=60,
        )
        if r.returncode == 0 and Path(output_path).exists():
            return True
        logger.warning(f"Piper exit={r.returncode}: {r.stderr[:120]}")
    except FileNotFoundError:
        logger.info("Piper не установлен — fallback")
    except Exception as e:
        logger.warning(f"Piper: {e}")

    # Fallback: pyttsx3 (оффлайн)
    try:
        import pyttsx3
        engine = pyttsx3.init()
        engine.setProperty("rate", 155)
        engine.save_to_file(text, output_path)
        engine.runAndWait()
        return True
    except Exception:
        pass

    Path(output_path).write_bytes(b"FAKE_AUDIO")
    return False


# ══════════════════════════════════════════════════════════════════
# Whisper субтитры
# ══════════════════════════════════════════════════════════════════

def transcribe_to_subtitles(audio_path: str) -> list[dict]:
    """Возвращает список сегментов [{start, end, text}] через Whisper tiny."""
    if not WHISPER_OK:
        logger.info("Whisper не установлен — субтитры пропущены")
        return []
    try:
        model = whisper.load_model("tiny")
        result = model.transcribe(audio_path, language="ru")
        return [
            {"start": s["start"], "end": s["end"], "text": s["text"].strip()}
            for s in result.get("segments", [])
        ]
    except Exception as e:
        logger.warning(f"Whisper: {e}")
        return []


# ══════════════════════════════════════════════════════════════════
# Сцены из JSON-шаблона
# ══════════════════════════════════════════════════════════════════

def load_scene_template(name: str) -> Optional[dict]:
    p = SCENE_TEMPLATES_DIR / f"{name}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Шаблон {name}: {e}")
        return None


def list_scene_templates() -> list[str]:
    return [p.stem for p in SCENE_TEMPLATES_DIR.glob("*.json")]


# ══════════════════════════════════════════════════════════════════
# Заглушки стоков и публикации
# ══════════════════════════════════════════════════════════════════

def fetch_stock_images(query: str, count: int = 5) -> list[str]:
    return [f"https://picsum.photos/seed/{query}{i}/1080/1920" for i in range(count)]


def fetch_stock_clips(query: str, count: int = 3) -> list[str]:
    return [f"stub_clip_{i}.mp4" for i in range(count)]


def publish_all(video_path: str, title: str, description: str) -> list[dict]:
    return [
        {"platform": "youtube",   "status": "stub_published", "video_id": "yt_fake_001"},
        {"platform": "tiktok",    "status": "stub_published", "item_id":  "tt_fake_001"},
        {"platform": "instagram", "status": "stub_published", "media_id": "ig_fake_001"},
        {"platform": "vk",        "status": "stub_published", "video_id": "vk_fake_001"},
    ]


# ══════════════════════════════════════════════════════════════════
# Сборка видео (заглушки с реальными Piper/Whisper хуками)
# ══════════════════════════════════════════════════════════════════

def _output_path(prefix: str) -> str:
    return str(VIDEO_DIR / f"{prefix}_{int(time.time())}.mp4")


def create_shorts_video(text: str, duration: int) -> tuple[str, list[dict]]:
    clips = fetch_stock_clips(text.split()[0] if text else "action", count=3)
    path = _output_path("shorts")
    logger.info(f"Shorts: {len(clips)} клипов → {path}")

    # TTS
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tts_piper(text, tmp.name, VOICE_MAP["shorts"])
        subs = transcribe_to_subtitles(tmp.name)

    Path(path).write_bytes(b"FAKE_VIDEO_MP4")
    return path, subs


def create_novel_video(text: str, duration: int) -> tuple[str, list[dict]]:
    path = _output_path("novel")
    images = fetch_stock_images("anime fantasy", count=max(duration // 3, 2))
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tts_piper(text[:500], tmp.name, VOICE_MAP["novel"])
        subs = transcribe_to_subtitles(tmp.name)
    logger.info(f"Novel: {len(images)} картинок + TTS → {path}")
    Path(path).write_bytes(b"FAKE_VIDEO_MP4")
    return path, subs


def create_motivation_video(text: str, duration: int) -> tuple[str, list[dict]]:
    path = _output_path("motivation")
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tts_piper(text, tmp.name, VOICE_MAP["motivation"])
        subs = transcribe_to_subtitles(tmp.name)
    logger.info(f"Motivation → {path}")
    Path(path).write_bytes(b"FAKE_VIDEO_MP4")
    return path, subs


def create_from_template(scene_name: str) -> tuple[str, list[dict]]:
    template = load_scene_template(scene_name)
    if template is None:
        raise HTTPException(404, f"Шаблон '{scene_name}' не найден")
    path = _output_path(f"tpl_{scene_name}")
    # Текст из шаблона
    narration = template.get("narration", "")
    voice = template.get("voice", VOICE_MAP["custom"])
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tts_piper(narration, tmp.name, voice)
        subs = transcribe_to_subtitles(tmp.name)
    logger.info(f"Template '{scene_name}' → {path}")
    Path(path).write_bytes(b"FAKE_VIDEO_MP4")
    return path, subs


# ══════════════════════════════════════════════════════════════════
# Эндпоинты
# ══════════════════════════════════════════════════════════════════

class VideoRequest(BaseModel):
    type: VideoType = "motivation"
    text: str = ""
    duration: int = 30
    scene_template: Optional[str] = None


class VideoResponse(BaseModel):
    video_path: str
    type: str
    subtitles: list[dict]
    publications: list[dict]


@router.post("/generate", response_model=VideoResponse)
async def generate_video(req: VideoRequest):
    if req.duration < 5 or req.duration > 300:
        raise HTTPException(400, "duration должен быть 5–300 секунд")

    logger.info(f"Запрос: type={req.type}, duration={req.duration}s")

    if req.type == "custom" and req.scene_template:
        path, subs = create_from_template(req.scene_template)
        title = f"Custom: {req.scene_template}"
    elif req.type == "shorts":
        path, subs = create_shorts_video(req.text, req.duration)
        title = f"Shorts: {req.text[:50]}"
    elif req.type == "novel":
        path, subs = create_novel_video(req.text, req.duration)
        title = f"Пересказ: {req.text[:50]}"
    else:
        path, subs = create_motivation_video(req.text, req.duration)
        title = f"Мотивация: {req.text[:50]}"

    publications = publish_all(path, title, req.text)
    return VideoResponse(video_path=path, type=req.type,
                         subtitles=subs, publications=publications)


@router.get("/list")
def list_videos():
    files = sorted(VIDEO_DIR.glob("*.mp4"), key=lambda f: f.stat().st_mtime, reverse=True)
    return {
        "videos": [{"path": str(f), "size": f.stat().st_size,
                    "modified": f.stat().st_mtime} for f in files],
        "count": len(files),
    }


@router.get("/templates")
def get_templates():
    return {"templates": list_scene_templates()}


@router.get("/health")
def health():
    return {
        "agent": "Аполлон",
        "alive": True,
        "whisper": WHISPER_OK,
        "moviepy": MOVIEPY_OK,
        "voice_map": VOICE_MAP,
        "templates_count": len(list_scene_templates()),
    }


# ══════════════════════════════════════════════════════════════════
# App
# ══════════════════════════════════════════════════════════════════

app = FastAPI(title="Аполлон — Видеомейкер")
app.include_router(router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8018)
