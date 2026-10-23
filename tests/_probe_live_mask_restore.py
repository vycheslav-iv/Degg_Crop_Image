"""Живая проверка: Mask Editor открывается с СОХРАНЁННОЙ маской и кистью.

Симптом (жалоба 2026-10-23): «рисую маску или кисть, сохраняю — всё вроде
работает; нажимаю кнопку маски снова — в редакторе ничего не сохранено».

Механика стандартной ноды (`useMaskEditorSaver.ts` → `useMaskEditorLoader.ts`):
савер пишет `clipspace-painted-masked-<ts>.png` и соседние слои
`clipspace-mask-<ts>.png` (RGB = картинка, альфа = 1 − маска) и
`clipspace-paint-<ts>.png` (штрихи кисти). Лоадер редактора берёт базу из
виджета `image`: увидев префикс `clipspace-painted-masked-`, он сам выводит
имена слоёв и грузит картинку через `channel=rgb`, маску через `channel=a`,
кисть — из paint-слоя.

Наша нода держала служебный виджет `image` пустым (картинка из провода) или
равным `image_file` (файл) → слои не находились, редактор грузил источник без
альфы, маска и кисть пустые.

Что проверяется (фаза 1 — файл редактора от ТЕКУЩЕЙ картинки):
  1. служебный виджет `image` = файл редактора (иначе слои не найти);
  2. редактор запросил файл слоя маски (`clipspace-mask-<ts>.png`);
  3. основа редактора — RGB слоя маски (маркерный цвет), а не источник;
  4. маска восстановлена (ненулевая альфа на канвасе маски);
  5. кисть восстановлена (красные пиксели на канвасе rgb).

Фаза 2 — файл редактора от ДРУГОЙ картинки (те же размеры, другой RGB):
  6. виджет `image` остаётся источником — сравниваются ПИКСЕЛИ, а не имена;
  7. редактор открыл источник, а не слой чужой картинки;
  8. маска чужой картинки не восстановлена (альфа канваса маски = 0).

Образцы создаются пробой в папке `input` и удаляются после прогона.

Запуск (ComfyUI должен быть запущен):
    cd Degg_Crop_Image
    python tests/_probe_live_mask_restore.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
import time
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
# Маркерный цвет RGB-слоя маски: по нему видно, что база редактора — файл
# слоя, а не источник (у источника такого цвета нет: замер = 0 пикселей).
MARKER = (0, 128, 255)


def check(label: str, cond, detail="") -> None:
    global OKS
    if cond:
        OKS += 1
        print(f"  ok    {label}")
    else:
        FAILS.append(label)
        print(f"  FAIL  {label}" + (f"  [{detail}]" if detail else ""))


def _layers(ts: int) -> tuple[str, str, str, str]:
    return (
        f"clipspace-painted-masked-{ts}.png",
        f"clipspace-mask-{ts}.png",
        f"clipspace-paint-{ts}.png",
        f"clipspace-painted-{ts}.png",
    )


def make_fixtures() -> tuple[str, str, list[Path]]:
    """Два набора файлов ровно так, как их пишет штатный савер.

    Набор 1 — по текущей картинке (RGB = источник). Набор 2 — от «другой»
    картинки тех же размеров: RGB маркерный везде, поэтому подстановка чужого
    файла видна по базе редактора. Альфа в обоих = 1 − маска (слева маска).
    """
    from PIL import Image

    src = Image.open(INPUT_DIR / SOURCE_FILE).convert("RGB")
    w, h = src.size

    alpha = Image.new("L", (w, h), 255)
    ap = alpha.load()
    for y in range(h):
        for x in range(w // 2):
            ap[x, y] = 0

    def write(ts: int, base_rgb: Image.Image, paint_red: bool) -> list[Path]:
        masked, mask_layer_name, paint_name, painted_name = _layers(ts)
        paths = [INPUT_DIR / masked, INPUT_DIR / mask_layer_name, INPUT_DIR / paint_name, INPUT_DIR / painted_name]

        mask_layer = Image.new("RGB", (w, h), MARKER)
        mask_layer.putalpha(alpha)
        mask_layer.save(paths[1])

        paint = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        if paint_red:
            pp = paint.load()
            for y in range(h // 4, 3 * h // 4):
                for x in range(w // 2, w):
                    pp[x, y] = (255, 0, 0, 255)
        paint.save(paths[2])

        painted = base_rgb.copy()
        if paint_red:
            painted.paste(Image.new("RGB", (w // 2, h // 2), (255, 0, 0)), (w // 2, h // 4))
        painted.save(paths[3])

        painted_masked = painted.copy()
        painted_masked.putalpha(alpha)
        painted_masked.save(paths[0])
        return paths

    ts = int(time.time() * 1000)
    own = write(ts, src, paint_red=True)
    foreign = write(ts + 1, Image.new("RGB", (w, h), MARKER), paint_red=False)
    return f"{own[0].name} [input]", f"{foreign[0].name} [input]", own + foreign


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
  // Ровно то, что записал бы савер редактора (он пишет widget.value + callback).
  const maskSet = setW('mask_file', __MASK__);
  window.__probeCrop = crop;
  app.canvas.selectNodes([crop]);
  app.canvas.centerOnNode(crop);
  return {
    maskSet,
    maskFile: ((crop.widgets || []).find((w) => w.name === 'mask_file') || {}).value || '',
    imageWidget: ((crop.widgets || []).find((w) => w.name === 'image') || {}).value ?? null,
  };
})()
"""

# Служебный виджет `image` — ровно то, что читает лоадер редактора.
WIDGET_JS = r"""
(() => {
  const w = (window.__probeCrop.widgets || []).find((x) => x.name === 'image');
  return (w && typeof w.value === 'string' && w.value) || '';
})()
"""

OPEN_JS = r"""
(() => {
  const crop = window.__probeCrop;
  app.canvas.selectNodes([crop]);
  app.extensionManager.command.execute('Comfy.MaskEditor.OpenMaskEditor');
  return true;
})()
"""

MEASURE_JS = r"""
(() => {
  const root = document.querySelector('.maskEditor-dialog-root');
  if (!root) return { open: false };
  const cs = root.querySelectorAll('#maskEditorCanvasContainer canvas');
  if (cs.length < 3) return { open: true, canvases: cs.length };
  const read = (c) => {
    if (!c || !c.width || !c.height) return null;
    const ctx = c.getContext('2d');
    if (!ctx) return null;
    const d = ctx.getImageData(0, 0, c.width, c.height).data;
    let alpha = 0, red = 0, marker = 0, total = 0;
    for (let i = 0; i < d.length; i += 4) {
      total++;
      if (d[i + 3] > 0) alpha++;
      if (d[i + 3] > 0 && d[i] > 150 && d[i + 1] < 100 && d[i + 2] < 100) red++;
      if (d[i + 3] > 0 && Math.abs(d[i] - __MR__) < 24 &&
          Math.abs(d[i + 1] - __MG__) < 24 && Math.abs(d[i + 2] - __MB__) < 24) marker++;
    }
    return { w: c.width, h: c.height, total, alpha, red, marker };
  };
  return {
    open: true,
    canvases: cs.length,
    img: read(cs[0]),
    rgb: read(cs[1]),
    mask: read(cs[2]),
    // Только запросы СЛОЁВ редактора (channel=rgb / channel=a): превью самой
    // ноды тоже тянет clipspace-файл, но без channel.
    resources: performance.getEntriesByType('resource')
      .map((e) => e.name)
      .filter((n) => n.indexOf('clipspace-') >= 0 && n.indexOf('channel=') >= 0)
      .map((n) => {
        try { return new URL(n).searchParams.get('filename') || n; } catch (e) { return n; }
      }),
  };
})()
"""


async def session(args, port: int, mask_value: str) -> tuple[str, dict]:
    """Свежая страница ComfyUI, нода с mask_file=mask_value, открытый редактор.

    Возвращает (значение виджета `image`, замер канвасов). Отдельная сессия на
    фазу: диалог редактора закрывается только своей кнопкой, а вторая фаза
    должна открыть его заново с другим mask_file.
    """
    import websockets  # зависимость ComfyUI

    profile = Path(tempfile.mkdtemp(prefix="degg-crop-restore-"))
    chrome = subprocess.Popen(
        [
            find_chrome(),
            "--headless=new",
            f"--remote-debugging-port={port}",
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
        page = wait_devtools(port)
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024) as ws:
            cdp = CDP(ws)
            await cdp.call("Runtime.enable")
            await cdp.call("Page.enable")
            print("→ ждём фронтенд…")
            await wait_for(cdp, "!!(window.app && window.app.isGraphReady)", 180, "isGraphReady")
            await cdp.eval(INSTALL_JS)
            setup = await cdp.eval(
                SETUP_JS.replace("__SOURCE__", json.dumps(SOURCE_FILE)).replace(
                    "__MASK__", json.dumps(mask_value)
                )
            )
            print(f"→ нода: {setup}")
            check("mask_file принят нодой", (setup or {}).get("maskSet"), setup)

            # Превью ноды грузит файл редактора как слой (без channel=) — по
            # этому запросу видно, что сравнение пикселей уже может состояться.
            layer = mask_value.split(" ")[0]
            await wait_for(
                cdp,
                "performance.getEntriesByType('resource')"
                f".some((e) => e.name.indexOf({json.dumps(layer)}) >= 0)",
                25,
                "превью загрузило файл редактора",
            )
            widget = await cdp.eval(WIDGET_JS)
            print(f"→ виджет image = {widget!r}")

            await cdp.eval("performance.clearResourceTimings()")
            await cdp.eval(OPEN_JS)
            await wait_for(
                cdp,
                "!!document.querySelector('.maskEditor-dialog-root #maskEditorCanvasContainer canvas')",
                30,
                "диалог редактора",
            )
            # Ждём, пока слои догрузятся и канвасы получат размер картинки
            # (до инициализации они 300x150 и пустые).
            deadline = time.time() + 25
            measured = {}
            while time.time() < deadline:
                measured = await cdp.eval(
                    MEASURE_JS.replace("__MR__", str(MARKER[0]))
                    .replace("__MG__", str(MARKER[1]))
                    .replace("__MB__", str(MARKER[2]))
                )
                img0 = (measured or {}).get("img") or {}
                if (img0.get("w") or 0) >= 700:
                    break
                await asyncio.sleep(0.3)
            print(f"→ замер: {json.dumps(measured, ensure_ascii=False)[:900]}")
            return widget, measured or {}
    finally:
        chrome.terminate()
        try:
            chrome.wait(timeout=10)
        except Exception:
            chrome.kill()
        if not args.keep:
            import shutil

            shutil.rmtree(profile, ignore_errors=True)


def _pct(value, total) -> float:
    return (value or 0) / max(1, total or 0)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8188/")
    ap.add_argument("--port", type=int, default=9336)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    mask_value, foreign_value, fixtures = make_fixtures()
    try:
        # ── Фаза 1: файл редактора от ТЕКУЩЕЙ картинки ──────────────────────
        print(f"→ фаза 1: mask_file = {mask_value}")
        widget, measured = await session(args, args.port, mask_value)
        check("виджет image = файл редактора (слои находятся)", widget == mask_value, widget)
        check("редактор открылся", bool(measured.get("open")), measured)
        res = measured.get("resources") or []
        check(
            "запрошен файл слоя маски",
            any(n.startswith("clipspace-mask-") for n in res),
            res[:6],
        )
        img = measured.get("img") or {}
        check(
            "основа редактора — RGB слоя маски, а не источник",
            _pct(img.get("marker"), img.get("total")) > 0.5,
            img,
        )
        mask = measured.get("mask") or {}
        check(
            "маска восстановлена (ненулевая альфа канваса маски)",
            _pct(mask.get("alpha"), mask.get("total")) > 0.1,
            mask,
        )
        rgb = measured.get("rgb") or {}
        check(
            "кисть восстановлена (красные пиксели канваса rgb)",
            _pct(rgb.get("red"), rgb.get("total")) > 0.02,
            rgb,
        )

        # ── Фаза 2: файл редактора от ДРУГОЙ картинки ───────────────────────
        print(f"→ фаза 2 (чужая картинка): mask_file = {foreign_value}")
        widget2, measured2 = await session(args, args.port + 1, foreign_value)
        check(
            "чужой файл не подставляется: виджет image = источник",
            widget2 == SOURCE_FILE,
            widget2,
        )
        img2 = measured2.get("img") or {}
        check(
            "редактор открыл источник, а не слой чужой картинки",
            (img2.get("total") or 0) > 0 and (img2.get("marker") or 0) == 0,
            img2,
        )
        mask2 = measured2.get("mask") or {}
        check(
            "маска чужой картинки не восстановлена (альфа = 0)",
            (mask2.get("total") or 0) > 0 and (mask2.get("alpha") or 0) == 0,
            mask2,
        )
    finally:
        for p in fixtures:
            try:
                p.unlink()
            except Exception:
                pass

    print()
    print(f"ok: {OKS}   FAIL: {len(FAILS)}")
    if FAILS:
        print("ПРОБА НЕ ПРОЙДЕНА")
        return 1
    print("ПРОБА ПРОЙДЕНА")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
