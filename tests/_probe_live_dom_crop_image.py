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


async def drag_mouse(cdp: CDP, x: float, y: float, dx: float, dy: float, steps: int = 10, modifiers: int = 0) -> None:
    await cdp.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y, "modifiers": modifiers})
    await cdp.call(
        "Input.dispatchMouseEvent",
        {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1, "buttons": 1, "modifiers": modifiers},
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
                "modifiers": modifiers,
            },
        )
        await asyncio.sleep(0.04)
    await cdp.call(
        "Input.dispatchMouseEvent",
        {"type": "mouseReleased", "x": x + dx, "y": y + dy, "button": "left", "clickCount": 1, "buttons": 0, "modifiers": modifiers},
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
  // Задать кадр так, как это делает воркфлоу: пишем crop_rect (+ output_size)
  // и просим редактор перечитать состояние. Тестовых хуков в редакторе нет.
  window.__setFrame = (frac, manual) => {
    const n = window.__probeCrop;
    const cr = (n.widgets || []).find((x) => x.name === 'crop_rect');
    const os = (n.widgets || []).find((x) => x.name === 'output_size');
    if (cr) cr.value = JSON.stringify(frac);
    if (os) os.value = manual == null ? '' : String(manual);
    n.__deggCrop.refresh();
    return n.__deggCrop.state.frac;
  };
  window.__widget = (name) =>
    (window.__probeCrop.widgets || []).find((x) => x.name === name) || null;
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
                ".filter((n) => n !== 'preview' && n !== 'crop_rect' && n !== 'output_size'))()"
            )
            expected_order = [
                "lock_ratio",
                "center_x",
                "center_y",
                "frame_w",
                "frame_h",
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
            check(
                "плашка в одну строку (размер и МП через отступ в 2 пробела)",
                "\n" not in (after.get("badge") or "")
                and "px  " in (after.get("badge") or "")
                and " MP" in (after.get("badge") or ""),
                after.get("badge"),
            )
            # Подключение картинки = сброс под неё: результат = размер картинки.
            check(
                "подключение картинки: результат = размер картинки (768 × 768)",
                "768 × 768 px" in badge,
                badge,
            )
            reset_frac = after.get("frac") or {}
            check(
                "подключение картинки: рамка по всему изображению",
                abs(reset_frac.get("x", 1)) < 1e-6 and abs(reset_frac.get("y", 1)) < 1e-6
                and abs(reset_frac.get("w", 0) - 1) < 1e-6 and abs(reset_frac.get("h", 0) - 1) < 1e-6,
                reset_frac,
            )
            # Дальше — режим «по мегапикселям»: ставим 2.0 явно.
            await cdp.eval(
                "(() => { const n = window.__probeCrop; const m = n.widgets.find((w) => w.name === 'megapixels');"
                " m.value = 2.0; m.callback && m.callback(2.0, app.canvas, n); return true; })()"
            )
            await asyncio.sleep(0.35)
            after = await cdp.eval(STATE_JS)
            badge = after.get("badge") or ""
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
            # Пропорцию считаем по frac: при сжатой сцене frameBox округляется
            # до целых CSS-пикселей (2×2) и теряет точность.
            fb = wide.get("frac") or {}
            wide_natural = wide.get("natural") or [1, 1]
            fb_ratio = (fb.get("w", 0) * wide_natural[0]) / max(1e-6, fb.get("h", 1) * wide_natural[1])
            check("смена пропорции дала 16:9", abs(fb_ratio - 16 / 9) < 0.01, {"frac": fb, "ratio": fb_ratio})
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

            # ── кнопки бара: «По центру» / «Вписать» / «Сбросить» ────────
            CLICK = "(() => { const b = window.__probeCrop.__deggCrop.widget"
            CLICK += ".querySelectorAll('.degg-crop__btn'); return b[%d] ? (b[%d].click(), true) : false; })()"

            await cdp.eval(
                "(() => { const n = window.__probeCrop; window.__setFrame({x:0.1,y:0.7,w:0.4,h:0.2}); return n.__deggCrop.state.frac; })()"
            )
            await asyncio.sleep(0.2)
            check("кнопка «По центру» найдена", bool(await cdp.eval(CLICK % (1, 1))))
            await asyncio.sleep(0.2)
            centered = await cdp.eval(STATE_JS)
            cf = centered.get("frac") or {}
            check(
                "«По центру»: размер и пропорции не меняются",
                abs(cf.get("w", 0) - 0.4) < 1e-6 and abs(cf.get("h", 0) - 0.2) < 1e-6,
                cf,
            )
            check(
                "«По центру»: центр рамки совпал с центром изображения",
                abs(cf.get("x", -1) - 0.3) < 1e-6 and abs(cf.get("y", -1) - 0.4) < 1e-6,
                cf,
            )

            await cdp.eval(
                "(() => { const n = window.__probeCrop; window.__setFrame({x:0.1,y:0.4,w:0.25,h:0.1}); return n.__deggCrop.state.frac; })()"
            )
            await asyncio.sleep(0.2)
            check("кнопка «Вписать» найдена", bool(await cdp.eval(CLICK % (0, 0))))
            await asyncio.sleep(0.2)
            fitted = await cdp.eval(STATE_JS)
            ff = fitted.get("frac") or {}
            check(
                "«Вписать»: маленькая рамка увеличена до границ",
                abs(ff.get("w", 0) - 1) < 1e-6 and abs(ff.get("h", 0) - 0.4) < 1e-6,
                ff,
            )
            check(
                "«Вписать»: масштаб равномерный, центр сохранён",
                abs(ff.get("x", -1)) < 1e-6 and abs(ff.get("y", -1) - 0.3) < 1e-6,
                ff,
            )

            await cdp.eval(
                "(() => { const n = window.__probeCrop; window.__setFrame({x:-0.5,y:0.6,w:1.5,h:0.5}); return n.__deggCrop.state.frac; })()"
            )
            await asyncio.sleep(0.2)
            await cdp.eval(CLICK % (0, 0))
            await asyncio.sleep(0.2)
            huge = await cdp.eval(STATE_JS)
            hf = huge.get("frac") or {}
            check(
                "«Вписать»: большая рамка уменьшена (и вверх, и вниз)",
                abs(hf.get("w", 0) - 1) < 1e-6 and abs(hf.get("h", 0) - 1 / 3) < 1e-6,
                hf,
            )
            check(
                "«Вписать»: большая рамка отцентрирована",
                abs(hf.get("x", -1)) < 1e-6 and abs(hf.get("y", -1) - 1 / 3) < 1e-6,
                hf,
            )

            await cdp.eval(
                "(() => { const n = window.__probeCrop;"
                " const a = n.widgets.find((x) => x.name === 'aspect_ratio'); a.value = '16:9'; a.callback && a.callback(a.value);"
                " const l = n.widgets.find((x) => x.name === 'lock_ratio'); l.value = true; l.callback && l.callback(true);"
                " window.__setFrame({x:0.2,y:0.2,w:0.3,h:0.3}); return true; })()"
            )
            await asyncio.sleep(0.3)
            check("кнопка «Сбросить» найдена", bool(await cdp.eval(CLICK % (2, 2))))
            await asyncio.sleep(0.3)
            reset = await cdp.eval(STATE_JS)
            rf = reset.get("frac") or {}
            check(
                "«Сбросить»: рамка ровно по изображению",
                abs(rf.get("x", -1)) < 1e-6
                and abs(rf.get("y", -1)) < 1e-6
                and abs(rf.get("w", 0) - 1) < 1e-6
                and abs(rf.get("h", 0) - 1) < 1e-6,
                rf,
            )
            reset_widgets = await cdp.eval(
                "(() => { const n = window.__probeCrop;"
                " return { aspect: n.widgets.find((x) => x.name === 'aspect_ratio').value,"
                " lock: n.widgets.find((x) => x.name === 'lock_ratio').value }; })()"
            )
            check("«Сбросить»: пропорции → «Свободные»", reset_widgets.get("aspect") == "free", reset_widgets)
            check(
                "«Сбросить»: фиксация включена на пропорциях изображения",
                reset_widgets.get("lock") is True and reset_widgets.get("aspect") == "free",
                reset_widgets,
            )
            img_box = reset.get("imgBox") or {}
            frame_box = reset.get("frameBox") or {}
            check(
                "«Сбросить»: рамка на сцене занимает всё изображение",
                abs(frame_box.get("x", -1) - img_box.get("x", -2)) < 1.5
                and abs(frame_box.get("y", -1) - img_box.get("y", -2)) < 1.5
                and abs(frame_box.get("w", 0) - img_box.get("w", -1)) < 1.5
                and abs(frame_box.get("h", 0) - img_box.get("h", -1)) < 1.5,
                {"frame": frame_box, "image": img_box},
            )

            # Возвращаем режим результата, который ожидают проверки ниже
            # (наш «Сбросить» честно переводит пропорции в «Свободные»).
            await cdp.eval(
                "(() => { const n = window.__probeCrop; const a = n.widgets.find((x) => x.name === 'aspect_ratio');"
                " a.value = '16:9'; a.callback && a.callback(a.value);"
                " const l = n.widgets.find((x) => x.name === 'lock_ratio'); l.value = true; l.callback && l.callback(true);"
                " return true; })()"
            )
            await asyncio.sleep(0.3)

            # ── фиксация текущих пропорций, координаты, Shift, подсказка ──
            # «Зафиксировано» держит пропорции, которые у рамки есть СЕЙЧАС,
            # а не подставляет выбранный пресет.
            await cdp.eval(
                "(() => { const n = window.__probeCrop, e = n.__deggCrop;"
                " const a = n.widgets.find((x) => x.name === 'aspect_ratio');"
                " const l = n.widgets.find((x) => x.name === 'lock_ratio');"
                " a.value = 'free'; a.callback && a.callback('free');"
                " window.__setFrame({ x: 0.1, y: 0.1, w: 0.4, h: 0.25 });"
                " l.value = false; l.callback && l.callback(false);"
                " l.value = true; l.callback && l.callback(true); return true; })()"
            )
            await asyncio.sleep(0.25)
            locked_free = await cdp.eval(STATE_JS)
            lf = locked_free.get("frac") or {}
            ln = locked_free.get("natural") or [1, 1]
            lf_ratio = (lf.get("w", 0) * ln[0]) / max(1e-6, lf.get("h", 1) * ln[1])
            check(
                "«Зафиксировано» сохраняет текущие пропорции рамки (1.6)",
                abs(lf.get("w", 0) - 0.4) < 1e-6 and abs(lf.get("h", 0) - 0.25) < 1e-6 and abs(lf_ratio - 1.6) < 0.01,
                {"frac": lf, "ratio": lf_ratio},
            )
            lock_state = await cdp.eval(
                "(() => { const n = window.__probeCrop; return {a: n.widgets.find(x=>x.name==='aspect_ratio').value,"
                " l: n.widgets.find(x=>x.name==='lock_ratio').value}; })()"
            )
            check(
                "фиксация текущих пропорций не трогает список пропорций (остаётся free)",
                lock_state.get("a") == "free" and lock_state.get("l") is True,
                lock_state,
            )

            # Выбор пресета применяет его и включает «Зафиксировано».
            await cdp.eval(
                "(() => { const n = window.__probeCrop; const a = n.widgets.find((x) => x.name === 'aspect_ratio');"
                " a.value = '4:3'; a.callback && a.callback(a.value); return true; })()"
            )
            await asyncio.sleep(0.25)
            preset = await cdp.eval(STATE_JS)
            pf = preset.get("frac") or {}
            pn = preset.get("natural") or [1, 1]
            pf_ratio = (pf.get("w", 0) * pn[0]) / max(1e-6, pf.get("h", 1) * pn[1])
            preset_state = await cdp.eval(
                "(() => window.__probeCrop.widgets.find(x=>x.name==='lock_ratio').value)()"
            )
            check("выбор пресета включает замок", preset_state is True, preset_state)
            check("выбор пресета применяет пропорцию 4:3", abs(pf_ratio - 4 / 3) < 0.01, {"frac": pf, "ratio": pf_ratio})

            # «Свободные» снимает замок.
            await cdp.eval(
                "(() => { const n = window.__probeCrop; const a = n.widgets.find((x) => x.name === 'aspect_ratio');"
                " a.value = 'free'; a.callback && a.callback(a.value); return true; })()"
            )
            await asyncio.sleep(0.2)
            free_state = await cdp.eval(
                "(() => window.__probeCrop.widgets.find(x=>x.name==='lock_ratio').value)()"
            )
            check("«Свободные» выключает замок", free_state is False, free_state)

            # Координаты: центр рамки относительно центра изображения, px.
            await cdp.eval(
                "(() => window.__setFrame({ x: 0.1, y: 0.2, w: 0.4, h: 0.2 }))()"
            )
            await asyncio.sleep(0.2)
            coords_before = await cdp.eval(
                "(() => { const e = window.__probeCrop.__deggCrop;"
                " return { x: window.__widget('center_x').value, y: window.__widget('center_y').value }; })()"
            )
            coord_natural = moved_natural = (await cdp.eval(STATE_JS)).get("natural") or [1, 1]
            exp_x = round((0.1 + 0.4 / 2 - 0.5) * coord_natural[0])
            exp_y = round((0.2 + 0.2 / 2 - 0.5) * coord_natural[1])
            check(
                "координаты показывают смещение центра рамки (px)",
                abs(int(coords_before.get("x", 0)) - exp_x) <= 1
                and abs(int(coords_before.get("y", 0)) - exp_y) <= 1,
                {"got": coords_before, "expected": [exp_x, exp_y]},
            )
            # Правка нативного поля: значение → callback виджета → рамка едет.
            await cdp.eval(
                "(() => { const e = window.__probeCrop.__deggCrop;"
                " window.__widget('center_x').value = 100; window.__widget('center_y').value = -50;"
                " window.__widget('center_x').callback && window.__widget('center_x').callback(100, app.canvas, window.__probeCrop);"
                " window.__widget('center_y').callback && window.__widget('center_y').callback(-50, app.canvas, window.__probeCrop);"
                " return true; })()"
            )
            await asyncio.sleep(0.2)
            moved_coords = await cdp.eval(STATE_JS)
            mc = moved_coords.get("frac") or {}
            mn = moved_coords.get("natural") or [1, 1]
            mc_offset = (
                (mc.get("x", 0) + mc.get("w", 0) / 2 - 0.5) * mn[0],
                (mc.get("y", 0) + mc.get("h", 0) / 2 - 0.5) * mn[1],
            )
            check(
                "ввод координат ставит центр рамки в заданную точку",
                abs(mc_offset[0] - 100) < 1.5 and abs(mc_offset[1] + 50) < 1.5,
                {"offset": mc_offset, "frac": mc},
            )

            # ── ручной размер РЕЗУЛЬТАТА (ширина/высота в пикселях) ────────
            # Поля задают ИТОГОВЫЙ размер картинки: 1000×1000 → и плашка, и
            # выходы width/height показывают 1000×1000 (кратность учитывается).
            await cdp.eval(
                "(() => { const n = window.__probeCrop, e = n.__deggCrop;"
                " const a = n.widgets.find((x) => x.name === 'aspect_ratio');"
                " a.value = 'free'; a.callback && a.callback(a.value);"
                " window.__setFrame({ x: 0.1, y: 0.1, w: 0.5, h: 0.25 });"
                " return true; })()"
            )
            await asyncio.sleep(0.3)
            await cdp.eval(
                "(() => { const n = window.__probeCrop, e = n.__deggCrop;"
                " window.__widget('frame_w').value = 1000; window.__widget('frame_w').callback && window.__widget('frame_w').callback(1000, app.canvas, n);"
                " window.__widget('frame_h').value = 1000; window.__widget('frame_h').callback && window.__widget('frame_h').callback(1000, app.canvas, n);"
                " return true; })()"
            )
            await asyncio.sleep(0.35)
            manual = await cdp.eval(STATE_JS)
            mb = manual.get("badge") or ""
            check(
                "ручной размер: плашка показывает введённый размер (1000 × 1000)",
                "1000 × 1000 px" in mb,
                mb,
            )
            check(
                "ручной размер: поля показывают введённый размер",
                await cdp.eval(
                    "(() => { const e = window.__probeCrop.__deggCrop;"
                    " return { w: window.__widget('frame_w').value, h: window.__widget('frame_h').value }; })()"
                )
                == {"w": 1000, "h": 1000},
                await cdp.eval(
                    "(() => { const e = window.__probeCrop.__deggCrop;"
                    " return { w: window.__widget('frame_w').value, h: window.__widget('frame_h').value }; })()"
                ),
            )
            mp_manual = await cdp.eval(
                "(() => window.__probeCrop.widgets.find((w) => w.name === 'megapixels').value)()"
            )
            check(
                "ручной размер: мегапиксели показывают эквивалент результата",
                abs(float(mp_manual) - (1000 * 1000) / (1024 * 1024)) < 0.06,
                mp_manual,
            )
            mfrac = manual.get("frac") or {}
            mnat = manual.get("natural") or [1, 1]
            check(
                "ручной размер: пропорции кадра подстроились под результат (1:1)",
                abs(mfrac.get("w", 0) * mnat[0] - mfrac.get("h", 0) * mnat[1]) < 2,
                mfrac,
            )
            stored = await cdp.eval(
                "(() => { const e = window.__probeCrop.__deggCrop; return window.__widget('output_size').value; })()"
            )
            check(
                "ручной размер: сохранён в скрытом output_size",
                "1000" in str(stored),
                stored,
            )

            # Кратность учитывается: 1333 × 750 при кратности 8 → 1336 × 752.
            await cdp.eval(
                "(() => { const n = window.__probeCrop, e = n.__deggCrop;"
                " window.__widget('frame_w').value = 1333; window.__widget('frame_w').callback && window.__widget('frame_w').callback(1333, app.canvas, n);"
                " return true; })()"
            )
            await asyncio.sleep(0.3)
            snapped = await cdp.eval(STATE_JS)
            sw = await cdp.eval(
                "(() => { const e = window.__probeCrop.__deggCrop; return window.__widget('frame_w').value; })()"
            )
            check(
                "ручной размер: кратность учитывается (1333 → 1336)",
                int(sw) == 1336 and "1336 ×" in (snapped.get("badge") or ""),
                {"field": sw, "badge": snapped.get("badge")},
            )

            # Правка мегапикселей возвращает режим «по мегапикселям».
            # Кадр сейчас 1:1 и ручной размер с ним согласован — сверка
            # устаревшего размера его не сбросит.
            await cdp.eval(
                "(() => { const n = window.__probeCrop; const m = n.widgets.find((w) => w.name === 'megapixels');"
                " m.value = 2.0; m.callback && m.callback(2.0, app.canvas, n); return true; })()"
            )
            await asyncio.sleep(0.3)
            back = await cdp.eval(STATE_JS)
            bw = await cdp.eval(
                "(() => { const e = window.__probeCrop.__deggCrop; return window.__widget('frame_w').value; })()"
            )
            bfrac = back.get("frac") or {}
            bnat = back.get("natural") or [1, 1]
            exp_w2, exp_h2 = ci.output_size(
                bfrac.get("w", 0) * bnat[0], bfrac.get("h", 0) * bnat[1], 2.0, 8
            )
            check(
                "правка мегапикселей возвращает размер по мегапикселям",
                int(bw) == exp_w2 and f"{exp_w2} × {exp_h2} px" in (back.get("badge") or ""),
                {"field": bw, "expected": [exp_w2, exp_h2], "badge": back.get("badge")},
            )

            # Свободный drag меняет пропорции кадра: ручной размер обязан
            # последовать за ними (ширина сохраняется, высота следует), иначе
            # результат растянулся бы.
            await cdp.eval(
                "(() => { const n = window.__probeCrop;"
                " const a = n.widgets.find((x) => x.name === 'aspect_ratio');"
                " a.value = 'free'; a.callback && a.callback(a.value);"
                " const w = n.widgets.find((x) => x.name === 'frame_w');"
                " const h = n.widgets.find((x) => x.name === 'frame_h');"
                " w.value = 1000; w.callback && w.callback(1000, app.canvas, n);"
                " h.value = 1000; h.callback && h.callback(1000, app.canvas, n);"
                " return true; })()"
            )
            await asyncio.sleep(0.3)
            drag_east = await cdp.eval(
                "(() => { const r = window.__probeCrop.__deggCrop.widget"
                ".querySelector('.degg-crop__h[data-h=\"e\"]').getBoundingClientRect();"
                " return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()"
            )
            await drag_mouse(cdp, drag_east["x"], drag_east["y"], 40, 0)
            await asyncio.sleep(0.35)
            after_drag = await cdp.eval(STATE_JS)
            dfrac = after_drag.get("frac") or {}
            dnat = after_drag.get("natural") or [1, 1]
            dw = await cdp.eval("(() => window.__widget('frame_w').value)()")
            dh = await cdp.eval("(() => window.__widget('frame_h').value)()")
            check(
                "свободный drag: ширина ручного размера сохраняется",
                int(dw) == 1000,
                {"w": dw, "h": dh},
            )
            crop_ratio_px = (dfrac.get("w", 0) * dnat[0]) / max(1e-6, dfrac.get("h", 0) * dnat[1])
            result_ratio_px = int(dw) / max(1, int(dh))
            check(
                "свободный drag: пропорции результата следуют за пропорциями кадра",
                abs(result_ratio_px - crop_ratio_px) / crop_ratio_px < 0.02,
                {"frac": dfrac, "result": [dw, dh], "crop_ratio": crop_ratio_px},
            )

            # 16:9 + 2 МП → поля показывают итоговый размер 1928 × 1088.
            # Мегапиксели в ручном режиме были подстроены под ручной размер —
            # ставим 2.0 явно.
            await cdp.eval(
                "(() => { const n = window.__probeCrop; const m = n.widgets.find((w) => w.name === 'megapixels');"
                " m.value = 2.0; m.callback && m.callback(2.0, app.canvas, n); return true; })()"
            )
            await asyncio.sleep(0.2)
            await cdp.eval(
                "(() => { const n = window.__probeCrop; const a = n.widgets.find((x) => x.name === 'aspect_ratio');"
                " a.value = '16:9'; a.callback && a.callback(a.value); return true; })()"
            )
            await asyncio.sleep(0.35)
            wide_out = await cdp.eval(STATE_JS)
            ww = await cdp.eval(
                "(() => { const e = window.__probeCrop.__deggCrop; return window.__widget('frame_w').value; })()"
            )
            wh = await cdp.eval(
                "(() => { const e = window.__probeCrop.__deggCrop; return window.__widget('frame_h').value; })()"
            )
            check(
                "16:9 и 2 МП: поля показывают итоговый размер (1928 × 1088)",
                int(ww) == 1928 and int(wh) == 1088 and "1928 × 1088 px" in (wide_out.get("badge") or ""),
                {"w": ww, "h": wh, "badge": wide_out.get("badge")},
            )

            # Обратное направление: ручной размер активен, выбираем пресет
            # пропорций → ручной режим сбрасывается, поля = размер по MP.
            await cdp.eval(
                "(() => { const n = window.__probeCrop, e = n.__deggCrop;"
                " window.__widget('frame_w').value = 1200; window.__widget('frame_w').callback && window.__widget('frame_w').callback(1200, app.canvas, n);"
                " return true; })()"
            )
            await asyncio.sleep(0.3)
            manual_on = await cdp.eval(STATE_JS)
            check(
                "ручной размер активен (поле = введённое)",
                int(await cdp.eval("(() => window.__widget('frame_w').value)()")) == 1200,
                manual_on.get("badge"),
            )
            await cdp.eval(
                "(() => { const n = window.__probeCrop; const a = n.widgets.find((x) => x.name === 'aspect_ratio');"
                " a.value = '4:3'; a.callback && a.callback(a.value); return true; })()"
            )
            await asyncio.sleep(0.35)
            after_preset = await cdp.eval(STATE_JS)
            pw = await cdp.eval("(() => window.__widget('frame_w').value)()")
            ph = await cdp.eval("(() => window.__widget('frame_h').value)()")
            check(
                "выбор пресета сбрасывает ручной размер: поля = размер по мегапикселям",
                abs((int(pw) / max(1, int(ph))) - 4 / 3) < 0.02
                and int(pw) % 8 == 0
                and int(ph) % 8 == 0
                and f"{int(pw)} × {int(ph)} px" in (after_preset.get("badge") or ""),
                {"w": pw, "h": ph, "badge": after_preset.get("badge")},
            )

            # РЕГРЕССИЯ (случай пользователя): ручной размер остался от прошлого
            # воркфлоу (1:1), а выбраны 16:9 и 2 МП. Пропорции кадра и ручного
            # результата разошлись → ручной режим сбрасывается, иначе MP и пресет
            # молча игнорируются, а кадр растягивается.
            await cdp.eval(
                "(() => { const n = window.__probeCrop, e = n.__deggCrop;"
                " const a = n.widgets.find((x) => x.name === 'aspect_ratio');"
                " const m = n.widgets.find((x) => x.name === 'megapixels');"
                " m.value = 2.0; m.callback && m.callback(2.0, app.canvas, n);"
                " a.value = '16:9'; a.callback && a.callback(a.value);"
                " window.__setFrame(e.state.frac, '1448,1448');"
                " return true; })()"
            )
            await asyncio.sleep(0.35)
            stale = await cdp.eval(STATE_JS)
            sw2 = await cdp.eval("(() => window.__widget('frame_w').value)()")
            sh2 = await cdp.eval("(() => window.__widget('frame_h').value)()")
            check(
                "устаревший ручной размер не перебивает пресет и мегапиксели",
                int(sw2) == 1928 and int(sh2) == 1088
                and "1928 × 1088 px" in (stale.get("badge") or ""),
                {"w": sw2, "h": sh2, "badge": stale.get("badge")},
            )
            sfrac = stale.get("frac") or {}
            snat = stale.get("natural") or [1, 1]
            check(
                "после сброса ручного режима пропорции кадра = пропорциям результата (16:9)",
                abs((sfrac.get("w", 0) * snat[0]) / max(1e-6, sfrac.get("h", 0) * snat[1]) - 16 / 9) < 0.01,
                sfrac,
            )

            # Возвращаем свободный режим и 2 МП — дальше идут проверки Shift
            # и контрольный случай «16:9, 2 МП → 1928 × 1088».
            await cdp.eval(
                "(() => { const n = window.__probeCrop; const a = n.widgets.find((x) => x.name === 'aspect_ratio');"
                " a.value = 'free'; a.callback && a.callback(a.value);"
                " const m = n.widgets.find((x) => x.name === 'megapixels');"
                " m.value = 2.0; m.callback && m.callback(2.0, app.canvas, n); return true; })()"
            )
            await asyncio.sleep(0.35)

            # Shift + сторона в свободном режиме: пропорции сохраняются,
            # «Зафиксировано» не включается.
            await cdp.eval(
                "(() => window.__setFrame({ x: 0.2, y: 0.2, w: 0.4, h: 0.2 }))()"
            )
            await asyncio.sleep(0.2)
            east = await cdp.eval(
                "(() => { const r = window.__probeCrop.__deggCrop.widget"
                ".querySelector('.degg-crop__h[data-h=\"e\"]').getBoundingClientRect();"
                " return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()"
            )
            await drag_mouse(cdp, east["x"], east["y"], 40, 0, modifiers=8)
            await asyncio.sleep(0.3)
            shift_resize = await cdp.eval(STATE_JS)
            sr = shift_resize.get("frac") or {}
            sn = shift_resize.get("natural") or [1, 1]
            sr_ratio = (sr.get("w", 0) * sn[0]) / max(1e-6, sr.get("h", 1) * sn[1])
            shift_lock = await cdp.eval(
                "(() => window.__probeCrop.widgets.find(x=>x.name==='lock_ratio').value)()"
            )
            check(
                "Shift + край в «Свободно» сохраняет пропорции рамки",
                abs(sr_ratio - 2) < 0.02 and sr.get("w", 0) > 0.4,
                {"frac": sr, "ratio": sr_ratio},
            )
            check("Shift-ресайз не включает «Зафиксировано»", shift_lock is False, shift_lock)

            # Shift + перетаскивание внутри: движение строго по оси.
            await cdp.eval(
                "(() => window.__setFrame({ x: 0.1, y: 0.1, w: 0.8, h: 0.6 }))()"
            )
            await asyncio.sleep(0.2)
            inside = await cdp.eval(
                "(() => { const r = window.__probeCrop.__deggCrop.widget"
                ".querySelector('.degg-crop__frame').getBoundingClientRect();"
                " return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()"
            )
            await drag_mouse(cdp, inside["x"], inside["y"], 50, 40, modifiers=8)
            await asyncio.sleep(0.3)
            axis = await cdp.eval(STATE_JS)
            af = axis.get("frac") or {}
            check(
                "Shift при перемещении держит одну ось",
                abs(af.get("y", 0) - 0.1) < 0.02 and abs(af.get("x", 0) - 0.1) > 0.02,
                af,
            )

            # «Зафиксировано» применяет ЗАХВАЧЕННЫЕ пропорции при ресайзе:
            # рамку тянем за край, соотношение сторон не должно измениться.
            await cdp.eval(
                "(() => { const n = window.__probeCrop, e = n.__deggCrop;"
                " const a = n.widgets.find((x) => x.name === 'aspect_ratio');"
                " const l = n.widgets.find((x) => x.name === 'lock_ratio');"
                " a.value = 'free'; a.callback && a.callback('free');"
                " l.value = false; l.callback && l.callback(false);"
                " window.__setFrame({ x: 0.1, y: 0.1, w: 0.4, h: 0.25 });"
                " l.value = true; l.callback && l.callback(true); return true; })()"
            )
            await asyncio.sleep(0.25)
            lock_east = await cdp.eval(
                "(() => { const r = window.__probeCrop.__deggCrop.widget"
                ".querySelector('.degg-crop__h[data-h=\"e\"]').getBoundingClientRect();"
                " return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()"
            )
            await drag_mouse(cdp, lock_east["x"], lock_east["y"], 40, 0)
            await asyncio.sleep(0.3)
            lock_resize = await cdp.eval(STATE_JS)
            lr = lock_resize.get("frac") or {}
            ln2 = lock_resize.get("natural") or [1, 1]
            lr_ratio = (lr.get("w", 0) * ln2[0]) / max(1e-6, lr.get("h", 1) * ln2[1])
            check(
                "«Зафиксировано» держит захваченные пропорции при ресайзе",
                abs(lr_ratio - 1.6) < 0.02 and lr.get("w", 0) > 0.4,
                {"frac": lr, "ratio": lr_ratio},
            )

            # Рамка шире изображения: сцена увеличивается, drag не должен
            # «разгонять» рамку (обратная связь через меняющийся масштаб).
            await cdp.eval(
                "(() => { const n = window.__probeCrop, e = n.__deggCrop;"
                " const a = n.widgets.find((x) => x.name === 'aspect_ratio');"
                " a.value = '16:9'; a.callback && a.callback('16:9');"
                " window.__setFrame({ x: 0.2, y: 0.05, w: 1.6, h: 0.9 }); return true; })()"
            )
            await asyncio.sleep(0.3)
            wide_west = await cdp.eval(
                "(() => { const r = window.__probeCrop.__deggCrop.widget"
                ".querySelector('.degg-crop__h[data-h=\"w\"]').getBoundingClientRect();"
                " return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()"
            )
            await drag_mouse(cdp, wide_west["x"], wide_west["y"], -120, 0)
            await asyncio.sleep(0.35)
            runaway = await cdp.eval(STATE_JS)
            rr = runaway.get("frac") or {}
            rn = runaway.get("natural") or [1, 1]
            rr_ratio = (rr.get("w", 0) * rn[0]) / max(1e-6, rr.get("h", 1) * rn[1])
            check(
                "drag за пределы изображения не разгоняет рамку",
                rr.get("w", 0) < 4 and rr.get("h", 0) < 4,
                {"frac": rr},
            )
            check(
                "drag рамки шире изображения сохраняет пропорцию",
                abs(rr_ratio - 16 / 9) < 0.02,
                {"frac": rr, "ratio": rr_ratio},
            )

            # Иконка «?»: подсказка открывается и закрывается, текст не пустой.
            help_closed = await cdp.eval(
                "(() => window.__probeCrop.__deggCrop.widget.querySelector('.degg-crop__helpbox').style.display)()"
            )
            await cdp.eval(
                "(() => { window.__probeCrop.__deggCrop.widget.querySelector('.degg-crop__help').click(); return true; })()"
            )
            await asyncio.sleep(0.15)
            help_open = await cdp.eval(
                "(() => { const b = window.__probeCrop.__deggCrop.widget.querySelector('.degg-crop__helpbox');"
                " return { display: b.style.display, text: b.textContent }; })()"
            )
            check("подсказка «?» закрыта по умолчанию", help_closed in ("", "none"), help_closed)
            help_place = await cdp.eval(
                "(() => { const n = window.__probeCrop;"
                " const btn = n.__deggCrop.widget.querySelector('.degg-crop__help');"
                " const stage = n.__deggCrop.widget.querySelector('.degg-crop__stage');"
                " const b = btn.getBoundingClientRect(); const s = stage.getBoundingClientRect();"
                " return { inStage: stage.contains(btn), right: s.right - b.right, top: b.top - s.top }; })()"
            )
            check(
                "кнопка «?» стоит в правом верхнем углу превью",
                help_place.get("inStage") is True
                and abs(help_place.get("right", 0) - 8) < 2
                and abs(help_place.get("top", 0) - 6) < 2,
                help_place,
            )
            check(
                "подсказка «?» открывается и описывает Shift/координаты",
                help_open.get("display") == "block"
                and "Shift" in (help_open.get("text") or "")
                and "X/Y" in (help_open.get("text") or ""),
                {"display": help_open.get("display"), "text": (help_open.get("text") or "")[:120]},
            )
            await cdp.eval(
                "(() => { window.__probeCrop.__deggCrop.widget.querySelector('.degg-crop__help').click(); return true; })()"
            )
            await asyncio.sleep(0.1)
            check(
                "подсказка «?» закрывается повторным кликом",
                await cdp.eval("(() => window.__probeCrop.__deggCrop.widget.querySelector('.degg-crop__helpbox').style.display)()") == "none",
            )

            # Возвращаем 16:9 для проверок заливки/плашки ниже.
            await cdp.eval(
                "(() => { const n = window.__probeCrop; const a = n.widgets.find((x) => x.name === 'aspect_ratio');"
                " a.value = '16:9'; a.callback && a.callback(a.value); return true; })()"
            )
            await asyncio.sleep(0.3)

            # ── язык: смена Comfy.Locale на лету ─────────────────────────
            lang0 = await cdp.eval(
                "(() => { const g = app.extensionManager.setting.get;"
                " window.__probeLocaleGet = g;"
                " window.__probeLocale = g('Comfy.Locale') || 'en';"
                " return { locale: window.__probeLocale,"
                " texts: [...window.__probeCrop.__deggCrop.widget.querySelectorAll('.degg-crop__btn')].map((b) => b.textContent),"
                " lockOn: window.__probeCrop.widgets.find((x) => x.name === 'lock_ratio').options.on }; })()"
            )
            await cdp.eval(
                "(() => { app.extensionManager.setting.get = (id) => (id === 'Comfy.Locale' ? 'en' : window.__probeLocaleGet(id));"
                " app.ui.settings.dispatchChange('Comfy.Locale', 'en', window.__probeLocale); return true; })()"
            )
            await asyncio.sleep(0.2)
            en = await cdp.eval(
                "(() => ({ texts: [...window.__probeCrop.__deggCrop.widget.querySelectorAll('.degg-crop__btn')].map((b) => b.textContent),"
                " lockOn: window.__probeCrop.widgets.find((x) => x.name === 'lock_ratio').options.on }))()"
            )
            check("язык на лету → EN-кнопки", en.get("texts") == ["Fit", "Center", "Reset"], en.get("texts"))
            check("язык на лету → замок EN «Locked»", en.get("lockOn") == "Locked", en.get("lockOn"))

            await cdp.eval(
                "(() => { app.extensionManager.setting.get = window.__probeLocaleGet;"
                " app.ui.settings.dispatchChange('Comfy.Locale', window.__probeLocale, 'en'); return true; })()"
            )
            await asyncio.sleep(0.2)
            back = await cdp.eval(
                "(() => [...window.__probeCrop.__deggCrop.widget.querySelectorAll('.degg-crop__btn')].map((b) => b.textContent))()"
            )
            expect_ru = str(lang0.get("locale", "en")).lower().startswith("ru")
            check(
                "язык на лету → возврат к исходной локали",
                back == (["Вписать", "По центру", "Сбросить"] if expect_ru else ["Fit", "Center", "Reset"]),
                {"locale": lang0.get("locale"), "texts": back},
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

            # ── РЕГРЕСС: НЕКВАДРАТНЫЙ источник ───────────────────────────
            # Пропорции результата считаются по ПИКСЕЛЯМ кадра. На квадратной
            # картинке (example.png 768×768) доли и пиксели совпадают, и ошибка
            # не видна. Поэтому подключаем НАСТОЯЩУЮ неквадратную картинку и
            # задаём кадр долями — в пикселях это будет 16:9.
            NON_SQUARE = "image_flux3_klein_distilled_t2i_00023_.png"
            await cdp.eval(
                "(() => { const l = window.__probeLoad;"
                " const w = (l.widgets || []).find((x) => x.name === 'image');"
                " if (!w) return false;"
                " w.value = '%s';"
                " if (typeof w.callback === 'function') w.callback(w.value, app.canvas, l);"
                " return true; })()" % NON_SQUARE
            )
            natural = [0, 0]
            deadline = time.time() + 25
            while time.time() < deadline:
                natural = await cdp.eval(
                    "(() => { const i = window.__probeCrop.__deggCrop.widget"
                    ".querySelector('.degg-crop__img');"
                    " return i ? [i.naturalWidth, i.naturalHeight] : [0, 0]; })()"
                ) or [0, 0]
                if natural[0] and natural[0] != natural[1]:
                    break
                await asyncio.sleep(0.5)

            if not natural[0] or natural[0] == natural[1]:
                print(
                    "  ВНИМАНИЕ: неквадратная картинка '%s' недоступна — "
                    "регресс «размер по пикселям» пропущен" % NON_SQUARE
                )
            else:
                await cdp.eval(
                    "(() => window.__setFrame({ x: 0.181794, y: 0.183037,"
                    " w: 0.636412, h: 0.633926 }))()"
                )
                await asyncio.sleep(0.35)
                wide_src = await cdp.eval(STATE_JS)
                fw = await cdp.eval("(() => window.__widget('frame_w').value)()")
                fh = await cdp.eval("(() => window.__widget('frame_h').value)()")
                wfrac = wide_src.get("frac") or {}
                # Ожидаемый размер считает PYTHON — тем же кодом, что и сервер,
                # с текущими мегапикселями ноды.
                mp_now = float(await cdp.eval("(() => window.__widget('megapixels').value)()"))
                exp_w, exp_h = ci.output_size(
                    wfrac.get("w", 0) * natural[0],
                    wfrac.get("h", 0) * natural[1],
                    mp_now,
                    8,
                )
                check(
                    "неквадратный источник: размер по ПИКСЕЛЯМ кадра",
                    int(fw) == exp_w and int(fh) == exp_h
                    and f"{exp_w} × {exp_h} px" in (wide_src.get("badge") or ""),
                    {"got": [fw, fh], "expected": [exp_w, exp_h], "badge": wide_src.get("badge")},
                )
                check(
                    "неквадратный источник: доля кадра почти квадратная, но пиксели — 16:9",
                    abs(wfrac.get("w", 0) / max(1e-6, wfrac.get("h", 0)) - 1) < 0.01
                    and abs((wfrac.get("w", 0) * natural[0]) / (wfrac.get("h", 0) * natural[1]) - 16 / 9) < 0.01,
                    {"frac": wfrac, "natural": natural},
                )
                # Возвращаем квадратный источник, чтобы следующие проверки считались как раньше.
                await cdp.eval(
                    "(() => { const l = window.__probeLoad;"
                    " const w = (l.widgets || []).find((x) => x.name === 'image');"
                    " if (!w) return false;"
                    " w.value = 'example.png';"
                    " if (typeof w.callback === 'function') w.callback(w.value, app.canvas, l);"
                    " return true; })()"
                )
                await asyncio.sleep(1.2)

            # ── СБРОС К ИЗОБРАЖЕНИЮ ───────────────────────────────────────
            # 1. Кнопка «Сбросить»: рамка по всему изображению, значения — его.
            await cdp.eval(
                "(() => { const n = window.__probeCrop;"
                " window.__setFrame({ x: 0.2, y: 0.2, w: 0.4, h: 0.4 });"
                " const b = n.__deggCrop.widget.querySelectorAll('.degg-crop__btn')[2];"
                " b.click(); return true; })()"
            )
            await asyncio.sleep(0.35)
            after_reset = await cdp.eval(STATE_JS)
            rfrac = after_reset.get("frac") or {}
            rnat = after_reset.get("natural") or [1, 1]
            rw = await cdp.eval("(() => window.__widget('frame_w').value)()")
            rh = await cdp.eval("(() => window.__widget('frame_h').value)()")
            check(
                "«Сбросить»: рамка по всему изображению",
                abs(rfrac.get("x", 1)) < 1e-6 and abs(rfrac.get("y", 1)) < 1e-6
                and abs(rfrac.get("w", 0) - 1) < 1e-6 and abs(rfrac.get("h", 0) - 1) < 1e-6,
                rfrac,
            )
            check(
                "«Сбросить»: размер результата = размер изображения",
                int(rw) == rnat[0] and int(rh) == rnat[1],
                {"result": [rw, rh], "image": rnat},
            )
            check(
                "«Сбросить»: мегапиксели = площадь изображения",
                abs(
                    float(await cdp.eval("(() => window.__widget('megapixels').value)()"))
                    - (rnat[0] * rnat[1]) / (1024 * 1024)
                )
                < 0.06,
                await cdp.eval("(() => window.__widget('megapixels').value)()"),
            )
            check(
                "«Сбросить»: центр рамки = центр изображения",
                abs(int(await cdp.eval("(() => window.__widget('center_x').value)()"))) <= 1
                and abs(int(await cdp.eval("(() => window.__widget('center_y').value)()"))) <= 1,
            )

            # 2. Новая картинка → тот же сброс (другой файл, тот же размер —
            #    сбрасывает, потому что меняется файл, а не только размер).
            await cdp.eval(
                "(() => { const l = window.__probeLoad;"
                " const w = (l.widgets || []).find((x) => x.name === 'image');"
                " w.value = '%s';"
                " if (typeof w.callback === 'function') w.callback(w.value, app.canvas, l);"
                " return true; })()" % NON_SQUARE
            )
            await asyncio.sleep(2.5)
            after_new_image = await cdp.eval(STATE_JS)
            nfrac = after_new_image.get("frac") or {}
            nnat = after_new_image.get("natural") or [1, 1]
            nw = await cdp.eval("(() => window.__widget('frame_w').value)()")
            check(
                "новая картинка: рамка по всему изображению",
                abs(nfrac.get("x", 1)) < 1e-6 and abs(nfrac.get("y", 1)) < 1e-6
                and abs(nfrac.get("w", 0) - 1) < 1e-6 and abs(nfrac.get("h", 0) - 1) < 1e-6,
                nfrac,
            )
            check(
                "новая картинка: значения результата = значения изображения",
                int(nw) == nnat[0],
                {"result": nw, "image": nnat},
            )

            # 3. Восстановление воркфлоу рамку НЕ сбрасывает: кадр приходит
            #    из crop_rect, и новая картинка не должна его затирать.
            await cdp.eval(
                "(() => { const n = window.__probeCrop;"
                " window.__setFrame({ x: 0.2, y: 0.2, w: 0.4, h: 0.4 });"
                " n.__deggCrop.refresh();"
                " return true; })()"
            )
            await asyncio.sleep(0.6)
            restored = await cdp.eval(STATE_JS)
            sfrac = restored.get("frac") or {}
            check(
                "восстановление воркфлоу: рамка сохраняется (сброса нет)",
                abs(sfrac.get("w", 0) - 0.4) < 1e-6 and abs(sfrac.get("h", 0) - 0.4) < 1e-6,
                sfrac,
            )

            # Настоящий сценарий открытия воркфлою: сначала onConfigure (нода
            # получает сохранённое состояние), потом восстанавливаются связи.
            # Рамка из crop_rect обязана уцелеть — сброс тут только вредит.
            await cdp.eval(
                "(() => { const g = app.graph; const load = window.__probeLoad;"
                " const n2 = LiteGraph.createNode('DeggCropImage');"
                " g.add(n2); n2.pos = [900, 300];"
                " const cr = (n2.widgets || []).find((w) => w.name === 'crop_rect');"
                " if (cr) cr.value = '{\"x\":0.2,\"y\":0.2,\"w\":0.3,\"h\":0.3}';"
                " try { n2.onConfigure(); } catch (e) { /* оригинал может требовать info */ }"
                " load.connect(0, n2, 0);"
                " window.__probeCrop2 = n2;"
                " return true; })()"
            )
            await asyncio.sleep(3.0)
            cfrac = await cdp.eval(
                "(() => window.__probeCrop2 && window.__probeCrop2.__deggCrop"
                " ? window.__probeCrop2.__deggCrop.state.frac : null)()"
            ) or {}
            check(
                "открытие воркфлою: рамка из crop_rect уцелела (сброса нет)",
                abs(cfrac.get("w", 0) - 0.3) < 1e-6 and abs(cfrac.get("h", 0) - 0.3) < 1e-6,
                cfrac,
            )

            # Восстановление значений по ИМЕНАМ. Фронтенд восстанавливает
            # значения виджетов позиционно, а порядок виджетов задаёт редактор,
            # поэтому нода обязана выправлять их по именам — иначе старые воркфлои
            # разъезжаются.
            restored = await cdp.eval(
                "(() => { const n = window.__probeCrop;"
                " const named = { lock_ratio: true, center_x: 11, center_y: 22,"
                "  frame_w: 333, frame_h: 444, aspect_ratio: '4:3',"
                "  megapixels: 3.5, multiplicity: 16, resample: 'bicubic',"
                "  fill: 'white', dim_outside: 25 };"
                " const bogus = [999, 'broken', 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0];"
                " try { n.onConfigure({ widgets_values: bogus, widgets_values_named: named }); }"
                "  catch (e) { return { error: String(e) }; }"
                " const g = (nm) => { const ww = (n.widgets || []).find((x) => x.name === nm);"
                "  return ww ? ww.value : null; };"
                " return { lock: g('lock_ratio'), cx: g('center_x'), cy: g('center_y'),"
                "  fw: g('frame_w'), fh: g('frame_h'), aspect: g('aspect_ratio'),"
                "  mp: g('megapixels'), mult: g('multiplicity'), resample: g('resample'),"
                "  fill: g('fill'), dim: g('dim_outside') }; })()"
            )
            # Значение виджета применяется через store — асинхронно, поэтому
            # читаем его на следующем кадре.
            await asyncio.sleep(0.35)
            restored = await cdp.eval(
                "(() => { const n = window.__probeCrop;"
                " const g = (nm) => { const w = (n.widgets || []).find((x) => x.name === nm);"
                "  return w ? w.value : null; };"
                " return { lock: g('lock_ratio'), cx: g('center_x'), cy: g('center_y'),"
                "  fw: g('frame_w'), fh: g('frame_h'), aspect: g('aspect_ratio'),"
                "  mp: g('megapixels'), mult: g('multiplicity'), resample: g('resample'),"
                "  fill: g('fill'), dim: g('dim_outside') }; })()"
            )
            rv = (restored or {}).get("lock")
            rv = {
                "lock": (restored or {}).get("lock"),
                "cx": (restored or {}).get("cx"),
                "cy": (restored or {}).get("cy"),
                "fw": (restored or {}).get("fw"),
                "fh": (restored or {}).get("fh"),
                "aspect": (restored or {}).get("aspect"),
                "mp": (restored or {}).get("mp"),
                "mult": (restored or {}).get("mult"),
                "resample": (restored or {}).get("resample"),
                "fill": (restored or {}).get("fill"),
                "dim": (restored or {}).get("dim"),
            }
            check(
                "восстановление по именам: значения встали на свои виджеты",
                rv.get("lock") is True
                and rv.get("aspect") == "4:3" and abs(float(rv.get("mp") or 0) - 3.5) < 0.01
                and rv.get("mult") == 16 and rv.get("resample") == "bicubic"
                and rv.get("fill") == "white" and rv.get("dim") == 25,
                restored,
            )
            # Координаты и размер — «зеркала» состояния: редактор пересчитывает их
            # сам из crop_rect/output_size, поэтому берутся не из воркфлоу.
            derived = await cdp.eval(STATE_JS)
            dfrac = derived.get("frac") or {}
            dnat = derived.get("natural") or [1, 1]
            exp_cx = round((dfrac.get("x", 0) + dfrac.get("w", 0) / 2 - 0.5) * dnat[0])
            exp_cy = round((dfrac.get("y", 0) + dfrac.get("h", 0) / 2 - 0.5) * dnat[1])
            check(
                "восстановление по именам: координаты пересчитаны из состояния",
                abs(int(rv.get("cx") or 0) - exp_cx) <= 1 and abs(int(rv.get("cy") or 0) - exp_cy) <= 1,
                {"got": [rv.get("cx"), rv.get("cy")], "expected": [exp_cx, exp_cy]},
            )
            check(
                "восстановление по именам: испорченное значение пропорций заменено",
                rv.get("aspect") in ("free", "1:1", "2:3", "3:2", "4:3", "3:4", "16:9", "9:16", "21:9"),
                rv.get("aspect"),
            )

            # Реальный воркфлоу пользователя: значения по именам на своих местах.
            loaded = await cdp.eval(
                "(() => { const n = window.__probeCrop;"
                " const info = { widgets_values: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],"
                "  widgets_values_named: { lock_ratio: true, center_x: 5, center_y: 6,"
                "   frame_w: 700, frame_h: 800, aspect_ratio: '16:9', megapixels: 2.0,"
                "   multiplicity: 8, resample: 'lanczos', fill: 'transparent', dim_outside: 50 } };"
                " try { n.onConfigure(info); } catch (e) { return { error: String(e) }; }"
                " const g = (nm) => { const ww = (n.widgets || []).find((x) => x.name === nm);"
                "  return ww ? ww.value : null; };"
                " return { aspect: g('aspect_ratio'), mp: g('megapixels'), fill: g('fill'),"
                "  fw: g('frame_w'), cx: g('center_x') }; })()"
            )
            await asyncio.sleep(0.35)
            loaded = await cdp.eval(
                "(() => { const n = window.__probeCrop;"
                " const g = (nm) => { const w = (n.widgets || []).find((x) => x.name === nm);"
                "  return w ? w.value : null; };"
                " return { aspect: g('aspect_ratio'), mp: g('megapixels'), fill: g('fill'),"
                "  fw: g('frame_w'), cx: g('center_x') }; })()"
            )
            lv = loaded or {}
            check(
                "воркфлоу: значения по именам (16:9, 2 МП) на своих местах",
                lv.get("aspect") == "16:9"
                and abs(float(lv.get("mp") or 0) - 2.0) < 0.01
                and lv.get("fill") == "transparent",
                loaded,
            )

            # ── РЕАЛЬНЫЙ ВОРКФЛОУ: значения не должны разъехаться ───────────
            # Фронтенд восстанавливает значения виджетов позиционно, а порядок
            # виджетов задаёт редактор. Поэтому проверяем на СОХРАНЁННОМ воркфлоу
            # пользователя: его сохранённые значения (старый порядок) должны
            # встать на свои виджеты.
            real_wf = Path(
                r"D:\ComfyUI_windows_portable\ComfyUI\user\default\workflows\Test Crop.json"
            )
            if real_wf.exists():
                wf = json.loads(real_wf.read_text(encoding="utf-8"))
                saved = next(
                    (n for n in wf.get("nodes", []) if n.get("type") == "DeggCropImage"),
                    None,
                )
                if saved:
                    info = {
                        "widgets_values": saved.get("widgets_values", []),
                        "widgets_values_named": saved.get("widgets_values_named", {}),
                    }
                    applied = await cdp.eval(
                        "(() => { const n = window.__probeCrop;"
                        " const info = __INFO__;"
                        " try { n.configure(info); } catch (e) { return { error: String(e) }; }"
                        " return { ok: true }; })()".replace("__INFO__", json.dumps(info))
                    )
                    await asyncio.sleep(0.5)
                    got = await cdp.eval(
                        "(() => { const n = window.__probeCrop;"
                        " const g = (nm) => { const w = (n.widgets || []).find((x) => x.name === nm);"
                        "  return w ? w.value : null; };"
                        " return { aspect: g('aspect_ratio'), mp: g('megapixels'),"
                        "  mult: g('multiplicity'), resample: g('resample'), fill: g('fill'),"
                        "  dim: g('dim_outside'), lock: g('lock_ratio'),"
                        "  crop: g('crop_rect'), manual: g('output_size') }; })()"
                    )
                    want = saved.get("widgets_values_named", {})
                    pairs = (
                        ("aspect", "aspect_ratio"),
                        ("resample", "resample"),
                        ("fill", "fill"),
                        ("mult", "multiplicity"),
                        ("dim", "dim_outside"),
                        ("lock", "lock_ratio"),
                        ("crop", "crop_rect"),
                        ("manual", "output_size"),
                    )
                    same = all(got.get(ours) == want.get(theirs) for ours, theirs in pairs)
                    check(
                        "реальный воркфлоу: значения встали на свои виджеты",
                        bool((applied or {}).get("ok")) and same
                        and abs(float(got.get("mp") or 0) - float(want.get("megapixels") or 0)) < 0.001,
                        {"got": got, "want": want},
                    )
                else:
                    print("  ВНИМАНИЕ: в воркфлоу нет ноды DeggCropImage — проверка пропущена")
            else:
                print("  ВНИМАНИЕ: реальный воркфлоу не найден — проверка пропущена")

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
