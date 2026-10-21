/**
 * Degg Crop Image — interactive frame editor (Nodes 2.0).
 *
 * The frame is stored in normalized source coordinates (JSON in the hidden
 * `crop_rect` widget), so zooming the canvas never changes the result and a
 * different input image keeps the same relative frame.
 *
 * Preview of the upstream image comes from the public `app.nodeOutputs` /
 * `app.nodePreviewImages` maps (the same source the native LoadImage preview
 * uses); no execution is required.
 */

const { app: appNs } = window.comfyAPI ?? {};
const app = appNs?.app ?? appNs;
const apiNs = window.comfyAPI?.api;
const api = apiNs?.api ?? apiNs;

const NODE_CLASS = 'DeggCropImage';
const CSS_ID = 'degg-crop-image-css';
const MIN_H = 180;
const MAX_H = 560;
const PAD = 10;
const HANDLE = 10;
const EDGE_HIT = 12;

const RATIO_VALUES = {
  '1:1': 1,
  '2:3': 2 / 3,
  '3:2': 3 / 2,
  '4:3': 4 / 3,
  '3:4': 3 / 4,
  '16:9': 16 / 9,
  '9:16': 9 / 16,
  '21:9': 21 / 9,
};

const LABELS = {
  ru: {
    aspect: {
      free: 'Свободные',
      '1:1': '1:1',
      '2:3': '2:3',
      '3:2': '3:2',
      '4:3': '4:3',
      '3:4': '3:4',
      '16:9': '16:9',
      '9:16': '9:16',
      '21:9': '21:9',
    },
    fill: {
      transparent: 'Прозрачная',
      black: 'Чёрная',
      white: 'Белая',
      gray: 'Серая',
    },
    fitAll: 'Вписать всё',
    fitCenter: 'По центру',
    reset: 'Сбросить',
    empty: 'Подключите изображение',
    fitAllTip: 'Рамка по всему изображению (с учётом пропорций)',
    fitCenterTip: 'Рамка по центру изображения',
    resetTip: 'Сбросить рамку к изображению',
  },
  en: {
    aspect: { free: 'Free' },
    fill: {
      transparent: 'Transparent',
      black: 'Black',
      white: 'White',
      gray: 'Gray',
    },
    fitAll: 'Fit all',
    fitCenter: 'Center',
    reset: 'Reset',
    empty: 'Connect an image',
    fitAllTip: 'Frame the whole image (respecting the ratio)',
    fitCenterTip: 'Center the frame on the image',
    resetTip: 'Reset the frame to the image',
  },
};

/* ───────────────────────── pure helpers (tested) ───────────────────────── */

export function pyRound(value) {
  const floor = Math.floor(value);
  const diff = value - floor;
  if (Math.abs(diff - 0.5) < 1e-9) return floor % 2 === 0 ? floor : floor + 1;
  return Math.floor(value + 0.5);
}

export function computeOutputSize(rectW, rectH, megapixels, multiplicity) {
  // Как ResolutionSelector: 1 MP = 1024², каждая сторона округляется
  // независимо до ближайшей кратности.
  const step = Math.max(1, Math.round(Number(multiplicity) || 1));
  const mp = Math.max(Number(megapixels) || 0.01, 1e-4);
  const w0 = Math.max(Number(rectW) || 0, 1e-6);
  const h0 = Math.max(Number(rectH) || 0, 1e-6);
  const ratio = w0 / h0;
  const total = mp * 1024 * 1024;
  const baseW = Math.sqrt(total * ratio);
  const baseH = baseW / ratio;

  return {
    w: Math.max(step, pyRound(baseW / step) * step),
    h: Math.max(step, pyRound(baseH / step) * step),
  };
}

export function parseRectFrac(raw) {
  let data = raw;
  if (typeof raw === 'string') {
    const text = raw.trim();
    if (!text) return { x: 0, y: 0, w: 1, h: 1 };
    try {
      data = JSON.parse(text);
    } catch {
      return { x: 0, y: 0, w: 1, h: 1 };
    }
  }
  if (Array.isArray(data)) {
    if (data.length !== 4) return { x: 0, y: 0, w: 1, h: 1 };
    data = { x: data[0], y: data[1], w: data[2], h: data[3] };
  }
  if (!data || typeof data !== 'object') return { x: 0, y: 0, w: 1, h: 1 };
  const out = {};
  for (const key of ['x', 'y', 'w', 'h']) {
    const value = Number(data[key]);
    if (!Number.isFinite(value)) return { x: 0, y: 0, w: 1, h: 1 };
    out[key] = value;
  }
  return clampFrac(out);
}

export function clampFrac(rect) {
  const max = 100;
  return {
    x: Math.min(Math.max(rect.x, -max), max),
    y: Math.min(Math.max(rect.y, -max), max),
    w: Math.min(Math.max(rect.w, 1e-4), max),
    h: Math.min(Math.max(rect.h, 1e-4), max),
  };
}

/** Resize a source-space frame from a drag delta. */
export function resizeFrame(start, dx, dy, kind, ratio, imgW, imgH, minPx = 16) {
  let x0 = start.x;
  let y0 = start.y;
  let x1 = start.x + start.w;
  let y1 = start.y + start.h;

  const west = kind === 'w' || kind === 'nw' || kind === 'sw';
  const east = kind === 'e' || kind === 'ne' || kind === 'se';
  const north = kind === 'n' || kind === 'nw' || kind === 'ne';
  const south = kind === 's' || kind === 'sw' || kind === 'se';

  const minW = Math.max(1, Math.min(minPx, imgW));
  const minH = Math.max(1, Math.min(minPx, imgH));

  if (west) x0 = Math.min(start.x + dx, x1 - minW);
  if (east) x1 = Math.max(start.x + start.w + dx, x0 + minW);
  if (north) y0 = Math.min(start.y + dy, y1 - minH);
  if (south) y1 = Math.max(start.y + start.h + dy, y0 + minH);

  const corner = (west || east) && (north || south);
  const horizontal = (west || east) && !(north || south);
  const vertical = (north || south) && !(west || east);

  if (ratio && Number.isFinite(ratio) && ratio > 0) {
    if (corner) {
      const dw = Math.abs(x1 - x0 - start.w);
      const dh = Math.abs(y1 - y0 - start.h) * ratio;
      if (dh > dw) {
        const h = Math.max(minH, y1 - y0);
        const w = Math.max(minW, h * ratio);
        if (west) x0 = x1 - w;
        else x1 = x0 + w;
        if (north) y0 = y1 - h;
        else y1 = y0 + h;
      } else {
        const w = Math.max(minW, x1 - x0);
        const h = Math.max(minH, w / ratio);
        if (west) x0 = x1 - w;
        else x1 = x0 + w;
        if (north) y0 = y1 - h;
        else y1 = y0 + h;
      }
    } else if (horizontal) {
      const w = Math.max(minW, x1 - x0);
      const h = Math.max(minH, w / ratio);
      const cy = start.y + start.h / 2;
      y0 = cy - h / 2;
      y1 = cy + h / 2;
    } else if (vertical) {
      const h = Math.max(minH, y1 - y0);
      const w = Math.max(minW, h * ratio);
      const cx = start.x + start.w / 2;
      x0 = cx - w / 2;
      x1 = cx + w / 2;
    }
  }

  if (x1 - x0 < 1) x1 = x0 + 1;
  if (y1 - y0 < 1) y1 = y0 + 1;

  return clampFrac({
    x: x0 / imgW,
    y: y0 / imgH,
    w: (x1 - x0) / imgW,
    h: (y1 - y0) / imgH,
  });
}

/* ───────────────────────────── DOM / editor ───────────────────────────── */

function css() {
  if (document.getElementById(CSS_ID)) return;
  const style = document.createElement('style');
  style.id = CSS_ID;
  style.textContent = `
.degg-crop{display:flex;flex-direction:column;gap:6px;width:100%;height:100%;
  font:12px/1.2 system-ui,sans-serif;color:#ddd;box-sizing:border-box;overflow:hidden}
.degg-crop *{box-sizing:border-box}
.degg-crop__bar{display:flex;gap:6px;flex:0 0 24px}
.degg-crop__btn{flex:1 1 0;min-width:0;height:24px;padding:0 6px;border:1px solid #4a4a4a;
  border-radius:6px;background:#2b2b2b;color:#ddd;font:inherit;cursor:pointer;overflow:hidden;
  white-space:nowrap;text-overflow:ellipsis}
.degg-crop__btn:hover{background:#383838}
.degg-crop__stage{position:relative;flex:1 1 auto;min-height:80px;overflow:hidden;
  border-radius:6px;background:#141414;touch-action:none;user-select:none;cursor:default}
.degg-crop__img{position:absolute;pointer-events:none;image-rendering:auto}
.degg-crop__fill{position:absolute;pointer-events:none;border-radius:1px}
.degg-crop__fill--checker{background-color:#bbb;background-image:
  linear-gradient(45deg,#888 25%,transparent 25%,transparent 75%,#888 75%),
  linear-gradient(45deg,#888 25%,transparent 25%,transparent 75%,#888 75%);
  background-size:16px 16px;background-position:0 0,8px 8px}
.degg-crop__dim{position:absolute;pointer-events:none}
.degg-crop__frame{position:absolute;pointer-events:none;border:1px solid #fff;
  box-shadow:0 0 0 1px rgba(0,0,0,.55),inset 0 0 0 1px rgba(0,0,0,.35)}
.degg-crop__h{position:absolute;pointer-events:none;width:${HANDLE}px;height:${HANDLE}px;
  margin:${-HANDLE / 2}px 0 0 ${-HANDLE / 2}px;background:#fff;border:1px solid #111;
  border-radius:2px;box-shadow:0 1px 2px rgba(0,0,0,.6)}
.degg-crop__badge{position:absolute;pointer-events:none;padding:3px 7px;border-radius:5px;
  background:rgba(0,0,0,.72);color:#fff;font:11px/1.35 ui-monospace,Consolas,monospace;
  white-space:pre;border:1px solid rgba(255,255,255,.18)}
.degg-crop__empty{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
  color:#777;font-size:12px;text-align:center;padding:12px}
`;
  document.head.appendChild(style);
}

function make(tag, className, parent) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (parent) parent.appendChild(element);
  return element;
}

function setRect(element, x, y, w, h) {
  element.style.left = `${x}px`;
  element.style.top = `${y}px`;
  element.style.width = `${Math.max(0, w)}px`;
  element.style.height = `${Math.max(0, h)}px`;
}

function locale() {
  try {
    const value = app?.extensionManager?.setting?.get?.('Comfy.Locale');
    if (typeof value === 'string' && value) return value.toLowerCase();
  } catch {
    /* ignore: frontend without settings manager */
  }
  return 'en';
}

function labels() {
  return locale().startsWith('ru') ? LABELS.ru : LABELS.en;
}

function setOptionLabels(widget, map) {
  if (!widget) return;
  widget.options = widget.options || {};
  widget.options.getOptionLabel = (value) => map[value] ?? String(value ?? '');
}

function widgetByName(node, name) {
  return (node.widgets || []).find((w) => w.name === name) || null;
}

function upstreamNode(node) {
  const source = node.getInputNode?.(0);
  if (!source) return null;
  try {
    if (source.isSubgraphNode?.()) {
      const link = node.getInputLink?.(0);
      const resolved = source.resolveSubgraphOutputLink?.(link?.origin_slot);
      if (resolved?.outputNode) return resolved.outputNode;
    }
  } catch {
    /* ignore broken subgraph wiring */
  }
  return source;
}

function locatorIdFor(node) {
  try {
    if (node?.graph && node.graph !== app?.rootGraph && node.graph?.id) {
      return `${node.graph.id}:${node.id}`;
    }
  } catch {
    /* ignore */
  }
  return String(node?.id ?? '');
}

function viewUrl(image) {
  if (!image) return null;
  if (typeof image === 'string') return image;
  if (!image.filename) return null;
  const params = new URLSearchParams({
    filename: image.filename,
    subfolder: image.subfolder || '',
    type: image.type || 'input',
  });
  const path = `/view?${params.toString()}`;
  return typeof api?.apiURL === 'function' ? api.apiURL(path) : path;
}

function previewFor(node) {
  const source = upstreamNode(node);
  if (!source) return { key: '', url: null };

  const key = locatorIdFor(source);
  const previews = app?.nodePreviewImages?.[key];
  if (Array.isArray(previews) && previews.length) {
    return { key: `p:${key}:${previews[0]}`, url: previews[0] };
  }

  const outputs = app?.nodeOutputs?.[key];
  const images = Array.isArray(outputs?.images) ? outputs.images : [];
  const image = images.find((item) => item && (item.filename || typeof item === 'string'));
  const url = viewUrl(image);
  if (url) {
    const tag = typeof image === 'string' ? image : `${image.filename}|${image.subfolder}|${image.type}`;
    return { key: `o:${key}:${tag}`, url };
  }

  const legacy = source.imgs?.[0]?.src;
  if (legacy) return { key: `i:${key}:${legacy}`, url: legacy };
  return { key: `n:${key}`, url: null };
}

function createEditor(node, widget) {
  css();

  const root = make('div', 'degg-crop');
  const bar = make('div', 'degg-crop__bar', root);
  const stage = make('div', 'degg-crop__stage', root);
  // Заливка лежит ПОД изображением: внутри картинки её закрывает сам кадр,
  // наружу (аутпейнт) она и есть фон результата.
  const fillEl = make('div', 'degg-crop__fill', stage);
  const image = make('img', 'degg-crop__img', stage);
  image.draggable = false;
  image.alt = '';
  const dimEl = make('div', 'degg-crop__dim', stage);
  const frameEl = make('div', 'degg-crop__frame', stage);
  const badge = make('div', 'degg-crop__badge', stage);
  const empty = make('div', 'degg-crop__empty', stage);
  const handles = {};
  for (const name of ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w']) {
    handles[name] = make('div', 'degg-crop__h', stage);
    handles[name].dataset.h = name;
  }

  const text = labels();
  const buttons = [
    ['fitAll', text.fitAll, text.fitAllTip],
    ['fitCenter', text.fitCenter, text.fitCenterTip],
    ['reset', text.reset, text.resetTip],
  ];
  const buttonEls = {};
  for (const [id, label, tip] of buttons) {
    const button = make('button', 'degg-crop__btn', bar);
    button.type = 'button';
    button.textContent = label;
    button.title = tip;
    buttonEls[id] = button;
  }

  const state = {
    frac: { x: 0, y: 0, w: 1, h: 1 },
    img: null,
    key: '',
    scale: 1,
    offX: 0,
    offY: 0,
    drag: null,
    pending: false,
  };

  const cropWidget = widgetByName(node, 'crop_rect');
  const aspectWidget = widgetByName(node, 'aspect_ratio');
  const lockWidget = widgetByName(node, 'lock_ratio');
  const dimWidget = widgetByName(node, 'dim_outside');
  const multWidget = widgetByName(node, 'multiplicity');
  const mpWidget = widgetByName(node, 'megapixels');
  const fillWidget = widgetByName(node, 'fill');

  if (cropWidget) cropWidget.hidden = true;
  setOptionLabels(aspectWidget, labels().aspect);
  setOptionLabels(fillWidget, labels().fill);

  function ratio() {
    const value = aspectWidget?.value;
    return RATIO_VALUES[value] ?? null;
  }

  function locked() {
    return !!lockWidget?.value;
  }

  function pxRect() {
    if (!state.img) return { x: 0, y: 0, w: 0, h: 0 };
    return {
      x: state.frac.x * state.img.w,
      y: state.frac.y * state.img.h,
      w: state.frac.w * state.img.w,
      h: state.frac.h * state.img.h,
    };
  }

  function union() {
    const r = pxRect();
    if (!state.img) return null;
    const x0 = Math.min(0, r.x);
    const y0 = Math.min(0, r.y);
    const x1 = Math.max(state.img.w, r.x + r.w);
    const y1 = Math.max(state.img.h, r.y + r.h);
    return { w: Math.max(1, x1 - x0), h: Math.max(1, y1 - y0) };
  }

  function desiredHeight() {
    const width = Math.max(220, (node.size?.[0] || 380) - 24);
    const u = union();
    if (!u) return 240;
    return Math.round(Math.min(MAX_H, Math.max(MIN_H, (width * u.h) / u.w + 32)));
  }

  function refreshLayoutSize() {
    widget.computeLayoutSize = () => ({ minWidth: 260, minHeight: desiredHeight() });
    node.graph?.setDirtyCanvas?.(true);
  }

  function persist() {
    if (!cropWidget) return;
    if (state.pending) return;
    state.pending = true;
    const write = () => {
      state.pending = false;
      const f = state.frac;
      const value = JSON.stringify({
        x: Number(f.x.toFixed(6)),
        y: Number(f.y.toFixed(6)),
        w: Number(f.w.toFixed(6)),
        h: Number(f.h.toFixed(6)),
      });
      if (cropWidget.value !== value) cropWidget.value = value;
    };
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(write);
    else write();
  }

  function setFrac(frac, { persist: save = true } = {}) {
    state.frac = clampFrac(frac);
    if (save) persist();
    refreshLayoutSize();
    layout();
  }

  function layoutBadge(r) {
    const out = computeOutputSize(
      r.w,
      r.h,
      mpWidget?.value ?? 2,
      multWidget?.value ?? 8,
    );
    const megapixels = (out.w * out.h) / 1e6;
    badge.textContent = `${out.w} × ${out.h} px\n${megapixels.toFixed(2)} MP`;
    badge.style.display = '';
  }

  function layout() {
    const width = stage.clientWidth;
    const height = stage.clientHeight;
    if (!width || !height) return;

    if (!state.img) {
      for (const el of [image, fillEl, dimEl, frameEl, badge, ...Object.values(handles)]) {
        el.style.display = 'none';
      }
      empty.style.display = '';
      return;
    }
    empty.style.display = 'none';
    image.style.display = '';
    fillEl.style.display = '';
    dimEl.style.display = '';
    frameEl.style.display = '';
    badge.style.display = '';
    for (const el of Object.values(handles)) el.style.display = '';

    const r = pxRect();
    const u = union();
    const usableW = Math.max(20, width - 2 * PAD);
    const usableH = Math.max(20, height - 2 * PAD);
    const scale = Math.min(usableW / u.w, usableH / u.h);
    state.scale = scale;
    state.offX = PAD + (usableW - u.w * scale) / 2 - Math.min(0, r.x) * scale;
    state.offY = PAD + (usableH - u.h * scale) / 2 - Math.min(0, r.y) * scale;

    setRect(image, state.offX, state.offY, state.img.w * scale, state.img.h * scale);

    const fx = state.offX + r.x * scale;
    const fy = state.offY + r.y * scale;
    const fw = r.w * scale;
    const fh = r.h * scale;

    const fill = fillWidget?.value ?? 'transparent';
    fillEl.className = `degg-crop__fill${fill === 'transparent' ? ' degg-crop__fill--checker' : ''}`;
    fillEl.style.background = fill === 'transparent' ? '' : fill === 'gray' ? '#808080' : fill;
    setRect(fillEl, fx, fy, fw, fh);

    setRect(frameEl, fx, fy, fw, fh);
    frameEl.style.borderColor = locked() ? '#7fe0a8' : '#ffffff';

    const dim = Math.min(100, Math.max(0, Number(dimWidget?.value ?? 50))) / 100;
    setRect(dimEl, fx, fy, fw, fh);
    dimEl.style.boxShadow = dim > 0 ? `0 0 0 100vmax rgba(0,0,0,${dim})` : 'none';

    const hx = { l: fx, c: fx + fw / 2, r: fx + fw };
    const hy = { t: fy, c: fy + fh / 2, b: fy + fh };
    const spots = {
      nw: [hx.l, hy.t],
      n: [hx.c, hy.t],
      ne: [hx.r, hy.t],
      e: [hx.r, hy.c],
      se: [hx.r, hy.b],
      s: [hx.c, hy.b],
      sw: [hx.l, hy.b],
      w: [hx.l, hy.c],
    };
    for (const [name, [x, y]] of Object.entries(spots)) setRect(handles[name], x, y, HANDLE, HANDLE);

    layoutBadge(r);
    const badgeW = badge.offsetWidth || 130;
    const badgeH = badge.offsetHeight || 32;
    let bx = fx;
    let by = fy - badgeH - 4;
    if (by < 2) by = fy + 6;
    if (by + badgeH > height - 2) by = Math.max(2, height - badgeH - 2);
    bx = Math.min(Math.max(2, bx), Math.max(2, width - badgeW - 2));
    badge.style.left = `${bx}px`;
    badge.style.top = `${by}px`;
  }

  function applyAspect({ keepArea = true } = {}) {
    const r = ratio();
    if (!r || !locked() || !state.img) return;
    const px = pxRect();
    const area = Math.max(px.w * px.h, 16);
    let w = Math.sqrt(area * r);
    let h = w / r;
    const cx = px.x + px.w / 2;
    const cy = px.y + px.h / 2;
    const min = 8;
    if (w < min) {
      w = min;
      h = w / r;
    }
    if (h < min) {
      h = min;
      w = h * r;
    }
    const x = keepArea ? cx - w / 2 : px.x;
    const y = keepArea ? cy - h / 2 : px.y;
    setFrac({
      x: x / state.img.w,
      y: y / state.img.h,
      w: w / state.img.w,
      h: h / state.img.h,
    });
  }

  function fit({ center = false } = {}) {
    if (!state.img) return;
    const r = ratio();
    if (r && locked()) {
      const imgRatio = state.img.w / state.img.h;
      let w;
      let h;
      if (imgRatio > r) {
        h = state.img.h;
        w = h * r;
      } else {
        w = state.img.w;
        h = w / r;
      }
      const x = center ? (state.img.w - w) / 2 : 0;
      const y = center ? (state.img.h - h) / 2 : 0;
      setFrac({
        x: x / state.img.w,
        y: y / state.img.h,
        w: w / state.img.w,
        h: h / state.img.h,
      });
    } else {
      setFrac({ x: 0, y: 0, w: 1, h: 1 });
    }
  }

  function readRect() {
    if (!cropWidget) return;
    state.frac = parseRectFrac(cropWidget.value);
  }

  // LoadImage после выбора/загрузки файла пишет app.nodeOutputs[key]. Событие
  // приходит по-разному: legacy зовёт source.onWidgetChanged, Nodes 2.0 зовёт
  // только widget.callback (createWidgetUpdateHandler). Слушаем оба канала,
  // иначе в новом UI превью остаётся от прошлой картинки.
  let watchedSource = null;
  let watchedPrev = null;
  let watchedHandler = null;
  let watchedWidgets = [];
  let watchedOutputs = null;

  function detachSource() {
    if (watchedSource && watchedHandler && watchedSource.onWidgetChanged === watchedHandler) {
      watchedSource.onWidgetChanged = watchedPrev || undefined;
    }
    for (const entry of watchedWidgets) {
      if (entry.widget.callback === entry.wrapper) {
        if (entry.original) entry.widget.callback = entry.original;
        else delete entry.widget.callback;
      }
    }
    watchedSource = null;
    watchedPrev = null;
    watchedHandler = null;
    watchedWidgets = [];
    watchedOutputs = null;
  }

  function outputsRef() {
    const source = upstreamNode(node);
    if (!source) return null;
    const key = locatorIdFor(source);
    return key ? app?.nodeOutputs?.[key] ?? null : null;
  }

  // Настоящая смена картинки = подменился объект app.nodeOutputs (даже если имя
  // файла то же). Остальные виджеты upstream превью не трогают.
  function sourceOutputsChanged() {
    const next = outputsRef();
    const changed = next !== watchedOutputs;
    watchedOutputs = next;
    return changed;
  }

  function noteSourceChanged() {
    requestAnimationFrame(() => loadSource({ force: sourceOutputsChanged() }));
  }

  function bindingsIntact() {
    if (watchedSource && watchedSource.onWidgetChanged !== watchedHandler) return false;
    return watchedWidgets.every((entry) => entry.widget.callback === entry.wrapper);
  }

  function watchSourceWidgets(source) {
    for (const widget of source.widgets || []) {
      if (!widget || typeof widget.callback !== 'function') continue;
      const original = widget.callback;
      const wrapper = function () {
        const result = original.apply(this, arguments);
        noteSourceChanged();
        return result;
      };
      widget.callback = wrapper;
      watchedWidgets.push({ widget, original, wrapper });
    }
  }

  function watchSource() {
    const source = upstreamNode(node);
    if (source === watchedSource && bindingsIntact()) return;
    detachSource();
    if (!source) return;
    watchedSource = source;
    watchedPrev = source.onWidgetChanged;
    watchedHandler = function () {
      const result = watchedPrev?.apply(this, arguments);
      noteSourceChanged();
      return result;
    };
    watchedSource.onWidgetChanged = watchedHandler;
    watchedOutputs = outputsRef();
    watchSourceWidgets(source);
  }

  function loadSource({ retry = true, force = false } = {}) {
    const { key, url } = previewFor(node);
    const nextKey = url ? key : '';
    if (!force && nextKey === state.key) return;
    state.key = nextKey;

    if (!url) {
      state.img = null;
      image.removeAttribute('src');
      empty.textContent = labels().empty;
      empty.style.display = '';
      refreshLayoutSize();
      layout();
      return;
    }

    image.onload = () => {
      const w = image.naturalWidth || 0;
      const h = image.naturalHeight || 0;
      if (!w || !h) return;
      const changed = !state.img || state.img.w !== w || state.img.h !== h;
      state.img = { w, h };
      if (changed) {
        if (locked() && ratio()) applyAspect();
        else {
          // Keep the relative frame; normalize the transport value once.
          persist();
          refreshLayoutSize();
          layout();
        }
      } else {
        layout();
      }
    };
    image.onerror = () => {
      state.img = null;
      image.removeAttribute('src');
      empty.textContent = labels().empty;
      empty.style.display = '';
      layout();
      if (retry) {
        state.key = '';
        requestAnimationFrame(() => loadSource({ retry: false }));
      }
    };
    image.src = force ? `${url}${url.includes('?') ? '&' : '?'}_=${Date.now()}` : url;
  }

  function localFromEvent(event) {
    const rect = stage.getBoundingClientRect();
    const zoom = rect.width / Math.max(1, stage.clientWidth);
    return {
      x: (event.clientX - rect.left) / (zoom || 1),
      y: (event.clientY - rect.top) / (zoom || 1),
    };
  }

  function sourceFromLocal(point) {
    return {
      x: (point.x - state.offX) / (state.scale || 1),
      y: (point.y - state.offY) / (state.scale || 1),
    };
  }

  function hitTest(point) {
    if (!state.img) return null;
    const r = pxRect();
    const x0 = state.offX + r.x * state.scale;
    const y0 = state.offY + r.y * state.scale;
    const x1 = x0 + r.w * state.scale;
    const y1 = y0 + r.h * state.scale;
    const t = EDGE_HIT;
    const insideX = point.x > x0 - t && point.x < x1 + t;
    const insideY = point.y > y0 - t && point.y < y1 + t;
    if (!insideX || !insideY) return null;

    const nearL = Math.abs(point.x - x0) <= t;
    const nearR = Math.abs(point.x - x1) <= t;
    const nearT = Math.abs(point.y - y0) <= t;
    const nearB = Math.abs(point.y - y1) <= t;

    if (nearL && nearT) return 'nw';
    if (nearR && nearT) return 'ne';
    if (nearL && nearB) return 'sw';
    if (nearR && nearB) return 'se';
    if (nearL) return 'w';
    if (nearR) return 'e';
    if (nearT) return 'n';
    if (nearB) return 's';
    if (point.x > x0 && point.x < x1 && point.y > y0 && point.y < y1) return 'move';
    return null;
  }

  function cursorFor(kind) {
    if (kind === 'move') return 'move';
    if (kind === 'nw' || kind === 'se') return 'nwse-resize';
    if (kind === 'ne' || kind === 'sw') return 'nesw-resize';
    if (kind === 'w' || kind === 'e') return 'ew-resize';
    return 'ns-resize';
  }

  function onPointerDown(event) {
    if (event.button !== 0 || !state.img) return;
    event.preventDefault();
    event.stopPropagation();
    const local = localFromEvent(event);
    const kind = hitTest(local);
    if (!kind) return;
    state.drag = {
      kind,
      start: sourceFromLocal(local),
      rect: pxRect(),
    };
    try {
      stage.setPointerCapture(event.pointerId);
    } catch {
      /* ignore */
    }
  }

  function onPointerMove(event) {
    if (!state.img) return;
    if (!state.drag) {
      const kind = hitTest(localFromEvent(event));
      stage.style.cursor = kind ? cursorFor(kind) : 'default';
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    const point = sourceFromLocal(localFromEvent(event));
    const dx = point.x - state.drag.start.x;
    const dy = point.y - state.drag.start.y;

    if (state.drag.kind === 'move') {
      const start = state.drag.rect;
      setFrac({
        x: (start.x + dx) / state.img.w,
        y: (start.y + dy) / state.img.h,
        w: start.w / state.img.w,
        h: start.h / state.img.h,
      });
      return;
    }

    setFrac(
      resizeFrame(
        state.drag.rect,
        dx,
        dy,
        state.drag.kind,
        locked() ? ratio() : null,
        state.img.w,
        state.img.h,
      ),
    );
  }

  function endDrag(event) {
    if (!state.drag) return;
    state.drag = null;
    try {
      stage.releasePointerCapture(event.pointerId);
    } catch {
      /* ignore */
    }
  }

  stage.addEventListener('pointerdown', onPointerDown);
  stage.addEventListener('pointermove', onPointerMove);
  stage.addEventListener('pointerup', endDrag);
  stage.addEventListener('pointercancel', endDrag);
  stage.addEventListener('pointerleave', (event) => {
    if (!state.drag) stage.style.cursor = 'default';
    else endDrag(event);
  });

  buttonEls.fitAll.addEventListener('click', (event) => {
    event.stopPropagation();
    fit({ center: false });
  });
  buttonEls.fitCenter.addEventListener('click', (event) => {
    event.stopPropagation();
    fit({ center: true });
  });
  buttonEls.reset.addEventListener('click', (event) => {
    event.stopPropagation();
    state.frac = { x: 0, y: 0, w: 1, h: 1 };
    persist();
    if (locked() && ratio()) applyAspect({ keepArea: true });
    else {
      refreshLayoutSize();
      layout();
    }
  });

  if (aspectWidget?.callback) {
    const original = aspectWidget.callback;
    aspectWidget.callback = (...args) => {
      const result = original.apply(aspectWidget, args);
      applyAspect();
      return result;
    };
  }
  if (lockWidget?.callback) {
    const original = lockWidget.callback;
    lockWidget.callback = (...args) => {
      const result = original.apply(lockWidget, args);
      if (lockWidget.value) applyAspect();
      else layout();
      return result;
    };
  }

  // Изменение «косметики» и параметров выхода перерисовывает сцену сразу:
  // без этого затемнение и плашка отстают до следующего resize/drag.
  for (const widget of [dimWidget, fillWidget, mpWidget, multWidget]) {
    if (!widget?.callback) continue;
    const original = widget.callback;
    widget.callback = (...args) => {
      const result = original.apply(widget, args);
      layout();
      return result;
    };
  }

  const resize = node.onResize;
  node.onResize = function () {
    const result = resize?.apply(this, arguments);
    layout();
    return result;
  };

  // Превью входа обязано появляться сразу после соединения провода, без запуска
  // воркфлоу (ТЗ §3). `executed`/`b_preview` для этого не годятся: на момент
  // подключения upstream уже отдаёт `imgs`, но события нет.
  const connections = node.onConnectionsChange;
  node.onConnectionsChange = function (type, slot, connected, link, slotType) {
    const result = connections?.apply(this, arguments);
    const isInput = type === (window.LiteGraph?.INPUT ?? 1);
    if (isInput && Number(slot) === 0) {
      requestAnimationFrame(() => {
        watchSource();
        loadSource({ force: true });
      });
    }
    return result;
  };

  const configure = node.onConfigure;
  node.onConfigure = function () {
    const result = configure?.apply(this, arguments);
    readRect();
    requestAnimationFrame(() => {
      watchSource();
      loadSource({ force: true });
      if (locked() && ratio() && state.img) applyAspect();
      layout();
    });
    return result;
  };

  readRect();
  refreshLayoutSize();
  requestAnimationFrame(() => {
    watchSource();
    loadSource({ force: true });
    layout();
  });

  const onExecuted = () => requestAnimationFrame(() => loadSource());
  api?.addEventListener?.('executed', onExecuted);
  api?.addEventListener?.('b_preview', onExecuted);

  const remove = node.onRemoved;
  node.onRemoved = function () {
    api?.removeEventListener?.('executed', onExecuted);
    api?.removeEventListener?.('b_preview', onExecuted);
    detachSource();
    return remove?.apply(this, arguments);
  };

  function refresh() {
    readRect();
    loadSource();
    layout();
  }

  return {
    widget: root,
    refresh,
    layout,
    state,
  };
}

/* ─────────────────────────────── extension ────────────────────────────── */

function setupNode(node) {
  if (node.__deggCrop || !(node.comfyClass === NODE_CLASS || node.type === NODE_CLASS)) {
    return;
  }
  const container = document.createElement('div');
  // ⛔ surfaces.vueNode:'never' прячет виджет в Nodes 2.0 (Vue) — редактор
  // существует только в legacy-канвасе. Панель свойств гасим hideInPanel.
  const widget = node.addDOMWidget('preview', 'degg-crop', container, {
    hideInPanel: true,
    hideOnZoom: false,
    serialize: false,
  });
  widget.serialize = false;

  const editor = createEditor(node, widget);
  container.appendChild(editor.widget);

  const index = node.widgets?.indexOf(widget) ?? -1;
  if (index > 0) {
    node.widgets.splice(index, 1);
    node.widgets.unshift(widget);
  }

  node.__deggCrop = editor;
  node.setDirtyCanvas?.(true, true);
}

if (app?.registerExtension) {
  app.registerExtension({
    name: 'Degg.CropImage',
    nodeCreated(node) {
      setupNode(node);
    },
    loadedGraphNode(node) {
      if (node.__deggCrop) requestAnimationFrame(() => node.__deggCrop.refresh());
    },
  });
}

export { setupNode };
