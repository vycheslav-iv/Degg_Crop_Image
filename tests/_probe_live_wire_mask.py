"""Живая проверка: Mask Editor рисует по ТЕКУЩЕМУ источнику, в том числе по проводу.

Симптом (жалоба 2026-10-23): «загрузил картинку в ноду, поработал с маской, а
потом подключил картинку проводом — маску можно делать только по загруженному
файлу, по подключённой картинке не сделать».

Причина: фронтенд при открытии Mask Editor берёт картинку в порядке
`node.images` → `app.nodeOutputs[id]` → `node.imgs`. После сохранения маски
редактор кладёт в `node.images` ПОКРАШЕННЫЙ файл, и наш сброс (удаление
`app.nodeOutputs` + `node.imgs`) этот канал не чистил. Поэтому редактор
открывался по старой покраске, а не по текущему источнику.

Что проверяется:
  1. режим провода: редактор открывается по картинке из провода;
  2. после сохранения маски ссылка на файл редактора доехала до `mask_file`;
  3. ПОВТОРНОЕ открытие редактора — снова картинка из провода (а не покраска);
  4. маска, нарисованная в файловом режиме, не подменяет источник после
     подключения провода — редактор открывает картинку из провода.

Образцы: синяя картинка (её отдаёт LoadImage по проводу) и зелёная (выбрана в
комбо ноды). Цвет в редакторе различает, ЧТО именно он загрузил.

Запуск (ComfyUI должен быть запущен):
    cd Degg_Crop_Image
    python tests/_probe_live_wire_mask.py
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
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

from _probe_live_dom_crop_image import (  # noqa: E402
    CDP,
    INSTALL_JS,
    find_chrome,
    wait_devtools,
    wait_for,
)

FAILS: list[str] = []
OKS = 0

INPUT_DIR = Path(r"D:\ComfyUI_windows_portable\ComfyUI\input")
WIRE_FILE = "probe-wire-blue.png"
FILE_FILE = "probe-file-green.png"
WIRE_RGB = (0, 90, 220)
FILE_RGB = (0, 200, 60)


def check(label: str, cond, detail="") -> None:
    global OKS
    if cond:
        OKS += 1
        print(f"  ok    {label}")
    else:
        FAILS.append(label)
        print(f"  FAIL  {label}" + (f"  [{detail}]" if detail else ""))


def make_fixtures() -> None:
    from PIL import Image

    Image.new("RGB", (400, 300), WIRE_RGB).save(INPUT_DIR / WIRE_FILE)
    Image.new("RGB", (400, 300), FILE_RGB).save(INPUT_DIR / FILE_FILE)


SETUP_JS = r"""
(() => {
  app.graph.clear();
  const api = window.comfyAPI.api.api;

  const load = LiteGraph.createNode('LoadImage');
  load.pos = [80, 120];
  load.title = 'upstream';
  app.graph.add(load);
  const w = (load.widgets || []).find((x) => x.name === 'image');
  if (w) w.value = __WIRE__;
  load.imgs = [{ src: api.apiURL('/view?filename=' + encodeURIComponent(__WIRE__) + '&type=input') }];

  const crop = LiteGraph.createNode('DeggCropImage');
  crop.pos = [620, 120];
  app.graph.add(crop);
  crop.setSize([600, 900]);
  const file = (crop.widgets || []).find((x) => x.name === 'image_file');
  if (file) {
    file.value = __FILE__;
    file.callback?.(file.value);
  }
  const slot = (crop.inputs || []).findIndex((s) => s && s.name === 'image');
  window.__probe = {
    load,
    crop,
    slot,
    connect: () => window.__probe.load.connect(0, window.__probe.crop, window.__probe.slot),
    disconnect: () => window.__probe.crop.disconnectInput(window.__probe.slot),
    maskFile: () => ((crop.widgets || []).find((x) => x.name === 'mask_file') || {}).value || '',
    imageWidget: () => ((crop.widgets || []).find((x) => x.name === 'image') || {}).value ?? null,
  };
  app.canvas.selectNodes([crop]);
  app.canvas.centerOnNode(crop);
  app.canvas.setDirty(true, true);
  return { slot };
})()
"""

OPEN_JS = r"""
(() => {
  const crop = window.__probe.crop;
  app.canvas.selectNodes([crop]);
  app.extensionManager.command.execute('Comfy.MaskEditor.OpenMaskEditor');
  return true;
})()
"""

CANVAS_JS = r"""
(() => {
  const d = document.querySelector('.mask-editor-root') || document.querySelector('[role="dialog"]');
  if (!d) return null;
  const c = d.querySelector('canvas');
  if (!c) return null;
  const r = c.getBoundingClientRect();
  return { x: r.x, y: r.y, w: r.width, h: r.height };
})()
"""

CLOSE_JS = r"""
(() => {
  const d = document.querySelector('.mask-editor-root') || document.querySelector('[role="dialog"]');
  if (!d) return 'none';
  const btn = Array.from(d.querySelectorAll('button')).find((b) => /Отмена|Cancel/i.test(b.textContent || ''));
  if (!btn) return 'no-button';
  btn.click();
  return 'closed';
})()
"""

PREVIEW_JS = r"""
(() => {
  const n = window.__probe.crop;
  const root = n.__deggCrop && n.__deggCrop.widget;
  if (!root) return null;
  const img = root.querySelector('.degg-crop__img');
  const layer = root.querySelector('.degg-crop__masklayer');
  const stage = root.querySelector('.degg-crop__stage');
  const r = stage ? stage.getBoundingClientRect() : null;
  return {
    src: img ? String(img.getAttribute('src') || '') : '',
    vis: img ? getComputedStyle(img).visibility : '',
    layer: layer ? getComputedStyle(layer).display : '',
    box: r ? { x: r.x, y: r.y, w: r.width, h: r.height } : null,
  };
})()
"""

RESTORE_JS = r"""
(async () => {
  const json = app.graph.serialize();
  // Настоящий путь загрузки воркфлоу (не graph.configure напрямую).
  await app.loadGraphData(json);
  const crop = app.graph._nodes.find((n) => n.type === 'DeggCropImage');
  if (!crop) return { error: 'нода не восстановилась' };
  window.__probe.crop = crop;
  window.__probe.load = app.graph._nodes.find((n) => n.type === 'LoadImage');
  window.__probe.slot = (crop.inputs || []).findIndex((s) => s && s.name === 'image');
  window.__probe.maskFile = () => ((crop.widgets || []).find((x) => x.name === 'mask_file') || {}).value || '';
  window.__probe.imageWidget = () => ((crop.widgets || []).find((x) => x.name === 'image') || {}).value ?? null;
  app.canvas.selectNodes([crop]);
  app.canvas.centerOnNode(crop);
  return { ok: true, linked: (crop.inputs || []).some((s) => s && s.link != null) };
})()
"""


async def preview_pixel(cdp: CDP) -> tuple[str, object]:
    """Цвет превью ноды (не редактора): 25% от левого верхнего угла сцены.

    Точка выбрана вне штриха кисти (он идёт через центр), чтобы «покраска»
    нельзя было спутать с «источником».
    """
    st = await cdp.eval(PREVIEW_JS)
    if not st or not st.get("box"):
        return "сцены нет", None
    box = st["box"]
    shot = await grab(
        cdp,
        {
            "x": box["x"] + box["w"] * 0.25,
            "y": box["y"] + box["h"] * 0.25,
            "w": 4,
            "h": 4,
        },
    )
    return classify(shot.getpixel((2, 2))), shot.getpixel((2, 2))


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


def classify(px) -> str:
    r, g, b = px
    if b > 120 and b > r + 60 and b > g + 60:
        return "провод (синяя)"
    if g > 120 and g > r + 60 and g > b + 40:
        return "файл (зелёная)"
    if r < 60 and g < 60 and b < 60:
        return "покраска/пусто"
    return f"неизвестно{px}"


async def editor_source(cdp: CDP, attempts: int = 12) -> tuple[str, object]:
    """Что редактор РЕАЛЬНО загрузил: цвет центра его холста.

    Холст сначала чёрный (картинка ещё грузится), поэтому ждём цвет, а не
    один замер: иначе таймаут загрузки выглядел бы как «открыт по покраске».
    """
    box = None
    px = None
    for _ in range(max(1, attempts)):
        box = await cdp.eval(CANVAS_JS)
        if box:
            shot = await grab(cdp, box)
            w, h = shot.size
            px = shot.getpixel((int(w * 0.5), int(h * 0.5)))
            r, g, b = px
            if r > 60 or g > 60 or b > 60:
                break
        await asyncio.sleep(0.5)
    if not box:
        return "холста нет", None
    return classify(px), px


async def paint_and_save(cdp: CDP, box: dict) -> bool:
    """Штрих кистью по центру холста и кнопка «Сохранить» (как делает человек)."""

    async def send(kind, x, y, buttons=0):
        await cdp.call(
            "Input.dispatchMouseEvent",
            {
                "type": kind,
                "x": x,
                "y": y,
                "button": "left",
                "buttons": buttons,
                "clickCount": 1,
            },
        )

    cx, cy = box["x"] + box["w"] * 0.5, box["y"] + box["h"] * 0.5
    await send("mouseMoved", cx - 60, cy)
    await send("mousePressed", cx - 60, cy, 1)
    for i in range(1, 13):
        await send("mouseMoved", cx - 60 + i * 10, cy, 1)
        await asyncio.sleep(0.03)
    await send("mouseReleased", cx + 60, cy, 0)
    await asyncio.sleep(0.6)
    return bool(
        await cdp.eval(
            r"""
(() => {
  const d = document.querySelector('.mask-editor-root') || document.querySelector('[role="dialog"]');
  const btn = d && Array.from(d.querySelectorAll('button'))
    .find((b) => /Сохранить|Save/i.test(b.textContent || ''));
  if (!btn) return false;
  btn.click();
  return true;
})()
"""
        )
    )


async def open_editor(cdp: CDP) -> None:
    await cdp.eval(OPEN_JS)
    await wait_for(cdp, "!!document.querySelector('.mask-editor-root, [role=\"dialog\"] canvas')", 20, "редактор маски")
    await asyncio.sleep(1.6)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8188/")
    ap.add_argument("--port", type=int, default=9348)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    import websockets  # зависимость ComfyUI

    make_fixtures()
    profile = Path(tempfile.mkdtemp(prefix="degg-crop-wire-mask-"))
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
            await wait_for(cdp, "!!(window.app && window.app.graph && window.LiteGraph)", 90, "app")
            await cdp.eval(INSTALL_JS)
            await asyncio.sleep(2.0)

            setup = SETUP_JS.replace("__WIRE__", f"'{WIRE_FILE}'").replace("__FILE__", f"'{FILE_FILE}'")
            print("настройка:", await cdp.eval(setup))
            await cdp.eval("window.__probe.connect()")
            await asyncio.sleep(1.8)

            print("\n[1] режим провода: что откроет редактор")
            check("в режиме провода служебный виджет image пуст", (await cdp.eval("window.__probe.imageWidget()")) == "")
            await open_editor(cdp)
            what, px = await editor_source(cdp)
            check("редактор открыт по картинке из провода", what == "провод (синяя)", f"{what} {px}")
            box = await cdp.eval(CANVAS_JS)
            check("кнопка «Сохранить» нажалась", await paint_and_save(cdp, box))
            await asyncio.sleep(2.5)

            mask_file = await cdp.eval("window.__probe.maskFile()")
            print(f"  · mask_file = {mask_file!r}")
            check("маска из редактора доехала до mask_file", "clipspace-" in (mask_file or ""), mask_file)

            print("\n[2] повторное открытие — источник не должен подмениться покраской")
            await open_editor(cdp)
            what, px = await editor_source(cdp)
            check("повторно редактор открыт по картинке из провода", what == "провод (синяя)", f"{what} {px}")
            await cdp.eval(CLOSE_JS)
            await asyncio.sleep(0.8)

            print("\n[3] маска нарисована в файловом режиме, потом подключили провод")
            await cdp.eval("window.__probe.disconnect()")
            await asyncio.sleep(1.2)
            await open_editor(cdp)
            what, px = await editor_source(cdp)
            check("в файловом режиме редактор открыт по файлу из комбо", what == "файл (зелёная)", f"{what} {px}")
            box = await cdp.eval(CANVAS_JS)
            await paint_and_save(cdp, box)
            await asyncio.sleep(2.5)
            file_mode_mask = await cdp.eval("window.__probe.maskFile()")
            print(f"  · mask_file после файловой покраски = {file_mode_mask!r}")

            await cdp.eval("window.__probe.connect()")
            await asyncio.sleep(1.8)
            await open_editor(cdp)
            what, px = await editor_source(cdp)
            check(
                "после подключения провода редактор открыт по картинке из провода, а не по файлу",
                what == "провод (синяя)",
                f"{what} {px}",
            )
            await cdp.eval(CLOSE_JS)
            await asyncio.sleep(0.8)

            # Смена РОДА источника (файл ↔ провод) обесценивает покраску:
            # она нарисована по другой картинке. Иначе превью ноды продолжает
            # показывать старый файл, а сервер применяет старую маску.
            after_wire_mask = await cdp.eval("window.__probe.maskFile()")
            print(f"  · mask_file после подключения провода = {after_wire_mask!r}")
            check("маска файла сброшена при переходе на провод", not after_wire_mask, after_wire_mask)
            pv = await cdp.eval(PREVIEW_JS)
            check("превью показывает источник из провода (слой маски скрыт)",
                  bool(pv) and pv["vis"] != "hidden" and pv["layer"] == "none",
                  pv and {"vis": pv["vis"], "layer": pv["layer"]})
            color, rgb = await preview_pixel(cdp)
            check("в превью видна синяя картинка провода, а не покраска файла",
                  color == "провод (синяя)", f"{color} {rgb}")

            print("\n[4] сохранённый воркфлоу не должен терять маску при загрузке")
            await open_editor(cdp)
            box = await cdp.eval(CANVAS_JS)
            await paint_and_save(cdp, box)
            await asyncio.sleep(2.5)
            before_restore = await cdp.eval("window.__probe.maskFile()")
            check("маска в режиме провода записалась", bool(before_restore), before_restore)
            info = await cdp.eval(RESTORE_JS, await_promise=True)
            print(f"  · восстановление: {info}")
            await asyncio.sleep(2.2)
            after_restore = await cdp.eval("window.__probe.maskFile()")
            check("после перезагрузки воркфлоу маска на месте",
                  bool(after_restore) and after_restore == before_restore,
                  f"было {before_restore!r}, стало {after_restore!r}")

            print()
            print("[5] после загрузки воркфлоу смена рода источника тоже сбрасывает маску")
            await cdp.eval("window.__probe.disconnect()")
            await asyncio.sleep(1.8)
            after_disconnect = await cdp.eval("window.__probe.maskFile()")
            print(f"  · mask_file после отключения провода = {after_disconnect!r}")
            check("маска сброшена при возврате к файлу", not after_disconnect, after_disconnect)
            color, rgb = await preview_pixel(cdp)
            check("превью показывает файл из комбо", color == "файл (зелёная)", f"{color} {rgb}")

            print()
            print("[6] воркфлоу загружен в файловом режиме с маской — подключение провода её сбрасывает")
            await open_editor(cdp)
            box = await cdp.eval(CANVAS_JS)
            await paint_and_save(cdp, box)
            await asyncio.sleep(2.5)
            file_mask_before = await cdp.eval("window.__probe.maskFile()")
            check("маска в файловом режиме записалась", bool(file_mask_before), file_mask_before)
            info = await cdp.eval(RESTORE_JS, await_promise=True)
            print(f"  · восстановление: {info}")
            await asyncio.sleep(2.2)
            file_mask_after = await cdp.eval("window.__probe.maskFile()")
            check("файловая маска пережила загрузку воркфлоу",
                  bool(file_mask_after) and file_mask_after == file_mask_before,
                  f"было {file_mask_before!r}, стало {file_mask_after!r}")
            await cdp.eval("window.__probe.connect()")
            await asyncio.sleep(1.8)
            after_connect = await cdp.eval("window.__probe.maskFile()")
            print(f"  · mask_file после подключения провода = {after_connect!r}")
            check("маска сброшена при переходе на провод после загрузки",
                  not after_connect, after_connect)
            color, rgb = await preview_pixel(cdp)
            check("превью снова показывает провод", color == "провод (синяя)", f"{color} {rgb}")

            errors = await cdp.eval("window.__probeErrors")
            real = [e for e in (errors or []) if "favicon" not in e]
            check("консоль без ошибок", not real, real[:3])
    finally:
        try:
            chrome.terminate()
            chrome.wait(timeout=10)
        except Exception:
            chrome.kill()
        for name in (WIRE_FILE, FILE_FILE):
            try:
                (INPUT_DIR / name).unlink()
            except Exception:
                pass
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
