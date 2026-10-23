"""Живая проверка: результат штатного Mask Editor виден в превью ноды.

Симптом, который ловим (жалоба 2026-10-22): «рисую в редакторе красной
кистью, сохраняю — на превью ничего не видно, а у стандартных нод видно».

Что проверяется:
  1. превью в режиме файла показывает выбранный файл;
  2. после записи значения редактора в служебный виджет `image` ссылка
     доезжает до входа `mask_file`;
  3. слой с файлом редактора РЕАЛЬНО показан (вычисленный `display`, не
     inline: inline `''` при правиле `display:none` даёт скрытый слой — на
     этом баг и жил, а прежняя проверка этого не видела);
  4. файл догрузился и источник под ним скрыт;
  5. по скриншоту: покраска (правая половина образца) ВИДНА, а выбитая
     маской половина (альфа 0) показывает заливку, а не исходник.

Образец создаётся пробой в папке `input` (α=0 слева, красная кисть справа)
и удаляется после прогона.

Запуск (ComfyUI должен быть запущен):
    cd Degg_Crop_Image
    python tests/_probe_live_mask_preview.py
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from _probe_live_dom_crop_image import (  # noqa: E402  (общая обвязка CDP)
    CDP,
    INSTALL_JS,
    find_chrome,
    wait_devtools,
    wait_for,
)

FAILS: list[str] = []
OKS = 0

INPUT_DIR = Path(r"D:\ComfyUI_windows_portable\ComfyUI\input")
SOURCE_FILE = "example.png"
FIXTURE = "clipspace-probe-mask-painted.png"
PAINTED_VALUE = f"{FIXTURE} [input]"
FILL_COLOR = (128, 128, 128)  # fill=gray → #808080 (см. layout() в crop_image.js)


def check(label: str, cond, detail="") -> None:
    global OKS
    if cond:
        OKS += 1
        print(f"  ok    {label}")
    else:
        FAILS.append(label)
        print(f"  FAIL  {label}" + (f"  [{detail}]" if detail else ""))


def make_fixture() -> None:
    """Образец «как отдаёт Mask Editor»: RGB = картинка+кисть, альфа = 1−маска."""
    from PIL import Image

    w, h = 300, 200
    im = Image.new("RGBA", (w, h), (255, 0, 255, 0))  # левая половина — выбита
    px = im.load()
    for y in range(h):
        for x in range(w // 2, w):
            px[x, y] = (255, 0, 0, 255)  # правая половина — красная кисть
    im.save(INPUT_DIR / FIXTURE)


SETUP_JS = r"""
(() => {
  app.graph.clear();
  const crop = LiteGraph.createNode('DeggCropImage');
  crop.pos = [120, 120];
  app.graph.add(crop);
  crop.setSize([640, 860]);
  const setW = (name, value) => {
    const w = (crop.widgets || []).find((x) => x.name === name);
    if (!w) return false;
    w.value = value;
    w.callback?.(value);
    return true;
  };
  setW('image_file', __SOURCE__);
  // Плоская серая заливка: под выбитыми маской пикселями должно быть видно
  // именно её, а не исходник.
  setW('fill', 'gray');
  window.__probeCrop = crop;
  window.__probeSet = setW;
  app.canvas.selectNodes([crop]);
  app.canvas.centerOnNode(crop);
  app.canvas.setZoom(1);
  app.canvas.setDirty(true, true);
  return { id: crop.id, size: crop.size.slice() };
})()
"""

PREVIEW_JS = r"""
(() => {
  const n = window.__probeCrop;
  const root = n && n.__deggCrop && n.__deggCrop.widget;
  if (!root) return { mounted: false };
  const stage = root.querySelector('.degg-crop__stage');
  const img = root.querySelector('.degg-crop__img');
  const layer = root.querySelector('.degg-crop__masklayer');
  const r = stage ? stage.getBoundingClientRect() : null;
  const ib = img ? img.getBoundingClientRect() : null;
  return {
    mounted: true,
    src: img ? (img.getAttribute('src') || '') : '',
    natural: img ? [img.naturalWidth, img.naturalHeight] : [0, 0],
    layerSrc: layer ? (layer.getAttribute('src') || '') : '',
    // ⛔ Вычисленный display: inline '' при правиле display:none = слой скрыт.
    layerDisplay: layer ? getComputedStyle(layer).display : '',
    layerInline: layer ? layer.style.display : '',
    layerNatural: layer ? [layer.naturalWidth, layer.naturalHeight] : [0, 0],
    imgVisibility: img ? getComputedStyle(img).visibility : '',
    maskFile: ((n.widgets || []).find((w) => w.name === 'mask_file') || {}).value || '',
    stage: r ? { x: r.x, y: r.y, w: r.width, h: r.height } : null,
    imgBox: ib ? { x: ib.x - (r ? r.x : 0), y: ib.y - (r ? r.y : 0), w: ib.width, h: ib.height } : null,
  };
})()
"""

SAVE_JS = r"""
(() => {
  const n = window.__probeCrop;
  const w = (n.widgets || []).find((x) => x.name === 'image');
  if (!w) return { ok: false, reason: 'no image widget' };
  // Ровно путь фронтенда: setNodeWidgetValue → widget.value + widget.callback.
  w.value = __VALUE__;
  w.callback?.(__VALUE__, app.canvas, n);
  return { ok: true, value: w.value };
})()
"""


async def grab(cdp: CDP, box: dict):
    from PIL import Image

    res = await cdp.call(
        "Page.captureScreenshot",
        {
            "format": "png",
            "clip": {
                "x": box["x"],
                "y": box["y"],
                "width": max(1, box["w"]),
                "height": max(1, box["h"]),
                "scale": 1,
            },
        },
    )
    data = base64.b64decode(res.get("data") or "")
    return Image.open(io.BytesIO(data)).convert("RGB")


def region_stats(shot, box: dict, x0: float, x1: float):
    """Сколько пикселей красные и сколько серых (заливка) в полосе картинки."""
    total = red = gray = 0
    left = int(box["w"] * x0) + int(box["x"])
    right = int(box["w"] * x1) + int(box["x"])
    top = int(box["y"]) + 6
    bottom = int(box["y"] + box["h"]) - 6
    for y in range(max(0, top), min(shot.height, bottom), 2):
        for x in range(max(0, left), min(shot.width, right), 2):
            r, g, b = shot.getpixel((x, y))
            total += 1
            if r > 170 and g < 90 and b < 90:
                red += 1
            elif abs(r - FILL_COLOR[0]) <= 14 and abs(g - FILL_COLOR[1]) <= 14 and abs(b - FILL_COLOR[2]) <= 14:
                gray += 1
    return {"total": total, "red": red, "gray": gray}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8188/")
    ap.add_argument("--port", type=int, default=9334)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    import websockets  # зависимость ComfyUI

    make_fixture()
    profile = Path(tempfile.mkdtemp(prefix="degg-crop-mask-"))
    chrome = subprocess.Popen(
        [
            find_chrome(),
            "--headless=new",
            f"--remote-debugging-port={args.port}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-size=1600,1100",
            "--disable-features=Translate",
            args.url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        page = wait_devtools(args.port)
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024) as ws:
            cdp = CDP(ws)
            await cdp.call("Runtime.enable")
            await cdp.call("Page.enable")
            print("→ ждём фронтенд…")
            await wait_for(cdp, "!!(window.app && window.app.isGraphReady)", 180, "isGraphReady")
            await cdp.eval(INSTALL_JS)
            setup = await cdp.eval(SETUP_JS.replace("__SOURCE__", json.dumps(SOURCE_FILE)))
            print(f"→ нода: {setup}")
            await asyncio.sleep(1.5)

            before = await cdp.eval(PREVIEW_JS)
            check("DOM-виджет смонтирован", bool(before.get("mounted")), before)
            check(
                "превью показывает файл источника",
                SOURCE_FILE in (before.get("src") or ""),
                before.get("src"),
            )
            shot_before = await grab(cdp, before["stage"])
            base = region_stats(shot_before, before["imgBox"], 0.55, 0.98)
            print(f"  · до покраски (правая половина): {base}")
            check("до покраски краски в превью нет", base["red"] < base["total"] * 0.02, base)

            saved = await cdp.eval(SAVE_JS.replace("__VALUE__", json.dumps(PAINTED_VALUE)))
            await asyncio.sleep(1.5)

            after = await cdp.eval(PREVIEW_JS)
            check("редактор записал значение в служебный виджет", bool((saved or {}).get("ok")), saved)
            check(
                "ссылка на файл редактора доехала до mask_file",
                PAINTED_VALUE == (after.get("maskFile") or ""),
                after.get("maskFile"),
            )
            check(
                "слой получил файл редактора",
                FIXTURE in (after.get("layerSrc") or ""),
                after.get("layerSrc"),
            )
            check(
                "слой показан (вычисленный display = block)",
                (after.get("layerDisplay") or "") == "block",
                f"computed={after.get('layerDisplay')!r} inline={after.get('layerInline')!r}",
            )
            check(
                "файл покраски догрузился в слой",
                int((after.get("layerNatural") or [0])[0]) > 0,
                after.get("layerNatural"),
            )
            check(
                "источник скрыт под слоем",
                (after.get("imgVisibility") or "") == "hidden",
                f"visibility={after.get('imgVisibility')!r}",
            )

            shot_after = await grab(cdp, after["stage"])
            right = region_stats(shot_after, after["imgBox"], 0.55, 0.98)
            left = region_stats(shot_after, after["imgBox"], 0.02, 0.45)
            print(f"  · после покраски, правая половина: {right}")
            print(f"  · после покраски, левая половина (выбита маской): {left}")
            check(
                "краска из редактора ВИДНА в превью (как у стандартных нод)",
                right["red"] > right["total"] * 0.5,
                right,
            )
            check(
                "выбитая маской половина показывает заливку, а не исходник",
                left["gray"] > left["total"] * 0.5,
                left,
            )

            errors = await cdp.eval("window.__probeErrors")
            real = [e for e in (errors or []) if "favicon" not in e and SOURCE_FILE not in e]
            check("консоль без ошибок", not real, real[:3])
    finally:
        try:
            (INPUT_DIR / FIXTURE).unlink()
        except Exception:
            pass
        chrome.terminate()
        try:
            chrome.wait(timeout=10)
        except Exception:
            chrome.kill()
        if not args.keep:
            import shutil

            shutil.rmtree(profile, ignore_errors=True)

    print()
    print(f"ok: {OKS}   FAIL: {len(FAILS)}")
    if FAILS:
        print("ПРОБА НЕ ПРОЙДЕНА")
        return 1
    print("ПРОБА ПРОЙДЕНА")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
