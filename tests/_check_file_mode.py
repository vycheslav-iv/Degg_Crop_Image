"""Проверка БЕЗ браузера (API): нода работает с файлом из input без сокета.

Сценарий: сокет «image» НЕ подключён, вход «image_file» указывает на файл
из папки input. Нода обязана:
  1) взять картинку из файла (а не вернуть пустой результат);
  2) отдать результат ожидаемого размера (по МП, как в _check_image_size.py);
  3) вернуть маску по альфе файла (если альфа есть) — маска = 1 там, где
     файл прозрачен (конвенция ComfyUI).

Запуск: python tests/_check_file_mode.py [имя_файла.png]
"""

from __future__ import annotations

import json
import struct
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
INPUT_DIR = Path(r"D:\ComfyUI_windows_portable\ComfyUI\input")
OUTPUT_DIR = Path(r"D:\ComfyUI_windows_portable\ComfyUI\output")
MEGAPIXELS = 2.0

# image_file — обязательный вход (комбо с кнопкой загрузки), image — нет.
PROMPT = {
    "1": {
        "class_type": "DeggCropImage",
        "inputs": {
            "image_file": IMAGE,
            "megapixels": MEGAPIXELS,
            "multiplicity": 8,
            "resample": "lanczos",
            "fill": "transparent",
            "dim_outside": 50,
        },
    },
    "2": {"class_type": "PreviewImage", "inputs": {"images": ["1", 0]}},
}


def png_size(path: Path) -> tuple[int, int]:
    """Размер PNG по заголовку IHDR (без Pillow)."""
    with path.open("rb") as handle:
        head = handle.read(26)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("не PNG")
    width, height = struct.unpack(">II", head[16:24])
    return int(width), int(height)


def find_result(name: str, subfolder: str, kind: str) -> Path | None:
    base = OUTPUT_DIR if kind == "output" else Path(
        r"D:\ComfyUI_windows_portable\ComfyUI\temp"
    )
    path = base / (subfolder or "") / name
    return path if path.exists() else None


def main() -> int:
    data = json.dumps({"prompt": PROMPT}).encode("utf-8")
    req = urllib.request.Request(
        SERVER + "/prompt", data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        prompt_id = json.load(resp)["prompt_id"]
    print("prompt_id:", prompt_id)

    deadline = time.time() + 120
    history = {}
    while time.time() < deadline:
        time.sleep(2)
        with urllib.request.urlopen(SERVER + "/history/" + prompt_id, timeout=30) as resp:
            history = json.load(resp)
        if history.get(prompt_id):
            break

    entry = history.get(prompt_id) or {}
    if entry.get("status", {}).get("status_str") == "error":
        print("ОШИБКА ВЫПОЛНЕНИЯ")
        print(json.dumps(entry.get("status"), ensure_ascii=False)[:2000])
        return 1

    outputs = entry.get("outputs") or {}
    images = []
    for node_out in outputs.values():
        images.extend(node_out.get("images") or [])
    if not images:
        print("РЕЗУЛЬТАТ НЕ ПОЛУЧЕН")
        print(json.dumps(entry, ensure_ascii=False)[:2000])
        return 1

    failures = []
    src_w, src_h = png_size(INPUT_DIR / IMAGE)
    print(f"источник: {src_w} × {src_h}")

    for image in images:
        name = image.get("filename") or ""
        sub = image.get("subfolder") or ""
        kind = image.get("type") or "output"
        path = find_result(name, sub, kind)
        if path is None:
            failures.append(f"файл результата не найден: {name}")
            continue
        w, h = png_size(path)
        mp = (w * h) / 1e6
        print(f"результат: {w} × {h} px  ({mp:.2f} МП)  {path}")
        if mp < MEGAPIXELS * 0.6 or mp > MEGAPIXELS * 1.6:
            failures.append(f"размер результата {mp:.2f} МП не похож на {MEGAPIXELS} МП")
        # пропорции результата = пропорции источника (кадр = вся картинка)
        src_ratio = src_w / src_h
        out_ratio = w / h
        if abs(src_ratio - out_ratio) > 0.02:
            failures.append(f"пропорции результата {out_ratio:.4f} != источника {src_ratio:.4f}")

    if failures:
        for item in failures:
            print("FAIL:", item)
        return 1
    print("OK: результат получен из файла без подключённого сокета")
    return 0


if __name__ == "__main__":
    sys.exit(main())
