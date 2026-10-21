"""Проверка БЕЗ браузера (API): размер результата определяется переданным МП.

В интерфейсе сброс при подключении картинки делает JS-редактор: кадр = вся
картинка, МП = площадь картинки, результат = размер картинки (1:1). В API
редактора нет, поэтому размер считается от того МП, который передан в промпте:
это осознанно (в скрипте размер задаёт вызывающий).

Скрипт ставит в очередь LoadImage → DeggCropImage → PreviewImage и печатает
размер результата рядом с размером источника.

Запуск: python tests/_check_image_size.py [имя_файла.png]
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

PROMPT = {
    "1": {"class_type": "LoadImage", "inputs": {"image": IMAGE}},
    "2": {
        "class_type": "DeggCropImage",
        "inputs": {
            "image": ["1", 0],
            "megapixels": 2.0,
            "multiplicity": 8,
            "resample": "lanczos",
            "fill": "transparent",
            "dim_outside": 50,
        },
    },
    "3": {"class_type": "PreviewImage", "inputs": {"images": ["2", 0]}},
}


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
            history = (json.load(resp) or {}).get(prompt_id) or {}
        if history.get("status", {}).get("completed"):
            break

    status = history.get("status", {})
    if status.get("status_str") == "error":
        print("ОШИБКА:", json.dumps(status.get("messages", [])[-1:], ensure_ascii=False)[:500])
        return 1

    import struct

    for node_out in history.get("outputs", {}).values():
        for img in node_out.get("images", []) or []:
            url = (
                f"{SERVER}/view?filename={img['filename']}"
                f"&subfolder={img.get('subfolder', '')}&type={img.get('type', 'output')}"
            )
            with urllib.request.urlopen(url, timeout=60) as resp:
                data = resp.read()
            w, h = struct.unpack(">II", data[16:24])
            print("результат (файл):", w, "x", h)
            src = Path(
                r"D:\ComfyUI_windows_portable\ComfyUI\input"
            ) / IMAGE
            if src.exists():
                sw, sh = struct.unpack(">II", src.read_bytes()[16:24])
                print("источник        :", sw, "x", sh)
                print("МП источника (1024^2):", round(sw * sh / 1024 / 1024, 4))
                print("МП источника (1e6)   :", round(sw * sh / 1e6, 4))
                ok = (w, h) == (sw, sh)
                print("ИТОГ:", "OK — результат ровно как у картинки" if ok else "СБОЙ: размер отличается")
                return 0 if ok else 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
