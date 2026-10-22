"""Проверка БЕЗ браузера (API): маска из файла, нарисованного в Mask Editor.

Сценарий: нода получает файл с нарисованной маской (вход mask_file) — это
ровно то, что кладёт фронтенд после штатного Mask Editor. Файл: PNG, у которого
левая половина прозрачная. Конвенция ComfyUI: маска = 1 там, где убрать, то
есть mask = 1 - альфа. Значит прозрачная половина маски должна убраться из
результата (альфа 0), а непрозрачная — остаться (альфа 255).

Запуск: python tests/_check_mask_file.py
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
INPUT_DIR = Path(r"D:\ComfyUI_windows_portable\ComfyUI\input")
TEMP_DIR = Path(r"D:\ComfyUI_windows_portable\ComfyUI\temp")
SOURCE = "image_flux3_klein_distilled_t2i_00023_.png"
MASK_FILE = "degg-mask-file-check.png"

# Сценарий 1: источник по КАБЕЛЮ (LoadImage → сокет image) + маска из файла.
PROMPT_SOCKET = {
    "1": {"class_type": "LoadImage", "inputs": {"image": SOURCE}},
    "2": {
        "class_type": "DeggCropImage",
        "inputs": {
            "image": ["1", 0],
            "mask_file": MASK_FILE,
            "megapixels": 2.0,
            "multiplicity": 8,
            "resample": "lanczos",
            "fill": "transparent",
            "dim_outside": 50,
        },
    },
    "3": {"class_type": "PreviewImage", "inputs": {"images": ["2", 0]}},
}

# Сценарий 2: источник из ФАЙЛА (image_file, без кабеля) + та же маска.
PROMPT_FILE = {
    "1": {
        "class_type": "DeggCropImage",
        "inputs": {
            "image_file": SOURCE,
            "mask_file": MASK_FILE,
            "megapixels": 2.0,
            "multiplicity": 8,
            "resample": "lanczos",
            "fill": "transparent",
            "dim_outside": 50,
        },
    },
    "2": {"class_type": "PreviewImage", "inputs": {"images": ["1", 0]}},
}


def make_mask_file() -> None:
    """PNG 64×64: левая половина прозрачная, правая — белая."""
    from PIL import Image

    image = Image.new("RGBA", (64, 64), (255, 255, 255, 255))
    for y in range(64):
        for x in range(32):
            image.putpixel((x, y), (255, 255, 255, 0))
    image.save(INPUT_DIR / MASK_FILE)


def alpha_columns(path: Path) -> tuple[int, int]:
    """Средняя альфа в левой и правой половинах."""
    from PIL import Image

    with Image.open(path) as handle:
        rgba = handle.convert("RGBA")
        width, height = rgba.size
        pixels = rgba.load()
        left = []
        right = []
        for y in range(0, height, max(1, height // 32)):
            for x in range(0, width, max(1, width // 32)):
                value = pixels[x, y][3]
                if x < width // 2:
                    left.append(value)
                else:
                    right.append(value)
    return int(sum(left) / max(1, len(left))), int(sum(right) / max(1, len(right)))


def run_prompt(prompt: dict) -> Path | None:
    """Ставит промпт в очередь и возвращает путь к файлу результата."""
    data = json.dumps({"prompt": prompt}).encode("utf-8")
    req = urllib.request.Request(
        SERVER + "/prompt", data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        prompt_id = json.load(resp)["prompt_id"]
    print("prompt_id:", prompt_id)

    deadline = time.time() + 240  # очередь может быть занята предыдущими прогонами
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
        return None

    images = []
    for node_out in (entry.get("outputs") or {}).values():
        images.extend(node_out.get("images") or [])
    if not images:
        print("РЕЗУЛЬТАТ НЕ ПОЛУЧЕН")
        return None

    for image in images:
        name = image.get("filename") or ""
        kind = image.get("type") or "output"
        sub = image.get("subfolder") or ""
        base = TEMP_DIR if kind == "temp" else Path(
            r"D:\ComfyUI_windows_portable\ComfyUI\output"
        )
        path = base / (sub or "") / name
        if path.exists():
            return path
    return None


def main() -> int:
    make_mask_file()
    failures = []
    try:
        for label, prompt in (
            ("источник по КАБЕЛЮ + маска из файла", PROMPT_SOCKET),
            ("источник из ФАЙЛА + маска из файла", PROMPT_FILE),
        ):
            print(f"\n— сценарий: {label}")
            path = run_prompt(prompt)
            if path is None:
                failures.append(f"{label}: результат не получен")
                continue
            left, right = alpha_columns(path)
            print(f"результат: {path.name} — альфа слева {left}, справа {right}")
            if left > 32:
                failures.append(f"{label}: прозрачная половина маски не убрана (альфа {left})")
            if right < 200:
                failures.append(f"{label}: видимая половина убрана (альфа {right})")

        if failures:
            for item in failures:
                print("FAIL:", item)
            return 1
        print("\nOK: маска из файла работает и с кабелем, и с файлом-источником")
        return 0
    finally:
        try:
            (INPUT_DIR / MASK_FILE).unlink()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
