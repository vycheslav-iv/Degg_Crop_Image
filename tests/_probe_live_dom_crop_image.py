"""Живой замер DOM ноды DeggCropImage в настоящем браузере (headless Chrome + CDP).

Зачем: бэкенд проверяется `_probe_live_crop_image.py`, но вся суть ноды — DOM-
редактор кадра. Смоук работает на заглушках DOM и не может поймать, например,
что превью не появляется сразу после соединения провода.

Что проверяется:
  1. пустое состояние до соединения (нет `src`, видна подсказка);
  2. превью входа появляется СРАЗУ после connect, без запуска воркфлоу;
  3. плашка кадра совпадает с расчётом Python (`output_size`);
  4. drag восточной ручки меняет `crop_rect` и сохраняется в виджет;
  5. смена пропорции держит центр кадра и даёт 16:9;
  6. затемнение 0..100% управляет `box-shadow`;
  7. высота DOM-виджета растёт вместе с нодой (sizing).

Запуск (ComfyUI должен быть запущен):
    cd Degg_Crop_Image
    python tests/_probe_live_dom_crop_image.py
    python tests/_probe_live_dom_crop_image.py --url http://127.0.0.1:8188/ --keep

Зависимости — только те, что уже есть в ComfyUI (websockets).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]

FAILS: list[str] = []
OKS = 0


def check(label: str, cond, detail="") -> None:
    global OKS
    if cond:
        OKS += 1
        print(f"  ok    {label}")
    else:
        FAILS.append(label)
        print(f"  FAIL  {label}" + (f"  [{detail}]" if detail else ""))


def find_chrome() -> str:
    env = os.environ.get("CHROME_PATH")
    if env and Path(env).exists():
        return env
    for path in CHROME_CANDIDATES:
        if Path(path).exists():
            return path
    raise SystemExit("Chrome не найден (задай CHROME_PATH)")


def wait_devtools(port: int, timeout: float = 30.0) -> dict:
    url = f"http://127.0.0.1:{port}/json/list"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                pages = json.load(resp)
            for page in pages:
                if page.get("type") == "page" and page.get("webSocketDebuggerUrl"):
                    return page
        except Exception:
            pass
        time.sleep(0.3)
    raise SystemExit("CDP не отвечает: страница не найдена")


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self._id = 0
        self.events: list[dict] = []

    async def call(self, method: str, params: dict | None = None):
        self._id += 1
        mid = self._id
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            if "method" in msg:
                self.events.append(msg)

    async def eval(self, expr: str, await_promise: bool = False):
        res = await self.call(
            "Runtime.evaluate",
            {"expression": expr, "returnByValue": True, "awaitPromise": await_promise},
        )
        if "exceptionDetails" in res:
            raise RuntimeError(
                res["exceptionDetails"].get("exception", {}).get("description", "eval error")
            )
        return res.get("result", {}).get("value")


async def wait_for(cdp: CDP, expr: str, timeout: float = 60.0, label: str = ""):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = await cdp.eval(expr)
            if last:
                return last
        except Exception:
            pass
        await asyncio.sleep(0.15)
    raise SystemExit(f"не дождались: {label or expr} (последнее: {last!r})")


async def drag_mouse(cdp: CDP, x: float, y: float, dx: float, dy: float, steps: int = 10) -> None:
    await cdp.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
    await cdp.call(
        "Input.dispatchMouseEvent",
        {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1, "buttons": 1},
    )
    for i in range(1, steps + 1):
        await cdp.call(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseMoved",
                "x": x + dx * i / steps,
                "y": y + dy * i / steps,
                "button": "left",
                "buttons": 1,
            },
        )
        await asyncio.sleep(0.04)
    await cdp.call(
        "Input.dispatchMouseEvent",
        {"type": "mouseReleased", "x": x + dx, "y": y + dy, "button": "left", "clickCount": 1, "buttons": 0},
    )


INSTALL_JS = r"""
(() => {
  window.__probeErrors = [];
  window.addEventListener('error', (e) => window.__probeErrors.push('error: ' + (e.message || '')));
  window.addEventListener('unhandledrejection', (e) => window.__probeErrors.push('reject: ' + String(e.reason)));
  const origError = console.error;
  console.error = (...args) => { window.__probeErrors.push('console.error: ' + args.map(String).join(' ')); return origError.apply(console, args); };
  return 'installed';
})()
"""

SETUP_JS = r"""
(() => {
  const g = app.graph;
  const load = LiteGraph.createNode('LoadImage');
  load.pos = [80, 120];
  g.add(load);
  load.title = 'upstream';
  const imageWidget = (load.widgets || []).find((w) => w.name === 'image');
  if (imageWidget) imageWidget.value = 'example.png';
  // Ровно то, что делает нативная LoadImage после выбора файла: отдаёт src.
  load.imgs = [{ src: window.comfyAPI.api.api.apiURL('/view?filename=' + encodeURIComponent('example.png') + '&type=input') }];

  const crop = LiteGraph.createNode('DeggCropImage');
  crop.pos = [620, 120];
  g.add(crop);
  crop.setSize([460, 460]);
  window.__probeLoad = load;
  window.__probeCrop = crop;
  app.canvas.selectNodes([crop]);
  app.canvas.centerOnNode(crop);
  app.canvas.setZoom(1);
  app.canvas.setDirty(true, true);
  return { load: load.id, crop: crop.id, size: crop.size.slice() };
})()
"""

STATE_JS = r"""
(() => {
  const n = window.__probeCrop;
  const root = n && n.__deggCrop && n.__deggCrop.widget;
  if (!root) return { mounted: false };
  const q = (sel) => root.querySelector(sel);
  const box = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: r.x, y: r.y, w: r.width, h: r.height };
  };
  const img = q('.degg-crop__img');
  const frame = q('.degg-crop__frame');
  const dim = q('.degg-crop__dim');
  const fill = q('.degg-crop__fill');
  const badge = q('.degg-crop__badge');
  const empty = q('.degg-crop__empty');
  const stage = q('.degg-crop__stage');
  const widget = (n.widgets || []).find((w) => w.name === 'preview');
  const cropWidget = (n.widgets || []).find((w) => w.name === 'crop_rect');
  return {
    mounted: true,
    src: img ? (img.getAttribute('src') || '') : '',
    natural: img ? [img.naturalWidth, img.naturalHeight] : [0, 0],
    stage: box(stage),
    imgBox: box(img),
    frameBox: box(frame),
    badge: badge ? badge.textContent : '',
    badgeBox: box(badge),
    badgeVisible: !!(badge && badge.style.display !== 'none'),
    emptyVisible: !!(empty && empty.style.display !== 'none'),
    dimShadow: dim ? String(dim.style.boxShadow) : '',
    fillBox: box(fill),
    fillBg: fill ? getComputedStyle(fill).backgroundColor : '',
    fillIndex: fill && stage ? Array.prototype.indexOf.call(stage.children, fill) : -1,
    imgIndex: img && stage ? Array.prototype.indexOf.call(stage.children, img) : -1,
    frameBorder: frame ? String(frame.style.borderTopColor || frame.style.borderColor) : '',
    rootBox: box(root),
    widgetHeight: widget ? box(widget.element).h : 0,
    rect: cropWidget ? cropWidget.value : '',
    frac: n.__deggCrop.state.frac,
    imgState: n.__deggCrop.state.img,
    zoom: app.canvas.ds.scale,
  };
})()
"""


def rect_of(value) -> dict:
    try:
        data = json.loads(value or "")
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8188/")
    ap.add_argument("--port", type=int, default=9333)
    ap.add_argument("--keep", action="store_true", help="не удалять профиль Chrome")
    args = ap.parse_args()

    import websockets  # зависимость ComfyUI

    import crop_image as ci  # локальный расчёт для сверки плашки

    expected_w, expected_h = ci.output_size(768, 768, 2.0, 8)
    print(f"→ ожидание плашки по Python: {expected_w} × {expected_h}")

    profile = Path(tempfile.mkdtemp(prefix="degg-crop-probe-"))
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
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=32 * 1024 * 1024) as ws:
            cdp = CDP(ws)
            await cdp.call("Runtime.enable")
            print("→ ждём фронтенд…")
            await wait_for(cdp, "!!(window.app && window.app.isGraphReady)", 180, "isGraphReady")
            await cdp.eval(INSTALL_JS)
            setup = await cdp.eval(SETUP_JS)
            print(f"→ ноды: {setup}")
            await asyncio.sleep(1.2)

            before = await cdp.eval(STATE_JS)
            check("DOM-виджет смонтирован", bool(before.get("mounted")), before)
            check("до соединения src пустой", before.get("src") == "", before.get("src"))
            check("до соединения видна подсказка", bool(before.get("emptyVisible")))

            # Порядок виджетов на теле ноды (снизу вверх в UI — как в INPUT_TYPES).
            order = await cdp.eval(
                "(() => (window.__probeCrop.widgets || []).map((w) => w.name)"
                ".filter((n) => n !== 'preview' && n !== 'crop_rect'))()"
            )
            expected_order = [
                "lock_ratio",
                "aspect_ratio",
                "megapixels",
                "multiplicity",
                "resample",
                "fill",
                "dim_outside",
            ]
            check("порядок виджетов как в задании", order == expected_order, order)

            await cdp.eval(
                "(() => { const c = window.__probeCrop, l = window.__probeLoad;"
                " return l.connect(0, c, 0); })()"
            )
            diag = await cdp.eval(
                "(() => { const c = window.__probeCrop; const s = c.__deggCrop.state;"
                " return { INPUT: window.LiteGraph && window.LiteGraph.INPUT,"
                " hooked: typeof c.onConnectionsChange, link: c.inputs[0] && c.inputs[0].link,"
                " up: c.getInputNode && c.getInputNode(0) && c.getInputNode(0).id,"
                " imgs: (window.__probeLoad.imgs || []).map((i) => i && i.src),"
                " img: s.img, key: s.key, src: c.__deggCrop.widget.querySelector('.degg-crop__img').getAttribute('src') }; })()"
            )
            print(f"→ диагностика после connect: {diag}")
            await asyncio.sleep(1.0)
            diag2 = await cdp.eval("(() => { const s = window.__probeCrop.__deggCrop.state; return { img: s.img, key: s.key }; })()")
            print(f"→ через 1 c: {diag2}")
            chain = await cdp.eval(
                "(() => { const n = window.__probeCrop; const w = n.widgets.find((x) => x.name === 'preview');"
                " const el = w.element; const items = [];"
                " for (let e = el; e && e !== document.body; e = e.parentElement) {"
                "   const r = e.getBoundingClientRect(); const cs = getComputedStyle(e);"
                "   items.push({ tag: e.tagName, cls: String(e.className || '').slice(0, 40),"
                "     w: Math.round(r.width), h: Math.round(r.height), x: Math.round(r.x), y: Math.round(r.y),"
                "     disp: cs.display, pos: cs.position, hcss: cs.height, mh: cs.minHeight }); }"
                " const stage = el.querySelector('.degg-crop__stage'); const root = el.querySelector('.degg-crop');"
                " return { items, stage: stage ? [stage.clientWidth, stage.clientHeight, getComputedStyle(stage).height] : null,"
                "   root: root ? [getComputedStyle(root).height, getComputedStyle(root).display] : null,"
                "   nodeSize: n.size, nodePos: n.pos, vue: !!(window.LiteGraph && window.LiteGraph.vueNodesMode),"
                "   lastY: w.last_y, computed: w.computeLayoutSize && w.computeLayoutSize() }; })()"
            )
            print(f"→ цепочка: {json.dumps(chain, ensure_ascii=False)[:900]}")
            after = await cdp.eval(STATE_JS)
            check("превью появилось после connect", "example.png" in after.get("src", ""), after.get("src"))
            check("размер источника прочитан", after.get("natural") == [768, 768], after.get("natural"))
            check("подсказка скрыта", not after.get("emptyVisible"))
            outputs_diag = await cdp.eval(
                "(() => ({ keys: Object.keys(app.nodeOutputs || {}),"
                " v72: (app.nodeOutputs || {})['72'] || null,"
                " lastPrompt: app.lastPromptId || null }))()"
            )
            print(f"→ nodeOutputs: {json.dumps(outputs_diag, ensure_ascii=False)[:500]}")
            check(
                "upstream не исполнялся (нет prompt_id и выходов)",
                not outputs_diag.get("lastPrompt")
                and not (outputs_diag.get("v72") or {}).get("images"),
                outputs_diag,
            )

            badge = after.get("badge") or ""
            check("плашка видна", bool(after.get("badgeVisible")))
            check(f"плашка содержит {expected_w} × {expected_h} px", f"{expected_w} × {expected_h} px" in badge, badge)
            exp_mp = expected_w * expected_h / 1e6
            check(
                f"плашка показывает фактические MP ({exp_mp:.2f})",
                "MP" in badge and f"{exp_mp:.2f}" in badge,
                badge,
            )

            frame = after.get("frameBox") or {}
            stage = after.get("stage") or {}
            check("кадр вписан в сцену", frame.get("w", 0) > 50 and frame.get("h", 0) > 50, frame)
            check(
                "кадр не выходит за сцену",
                frame.get("x", -1) >= stage.get("x", 0) - 1
                and frame.get("y", -1) >= stage.get("y", 0) - 1
                and frame.get("x", 0) + frame.get("w", 0) <= stage.get("x", 0) + stage.get("w", 1) + 1
                and frame.get("y", 0) + frame.get("h", 0) <= stage.get("y", 0) + stage.get("h", 1) + 1,
                {"frame": frame, "stage": stage},
            )
            check("замок включён → рамка зелёная", "127" in after.get("frameBorder", "") and "224" in after.get("frameBorder", ""), after.get("frameBorder"))

            # §5: при «Свободных» рамка меняется произвольно даже с включённым
            # замком, поэтому сохранение пропорции проверяем на 1:1.
            await cdp.eval(
                "(() => { const n = window.__probeCrop; const w = n.widgets.find((x) => x.name === 'aspect_ratio');"
                " w.value = '1:1'; w.callback && w.callback(w.value); return w.value; })()"
            )
            await asyncio.sleep(0.3)
            handle = await cdp.eval(
                "(() => { const r = window.__probeCrop.__deggCrop.widget"
                ".querySelector('.degg-crop__h[data-h=\"e\"]').getBoundingClientRect();"
                " return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()"
            )
            start_w = rect_of(after.get("rect")).get("w", 0)
            await drag_mouse(cdp, handle["x"], handle["y"], -60, 0)
            await asyncio.sleep(0.35)
            dragged = await cdp.eval(STATE_JS)
            new_w = rect_of(dragged.get("rect")).get("w", 0)
            check("drag восточной ручки уменьшил кадр", 0 < new_w < start_w, {"before": start_w, "after": new_w})
            check("crop_rect сохранён в виджет", abs(new_w - dragged.get("frac", {}).get("w", -1)) < 1e-6, dragged.get("rect"))
            ratio_px = (dragged.get("frameBox") or {}).get("w", 0) / max(1e-6, (dragged.get("frameBox") or {}).get("h", 0))
            check("пропорция 1:1 сохранена после drag", abs(ratio_px - 1) < 0.02, ratio_px)
            drag_badge = await cdp.eval(STATE_JS)
            # megapixels — целевая площадь ВЫХОДА: рамка задаёт пропорцию и
            # содержимое, поэтому уменьшение рамки размер выхода не меняет.
            check(
                "плашка следует целевым MP, а не площади рамки",
                f"{expected_w} × {expected_h} px" in (drag_badge.get("badge") or ""),
                drag_badge.get("badge"),
            )

            # Центр сравниваем в координатах ИЗОБРАЖЕНИЯ: сцена пересчитывает
            # собственный offset при выходе рамки за границы.
            natural = drag_badge.get("natural") or [768, 768]
            frac = drag_badge.get("frac") or {}
            before_center = (
                (frac.get("x", 0) + frac.get("w", 0) / 2) * natural[0],
                (frac.get("y", 0) + frac.get("h", 0) / 2) * natural[1],
            )
            await cdp.eval(
                "(() => { const n = window.__probeCrop; const w = n.widgets.find((x) => x.name === 'aspect_ratio');"
                " w.value = '16:9'; w.callback && w.callback(w.value); return w.value; })()"
            )
            await asyncio.sleep(0.3)
            wide = await cdp.eval(STATE_JS)
            fb = wide.get("frameBox") or {}
            check("смена пропорции дала 16:9", abs(fb.get("w", 0) / max(1e-6, fb.get("h", 1)) - 16 / 9) < 0.03, fb)
            frac_w = wide.get("frac") or {}
            after_center = (
                (frac_w.get("x", 0) + frac_w.get("w", 0) / 2) * natural[0],
                (frac_w.get("y", 0) + frac_w.get("h", 0) / 2) * natural[1],
            )
            dx = abs(after_center[0] - before_center[0])
            dy = abs(after_center[1] - before_center[1])
            check("центр кадра сохранён", dx < 1.5 and dy < 1.5, {"dx": dx, "dy": dy})
            words = (wide.get("badge") or "").split("×")
            if len(words) == 2:
                got_w = int(words[0].strip())
                got_h = int("".join(ch for ch in words[1].split("px")[0] if ch.isdigit()))
                check("ширина/высота в плашке кратны 8", got_w % 8 == 0 and got_h % 8 == 0, (got_w, got_h))
                check(
                    "пропорция в плашке ≈ 16:9",
                    abs((got_w / got_h) / (16 / 9) - 1) < 0.01,
                    (got_w, got_h),
                )

            await cdp.eval(
                "(() => { const n = window.__probeCrop; const w = n.widgets.find((x) => x.name === 'dim_outside');"
                " w.value = 100; w.callback && w.callback(100); return w.value; })()"
            )
            await asyncio.sleep(0.2)
            dimmed = await cdp.eval(STATE_JS)
            shadow100 = (dimmed.get("dimShadow") or "").replace(" ", "")
            # Chrome нормализует alpha=1 в rgb(...) — принимаем обе формы.
            check(
                "затемнение 100% → непрозрачная тень",
                shadow100.startswith("rgb(0,0,0)") or "rgba(0,0,0,1)" in shadow100,
                dimmed.get("dimShadow"),
            )
            await cdp.eval(
                "(() => { const n = window.__probeCrop; const w = n.widgets.find((x) => x.name === 'dim_outside');"
                " w.value = 0; w.callback && w.callback(0); return w.value; })()"
            )
            await asyncio.sleep(0.2)
            clear = await cdp.eval(STATE_JS)
            check("затемнение 0% → без тени", (clear.get("dimShadow") or "none") == "none", clear.get("dimShadow"))

            # Заливка — фон АУТПЕЙНТА: лежит под изображением (иконка не тонируется)
            # и видна только там, где рамка вышла за границы картинки.
            await cdp.eval(
                "(() => { const n = window.__probeCrop; const w = n.widgets.find((x) => x.name === 'fill');"
                " w.value = 'black'; w.callback && w.callback('black'); return w.value; })()"
            )
            await asyncio.sleep(0.25)
            west = await cdp.eval(
                "(() => { const r = window.__probeCrop.__deggCrop.widget"
                ".querySelector('.degg-crop__h[data-h=\"w\"]').getBoundingClientRect();"
                " return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()"
            )
            await drag_mouse(cdp, west["x"], west["y"], -160, 0)
            await asyncio.sleep(0.35)
            filled = await cdp.eval(STATE_JS)
            fill_box = filled.get("fillBox") or {}
            img_box = filled.get("imgBox") or {}
            frame_box = filled.get("frameBox") or {}
            check(
                "заливка лежит под изображением (порядок слоёв)",
                -1 < filled.get("fillIndex", -1) < filled.get("imgIndex", -1),
                {"fill": filled.get("fillIndex"), "img": filled.get("imgIndex")},
            )
            check(
                "заливка покрывает кадр целиком",
                abs(fill_box.get("x", 0) - frame_box.get("x", 0)) < 1.5
                and abs(fill_box.get("y", 0) - frame_box.get("y", 0)) < 1.5
                and abs(fill_box.get("w", 0) - frame_box.get("w", 0)) < 1.5
                and abs(fill_box.get("h", 0) - frame_box.get("h", 0)) < 1.5,
                {"fill": fill_box, "frame": frame_box},
            )
            check(
                "рамка вышла за изображение (аутпейнт)",
                fill_box.get("x", 0) < img_box.get("x", 0) - 4
                or fill_box.get("y", 0) < img_box.get("y", 0) - 4,
                {"fill": fill_box, "img": img_box},
            )
            check(
                "заливка вне изображения чёрная",
                "rgb(0, 0, 0)" in (filled.get("fillBg") or ""),
                filled.get("fillBg"),
            )

            resample_label = await cdp.eval(
                "(() => { const n = window.__probeCrop; const w = n.widgets.find((x) => x.name === 'resample');"
                " const f = w && w.options && w.options.getOptionLabel;"
                " return f ? String(f('nearest-exact')) : 'nearest-exact'; })()"
            )
            check(
                "масштабирование подписано как в стандартных нодах (nearest-exact)",
                resample_label == "nearest-exact",
                resample_label,
            )

            # Случай из отчёта: 16:9, 2.0 MP, кратность 8 → 1928 × 1088,
            # как в ядре ComfyUI («Выбор разрешения»).
            exp_w, exp_h = ci.output_size(16.0, 9.0, 2.0, 8)
            badge16 = (filled.get("badge") or "").replace(chr(10), " ")
            check(
                f"плашка 16:9/2MP/8 -> {exp_w} × {exp_h}",
                badge16.startswith(f"{exp_w} × {exp_h}"),
                badge16,
            )
            mult_opts = await cdp.eval(
                "(() => { const w = window.__probeCrop.widgets.find((x) => x.name === 'multiplicity');"
                " const o = (w && w.options) || {}; return [o.min, o.max, o.step2]; })()"
            )
            check(
                "кратность 8..128 шагом 4 (как в «Выборе разрешения»)",
                mult_opts == [8, 128, 4],
                mult_opts,
            )

            h_before = wide.get("widgetHeight") or 0
            await cdp.eval("(() => { window.__probeCrop.setSize([600, 700]); return 1; })()")
            await asyncio.sleep(0.6)
            tall = await cdp.eval(STATE_JS)
            check(
                "DOM-виджет растёт вместе с нодой",
                (tall.get("widgetHeight") or 0) > h_before + 10,
                {"before": h_before, "after": tall.get("widgetHeight")},
            )
            check(
                "DOM-виджет не вылезает за ноду",
                (tall.get("rootBox") or {}).get("h", 0) <= 700,
                tall.get("rootBox"),
            )

            # Смена изображения в upstream-ноде (загрузка файла в LoadImage):
            # фронтенд пишет app.nodeOutputs и зовёт node.onWidgetChanged —
            # превью обязано обновиться без запуска воркфлоу (как у нативной
            # ноды кадрирования).
            await cdp.eval(
                "(() => { const l = window.__probeLoad;"
                " const w = (l.widgets || []).find((x) => x.name === 'image');"
                " const prev = w.value; w.value = '01.webp';"
                " app.nodeOutputs[String(l.id)] = { images: [{ filename: '01.webp', subfolder: '', type: 'input' }] };"
                " if (l.onWidgetChanged) l.onWidgetChanged('image', '01.webp', prev, w);"
                " return w.value; })()"
            )
            await asyncio.sleep(1.0)
            refreshed = await cdp.eval(STATE_JS)
            check(
                "смена изображения upstream обновляет превью",
                "01.webp" in (refreshed.get("src") or ""),
                refreshed.get("src"),
            )

            # Nodes 2.0 (createWidgetUpdateHandler) зовёт ТОЛЬКО widget.callback
            # источника и не зовёт node.onWidgetChanged — превью обязано
            # обновиться и по этому каналу.
            vue_path = await cdp.eval(
                "(() => { const l = window.__probeLoad;"
                " const w = (l.widgets || []).find((x) => x.name === 'image');"
                " const prev = w.value; w.value = 'example.png';"
                " app.nodeOutputs[String(l.id)] = { images: [{ filename: 'example.png', subfolder: '', type: 'input' }] };"
                " if (typeof w.callback === 'function') w.callback(w.value, app.canvas, l);"
                " return { prev, next: w.value, hasCallback: typeof w.callback === 'function' }; })()"
            )
            await asyncio.sleep(1.0)
            vue_state = await cdp.eval(STATE_JS)
            check(
                "Nodes 2.0: смена картинки через widget.callback обновляет превью",
                bool(vue_path.get("hasCallback"))
                and "example.png" in (vue_state.get("src") or ""),
                {"path": vue_path, "src": vue_state.get("src")},
            )

            # Перезаливка файла с тем же именем не меняет ключ превью — картинку
            # всё равно надо перечитать (кэш-бастер), иначе остаётся старая.
            same_before = vue_state.get("src") or ""
            await cdp.eval(
                "(() => { const l = window.__probeLoad;"
                " const w = (l.widgets || []).find((x) => x.name === 'image');"
                " w.value = 'example.png';"
                " app.nodeOutputs[String(l.id)] = { images: [{ filename: 'example.png', subfolder: '', type: 'input' }] };"
                " if (typeof w.callback === 'function') w.callback(w.value, app.canvas, l);"
                " return 1; })()"
            )
            await asyncio.sleep(1.0)
            same_state = await cdp.eval(STATE_JS)
            check(
                "перезаливка того же имени файла перечитывает картинку",
                bool(same_before) and (same_state.get("src") or "") != same_before,
                {"before": same_before, "after": same_state.get("src")},
            )

            errors = await cdp.eval("window.__probeErrors")
            real = [e for e in (errors or []) if "example.png" not in e and "favicon" not in e]
            check("консоль без ошибок", not real, real[:3])
    finally:
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
