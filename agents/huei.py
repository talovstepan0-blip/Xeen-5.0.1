"""

# ── Сиен 3.0.0 patch: абсолютные пути ──
from pathlib import Path as _WenPath
_BASE_DIR = _WenPath(__file__).resolve().parent.parent
_DATA_DIR = _BASE_DIR / "data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)

Агент 'Хуэй' (Фото) — генерация изображений через Stable Diffusion.
Этап 4 проекта 'Сиен 01'.

Требует GPU (NVIDIA, ≥8GB VRAM) и пакет diffusers.
При отсутствии GPU возвращает заглушку с инструкцией.
"""

import os, logging, time
from pathlib import Path
from typing import Optional
import httpx
from fastapi import FastAPI, APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

logger = logging.getLogger("huei")
logging.basicConfig(level=logging.INFO, format="[ХУЭЙ] %(message)s")

router   = APIRouter(prefix="/photo", tags=["huei"])
OUT_DIR  = Path("data/huei/output")
OUT_DIR.mkdir(parents=True, exist_ok=True)

SD_MODEL = os.environ.get("SD_MODEL", "runwayml/stable-diffusion-v1-5")
COMFY_URL = os.environ.get("COMFY_URL", "")   # если используешь ComfyUI

# ── Проверка GPU и diffusers ──────────────────────────────────────────────────

def check_gpu() -> dict:
    """Проверяет наличие CUDA и достаточно ли VRAM."""
    try:
        import torch
        cuda_ok   = torch.cuda.is_available()
        vram_gb   = torch.cuda.get_device_properties(0).total_memory / 1e9 if cuda_ok else 0
        device    = torch.cuda.get_device_name(0) if cuda_ok else "CPU"
        return {"cuda": cuda_ok, "vram_gb": round(vram_gb, 1), "device": device, "sufficient": vram_gb >= 6}
    except ImportError:
        return {"cuda": False, "vram_gb": 0, "device": "unknown", "sufficient": False,
                "error": "torch не установлен. Установи: pip install torch torchvision"}

try:
    from diffusers import StableDiffusionPipeline, DPMSolverMultistepScheduler
    import torch
    _gpu = check_gpu()
    if _gpu["cuda"] and _gpu["sufficient"]:
        logger.info(f"Загружаю SD модель {SD_MODEL}...")
        _pipe = StableDiffusionPipeline.from_pretrained(
            SD_MODEL, torch_dtype=torch.float16, safety_checker=None
        ).to("cuda")
        _pipe.scheduler = DPMSolverMultistepScheduler.from_config(_pipe.scheduler.config)
        _pipe.enable_attention_slicing()
        DIFFUSERS_OK = True
        logger.info("Stable Diffusion загружен!")
    else:
        _pipe = None
        DIFFUSERS_OK = False
        logger.warning(f"GPU недостаточно: {_gpu}. Используется заглушка.")
except ImportError:
    _pipe = None
    DIFFUSERS_OK = False
    logger.warning("diffusers не установлен. Установи: pip install diffusers transformers accelerate")

# ── ComfyUI интеграция (альтернатива) ────────────────────────────────────────

async def generate_via_comfyui(prompt: str, negative: str, width: int, height: int, steps: int) -> Optional[str]:
    """
    Генерация через ComfyUI API (если запущен локально).
    Установи ComfyUI: https://github.com/comfyanonymous/ComfyUI
    Запусти: python main.py --listen
    Установи переменную: COMFY_URL=http://localhost:8188

    Документация API: http://localhost:8188/docs (после запуска ComfyUI)
    """
    if not COMFY_URL:
        return None
    try:
        workflow = {
            "3": {"inputs": {"text": prompt, "clip": ["4", 1]}, "class_type": "CLIPTextEncode"},
            "4": {"inputs": {"ckpt_name": "v1-5-pruned-emaonly.ckpt"}, "class_type": "CheckpointLoaderSimple"},
            "6": {"inputs": {"text": negative, "clip": ["4", 1]}, "class_type": "CLIPTextEncode"},
            "7": {"inputs": {"seed": int(time.time()), "steps": steps, "cfg": 7.5,
                             "sampler_name": "dpmpp_2m", "scheduler": "karras",
                             "denoise": 1.0, "model": ["4", 0], "positive": ["3", 0],
                             "negative": ["6", 0], "latent_image": ["5", 0]},
                  "class_type": "KSampler"},
            "5": {"inputs": {"width": width, "height": height, "batch_size": 1}, "class_type": "EmptyLatentImage"},
        }
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(f"{COMFY_URL}/prompt", json={"prompt": workflow})
            if r.status_code == 200:
                return "comfyui_ok"
    except Exception as e:
        logger.warning(f"ComfyUI недоступен: {e}")
    return None

# ── Модели ────────────────────────────────────────────────────────────────────

class GenerateReq(BaseModel):
    prompt: str
    negative_prompt: str = "blurry, low quality, watermark, text, ugly, deformed"
    width: int  = 512
    height: int = 512
    steps: int  = 20
    guidance_scale: float = 7.5
    seed: Optional[int] = None

# ── Эндпоинты ─────────────────────────────────────────────────────────────────

@router.post("/generate")
async def generate(req: GenerateReq):
    """Сгенерировать изображение по промпту."""
    gpu_info = check_gpu()

    if not DIFFUSERS_OK or not gpu_info["sufficient"]:
        # Пробуем ComfyUI
        comfy = await generate_via_comfyui(req.prompt, req.negative_prompt, req.width, req.height, req.steps)
        if comfy:
            return {"status": "comfyui", "message": "Запрос отправлен в ComfyUI"}

        return {
            "status": "gpu_required",
            "message": "Генерация изображений требует GPU (NVIDIA ≥ 6GB VRAM)",
            "gpu_info": gpu_info,
            "alternatives": [
                "Установи NVIDIA GPU с ≥6GB VRAM",
                "Используй облако: RunPod, Vast.ai, Google Colab",
                "Запусти ComfyUI и укажи COMFY_URL=http://localhost:8188",
                "Используй AUTOMATIC1111 WebUI API",
            ],
            "install": "pip install diffusers transformers accelerate torch torchvision --index-url https://download.pytorch.org/whl/cu118",
            "stub_image": "https://picsum.photos/512/512",  # заглушка-изображение
        }

    # Реальная генерация
    import torch
    generator = torch.Generator("cuda").manual_seed(req.seed or int(time.time()))
    filename  = f"img_{int(time.time())}.png"
    out_path  = OUT_DIR / filename

    logger.info(f"Генерация: '{req.prompt[:60]}' {req.width}x{req.height} {req.steps} шагов")
    result = _pipe(
        prompt=req.prompt,
        negative_prompt=req.negative_prompt,
        width=req.width, height=req.height,
        num_inference_steps=req.steps,
        guidance_scale=req.guidance_scale,
        generator=generator,
    )
    result.images[0].save(out_path)
    logger.info(f"Изображение сохранено: {out_path}")

    return {"status": "generated", "file": filename, "path": str(out_path), "url": f"/photo/result/{filename}"}

@router.get("/result/{filename}")
def get_image(filename: str):
    """Получить сгенерированное изображение."""
    path = OUT_DIR / filename
    if not path.exists():
        raise HTTPException(404, "Файл не найден")
    return FileResponse(path, media_type="image/png")

@router.get("/gpu")
def gpu_info():
    """Информация о GPU."""
    return check_gpu()

@router.get("/health")
def health():
    gpu = check_gpu()
    return {"agent": "Хуэй", "alive": True, "diffusers": DIFFUSERS_OK, "gpu": gpu["cuda"]}

app = FastAPI(title="Хуэй — Генерация изображений")
app.include_router(router)

if __name__ == "__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=8014)
