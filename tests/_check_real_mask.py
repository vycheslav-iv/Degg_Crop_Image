"""Реальная проверка на сервере: маска подключена к DeggCropImage.

Сценарий пользователя: LoadImage с нарисованной маской → маска подключена к
ноде кадрирования. Проверяем, что результат НЕ чёрный (то есть маска применена
по конвенции ComfyUI: 1 = убрать, видимая альфа = 1 - маска).

Запуск: python tests/_check_real_mask.py
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SERVER = "http://127.0.0.1:8188"
IMAGE = sys.argv[1] if len(sys.argv) > 1 else "image_flux3_klein_distilled_t2i_00023_.png"
CROP_RECT = {"x": 0.181794, "y": 0.183037, "w": 0.636412, "h": 0.633926}

PROMPT = {
    "1": {
        "class_type": "LoadImage",
        "inputs": {"image": IMAGE},
    },
    "2": {
        "class_type": "DeggCropImage",
        "inputs": {
            "image": ["1", 0],
            "mask": ["1", 1],
            "crop_rect": json.dumps(CROP_RECT),
            "fill": "transparent",
            # API требует все обязательные входы, включая «интерфейсные»
            # (center_x/y, frame_w/h, aspect_ratio, lock_ratio, output_size) —
            # они не влияют на результат, но должны присутствовать.
            "center_x": 0,
            "center_y": 0,
            "frame_w": 0,
            "frame_h": 0,
            "aspect_ratio": "free",
            "lock_ratio": True,
            "output_size": "",
            "megapixels": 2.0,
            "multiplicity": 8,
            "resample": "lanczos",
            "dim_outside": 50,
        },
    },
    "3": {
        "class_type": "PreviewImage",
        "inputs": {"images": ["2", 0]},
    },
}


def post(path: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        SERVER + path, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def get(path: str) -> dict:
    with urllib.request.urlopen(SERVER + path, timeout=30) as resp:
        return json.load(resp)


def main() -> int:
    try:
        queued = post("/prompt", {"prompt": PROMPT})
    except Exception as exc:
        print("не удалось поставить в очередь:", exc)
        return 2
    prompt_id = queued.get("prompt_id")
    print("prompt_id:", prompt_id)
    if not prompt_id:
        print("ответ сервера:", json.dumps(queued, ensure_ascii=False)[:400])
        return 2

    deadline = time.time() + 120
    history = {}
    while time.time() < deadline:
        time.sleep(2)
        history = get("/history/" + prompt_id).get(prompt_id) or {}
        if history.get("status", {}).get("completed") or history.get("status", {}).get("status_str") == "error":
            break

    status = history.get("status", {})
    if status.get("status_str") == "error":
        print("ОШИБКА:", json.dumps(status.get("messages", [])[-1:], ensure_ascii=False)[:600])
        return 1

    outputs = history.get("outputs", {})
    images = []
    for node_out in outputs.values():
        for img in node_out.get("images", []) or []:
            images.append(img)
    if not images:
        print("нет выходных картинок:", json.dumps(outputs, ensure_ascii=False)[:300])
        return 1

    img = images[0]
    url = f"{SERVER}/view?filename={img['filename']}&subfolder={img.get('subfolder', '')}&type={img.get('type', 'output')}"
    out = Path(__file__).resolve().parent / "_mask_check.png"
    with urllib.request.urlopen(url, timeout=60) as resp:
        out.write_bytes(resp.read())
    print("картинка сохранена:", out)
    print("размер воркфлоу:", img.get("width"), "x", img.get("height"))

    # Проверяем пиксели через PIL, если он есть; иначе — считаем вручную (PNG).
    try:
        from PIL import Image  # type: ignore

        im = Image.open(out).convert("RGBA")
        px = im.load()
        w, h = im.size
        samples = [
            (w // 2, h // 2),
            (int(w * 0.25), int(h * 0.5)),
            (int(w * 0.75), int(h * 0.5)),
        ]
        mean = sum(sum(px[x, y][:3]) for x, y in samples) / (len(samples) * 3)
        alpha = sum(px[x, y][3] for x, y in samples) / len(samples)
        print("средняя яркость в выборке:", round(mean, 1), "| средняя альфа:", round(alpha, 1))
        if mean < 8:
            print("СБОЙ: картинка чёрная — маска применена неверно")
            return 1
        print("OK: картинка не чёрная — маска обработана по конвенции ComfyUI")
        return 0
    except ImportError:
        print("PIL нет — смотрите картинку глазами:", out)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
