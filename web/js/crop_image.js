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
// Порядок виджетов на теле ноды. ComfyUI рисует обязательные входы первыми, а
// «интерфейсные» (необязательные) — после, поэтому порядок задаёт редактор.
const WIDGET_ORDER = [
  // Выбор файла и кнопка загрузки — в самом верху ноды, над кнопками
  // «Вписать / По центру / Сбросить» (они лежат внутри превью).
  'image_file',
  'upload_file',
  'mask_editor',
  'preview',
  'lock_ratio',
  'center_x',
  'center_y',
  'frame_w',
  'frame_h',
  'aspect_ratio',
  'megapixels',
  'multiplicity',
  'resample',
  'fill',
  'dim_outside',
  'golden_ratio',
  // Служебные виджеты состояния и выбор файла — в самом конце: старые
  // воркфлоу (без именованной карты) хранят значения виджетов позиционно,
  // и новый виджет в начале списка сдвинул бы все значения на шаг.
  'crop_rect',
  'output_size',
  'mask_file',
  // Служебный виджет image — цель штатного Mask Editor (фронтенд пишет
  // результат рисования именно в виджет с таким именем). Он скрыт, но
  // стоит рядом со скрытыми: новый виджет в начале списка сдвинул бы
  // позиционное восстановление значений в старых воркфлоу.
  'image',
];
const CSS_ID = 'degg-crop-image-css';
const MIN_H = 180;
const MAX_H = 560;
const PAD = 10;
const HANDLE = 10;
const EDGE_HIT = 12;
// Предел доли кадра: уходить за кадром дальше бессмысленно.
const MAX_FRAC = 100;
// Допуск, в котором пропорции кадра и результата считаются совпадающими.
const RATIO_TOLERANCE = 0.005;
// «Один мегапиксель» в единицах ноды (паритет с ResolutionSelector: 1024×1024 px).
const MEGAPIXEL_UNIT = 1024 * 1024;
// Минимальный размер кадра, px: ручная правка размера и смена пропорций.
const MIN_CROP_PX = 16;
const MIN_RATIO_PX = 8;
// Отступ DOM-виджета (как у остальных виджетов фронтенда) — входит в
// минимальную высоту, которую виджет сообщает фронтенду.
const WIDGET_MARGIN = 4;
// Золотое сечение: точки деления кадра — 1/φ и 1−1/φ (0.618 и 0.382).
// Линии сетки всегда стоят в этих точках ТЕКУЩЕЙ рамки, поэтому при
// изменении её размера сетка пересчитывается (как в Capture One).
const GOLDEN_RATIO = (1 + Math.sqrt(5)) / 2;
const GOLDEN_LINES = [1 - 1 / GOLDEN_RATIO, 1 / GOLDEN_RATIO];

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
    fit: 'Вписать',
    center: 'По центру',
    reset: 'Сбросить',
    upload: 'Загрузить файл',
    mask: 'Маска',
    empty: 'Подключите изображение',
    fitTip: 'Вписать рамку в границы изображения, сохранив пропорции, и поставить по центру',
    centerTip: 'Поставить рамку по центру изображения, не меняя размер',
    resetTip: 'Сбросить рамку к целому изображению и свободным пропорциям',
    lockOn: 'Зафиксировано',
    lockOff: 'Свободно',
    helpTitle: 'Подсказка',
    help: [
      'Перетаскивание внутри рамки — перемещение.',
      'Shift + перетаскивание внутри — строго по горизонтали или вертикали.',
      'Углы и стороны рамки — изменение размера.',
      'Shift + угол/сторона при «Свободно» — масштаб с сохранением текущих пропорций.',
      '«Зафиксировано» фиксирует пропорции, которые есть у рамки сейчас.',
      'Выбор пропорции в списке — рамка принимает её и включает фиксацию.',
      '«Свободно» — список пропорций переключается на «Свободные».',
      'X/Y — положение центра рамки относительно центра изображения, в пикселях.',
      'Ширина/высота — размер рамки в пикселях; центр при этом не уезжает, а мегапиксели задают размер результата.',
      '«Вписать» — вписать рамку в изображение с сохранением пропорций; «По центру» — центрировать; «Сбросить» — рамка по изображению и свободные пропорции.',
    ],
  },
  en: {
    aspect: { free: 'Free' },
    fill: {
      transparent: 'Transparent',
      black: 'Black',
      white: 'White',
      gray: 'Gray',
    },
    fit: 'Fit',
    center: 'Center',
    reset: 'Reset',
    upload: 'Upload file',
    mask: 'Mask',
    empty: 'Connect an image',
    fitTip: 'Fit the frame to the image bounds, keep proportions, then center',
    centerTip: 'Center the frame on the image without changing its size',
    resetTip: 'Reset the frame to the whole image and free proportions',
    lockOn: 'Locked',
    lockOff: 'Free',
    helpTitle: 'Help',
    help: [
      'Drag inside the frame to move it.',
      'Shift + drag inside — strictly horizontal or vertical movement.',
      'Frame corners and edges resize it.',
      'Shift + corner/edge while Free — scale keeping the current proportions.',
      'Locked freezes the proportions the frame has right now.',
      'Picking a ratio from the list applies it and turns locking on.',
      'Free switches the ratio list to Free.',
      'X/Y — frame centre offset from the image centre, in pixels.',
      'Width/height — frame size in pixels; the centre stays put, megapixels set the result size.',
      'Fit — fit the frame into the image keeping proportions; Center — centre it; Reset — whole image and free proportions.',
    ],
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
  return {
    x: Math.min(Math.max(rect.x, -MAX_FRAC), MAX_FRAC),
    y: Math.min(Math.max(rect.y, -MAX_FRAC), MAX_FRAC),
    w: Math.min(Math.max(rect.w, 1e-4), MAX_FRAC),
    h: Math.min(Math.max(rect.h, 1e-4), MAX_FRAC),
  };
}

/** Center the frame on the image, keeping its size (it may extend outside). */
export function centerFrameFrac(rect) {
  const w = Number(rect?.w);
  const h = Number(rect?.h);
  if (!(w > 0) || !(h > 0)) return { ...rect };
  return { x: (1 - w) / 2, y: (1 - h) / 2, w, h };
}

/**
 * Uniformly scale the frame to touch the nearest image bounds (up or down),
 * then center it. Proportions are preserved; the mode is not touched.
 */
export function fitFrameFrac(rect) {
  const w = Number(rect?.w);
  const h = Number(rect?.h);
  if (!(w > 0) || !(h > 0)) return { ...rect };
  const scale = Math.min(1 / w, 1 / h);
  const nw = w * scale;
  const nh = h * scale;
  return { x: (1 - nw) / 2, y: (1 - nh) / 2, w: nw, h: nh };
}

/** Offset of the frame centre from the image centre, in source pixels. */
export function frameCenterOffset(rect, imgW, imgH) {
  const r = rect || {};
  const w = Number(r.w);
  const h = Number(r.h);
  if (!(imgW > 0) || !(imgH > 0) || !(w > 0) || !(h > 0)) {
    return { x: 0, y: 0 };
  }
  return {
    x: (Number(r.x) + w / 2 - 0.5) * imgW,
    y: (Number(r.y) + h / 2 - 0.5) * imgH,
  };
}

/** Move the frame so its centre sits at the given offset from the image centre. */
export function moveFrameToOffset(rect, imgW, imgH, offsetX, offsetY) {
  const w = Number(rect?.w);
  const h = Number(rect?.h);
  if (!(imgW > 0) || !(imgH > 0) || !(w > 0) || !(h > 0)) return { ...rect };
  const cx = imgW / 2 + Number(offsetX || 0);
  const cy = imgH / 2 + Number(offsetY || 0);
  return clampFrac({
    x: (cx - (w * imgW) / 2) / imgW,
    y: (cy - (h * imgH) / 2) / imgH,
    w,
    h,
  });
}

/**
 * Размер результата по ручному заданию, округлённый до кратности
 * (как в «Выборе разрешения»: каждая сторона округляется независимо).
 */
export function snapSize(manual, multiplicity) {
  const step = Math.max(1, Math.round(Number(multiplicity) || 1));
  const w = Math.max(step, pyRound(Number(manual?.w || 0) / step) * step);
  const h = Math.max(step, pyRound(Number(manual?.h || 0) / step) * step);
  return { w, h };
}

/**
 * Итоговый размер результата: ручной (если задан) или по мегапикселям.
 * Ручной размер — это ровно то, что ввёл пользователь (с кратностью).
 */
export function outputSizeFor(rectW, rectH, megapixels, multiplicity, manual) {
  if (manual && Number(manual.w) > 0 && Number(manual.h) > 0) {
    return snapSize(manual, multiplicity);
  }
  return computeOutputSize(rectW, rectH, megapixels, multiplicity);
}

/**
 * Задать рамке пропорции, сохранив её площадь и центр
 * (то же, что делает выбор пресета пропорций).
 */
export function frameWithRatio(rect, imgW, imgH, ratio, minPx = MIN_RATIO_PX) {
  const w0 = Number(rect?.w) * imgW;
  const h0 = Number(rect?.h) * imgH;
  if (!(imgW > 0) || !(imgH > 0) || !(w0 > 0) || !(h0 > 0)) return { ...rect };
  if (!(Number(ratio) > 0)) return { ...rect };
  const area = Math.max(w0 * h0, minPx * minPx);
  let w = Math.sqrt(area * ratio);
  let h = w / ratio;
  if (w < minPx) {
    w = minPx;
    h = w / ratio;
  }
  if (h < minPx) {
    h = minPx;
    w = h * ratio;
  }
  const cx = Number(rect.x) * imgW + w0 / 2;
  const cy = Number(rect.y) * imgH + h0 / 2;
  return clampFrac({
    x: (cx - w / 2) / imgW,
    y: (cy - h / 2) / imgH,
    w: w / imgW,
    h: h / imgH,
  });
}

/** Разобрать ручной размер результата "Ш,В" из скрытого поля. */
export function parseManualSize(raw) {
  const text = String(raw ?? '').trim();
  if (!text) return null;
  const parts = text.split(/[,x×\s]+/).filter(Boolean).map(Number);
  if (parts.length < 2) return null;
  if (!Number.isFinite(parts[0]) || !Number.isFinite(parts[1])) return null;
  if (parts[0] <= 0 || parts[1] <= 0) return null;
  return { w: parts[0], h: parts[1] };
}

/** Пропорции кадра (в долях) относительно размера источника, в пикселях. */
export function fracRatio(frac, imgW, imgH) {
  const w = Number(frac?.w) * imgW;
  const h = Number(frac?.h) * imgH;
  return w > 0 && h > 0 ? w / h : 1;
}

/** Совпадают ли пропорции кадра и ручного результата (с допуском). */
export function ratiosMatch(frac, manual, imgW, imgH, tolerance = RATIO_TOLERANCE) {
  if (!manual) return true;
  const wanted = manual.w / manual.h;
  if (!Number.isFinite(wanted) || wanted <= 0) return false;
  return Math.abs(fracRatio(frac, imgW, imgH) - wanted) / wanted < tolerance;
}

/** Shift-constrained move: lock to the dominant axis. */
export function axisDelta(dx, dy) {
  return Math.abs(dx) >= Math.abs(dy) ? { dx, dy: 0 } : { dx: 0, dy };
}

/** Resize a source-space frame from a drag delta. */
export function resizeFrame(start, dx, dy, kind, ratio, imgW, imgH, minPx = MIN_CROP_PX) {
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
.degg-crop{position:relative;display:flex;flex-direction:column;gap:6px;width:100%;height:100%;
  font:12px/1.2 system-ui,sans-serif;color:#ddd;box-sizing:border-box;overflow:hidden}
.degg-crop *{box-sizing:border-box}
.degg-crop__bar{display:flex;gap:6px;flex:0 0 24px}
.degg-crop__btn{flex:1 1 0;min-width:0;height:24px;padding:0 6px;border:1px solid #4a4a4a;
  border-radius:6px;background:#2b2b2b;color:#ddd;font:inherit;cursor:pointer;overflow:hidden;
  white-space:nowrap;text-overflow:ellipsis}
.degg-crop__btn:hover{background:#383838}
.degg-crop__help{position:absolute;top:6px;right:8px;z-index:12;width:18px;height:18px;
  padding:0;border:1px solid rgba(255,255,255,.4);border-radius:9px;
  background:rgba(24,24,28,.75);color:#fff;font:bold 11px/1 system-ui,sans-serif;
  cursor:help}
.degg-crop__help:hover{background:rgba(60,60,68,.9)}
.degg-crop__helpbox{position:absolute;right:8px;z-index:11;display:none;
  max-width:min(360px,90%);overflow:auto;padding:8px 10px;
  border:1px solid rgba(255,255,255,.4);border-radius:6px;background:rgba(18,18,18,.97);
  color:#ccc;font:11px/15px system-ui,sans-serif;pointer-events:none}
.degg-crop__helpbox b{display:block;color:#fff;margin-bottom:4px}
.degg-crop__stage{position:relative;flex:1 1 auto;min-height:80px;overflow:hidden;
  border-radius:6px;background:#141414;touch-action:none;user-select:none;cursor:default}
.degg-crop__img{position:absolute;pointer-events:none;image-rendering:auto}
.degg-crop__masklayer{position:absolute;pointer-events:none;image-rendering:auto;display:none}
.degg-crop__fill{position:absolute;pointer-events:none;border-radius:1px}
.degg-crop__fill--checker{background-color:#bbb;background-image:
  linear-gradient(45deg,#888 25%,transparent 25%,transparent 75%,#888 75%),
  linear-gradient(45deg,#888 25%,transparent 25%,transparent 75%,#888 75%);
  background-size:16px 16px;background-position:0 0,8px 8px}
.degg-crop__dim{position:absolute;pointer-events:none}
.degg-crop__grid{position:absolute;pointer-events:none}
.degg-crop__gridline{position:absolute;background:rgba(255,255,255,.6);
  box-shadow:0 0 0 1px rgba(0,0,0,.35)}
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

function apiUrl(path) {
  return typeof api?.apiURL === 'function' ? api.apiURL(path) : path;
}

function setOptionLabels(widget, map) {
  if (!widget) return;
  widget.options = widget.options || {};
  widget.options.getOptionLabel = (value) => map[value] ?? String(value ?? '');
}

function widgetByName(node, name) {
  return (node.widgets || []).find((w) => w.name === name) || null;
}

/**
 * Индекс входного сокета «image».
 *
 * Сокет image — НЕОБЯЗАТЕЛЬНЫЙ (нода умеет работать с файлом из input без
 * подключения), поэтому он НЕ первый во входах: обязательные входы идут
 * первыми. Искать upstream по индексу 0 нельзя — только по имени.
 */
function imageSlotIndex(node) {
  const inputs = node.inputs || [];
  const index = inputs.findIndex((slot) => slot?.name === 'image');
  return index >= 0 ? index : 0;
}

function upstreamNode(node) {
  const index = imageSlotIndex(node);
  const source = node.getInputNode?.(index);
  if (!source) return null;
  try {
    if (source.isSubgraphNode?.()) {
      const link = node.getInputLink?.(index);
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

/**
 * Имя файла из виджета выбора (в новом фронтенде значение может быть
 * аннотированным путём-объектом или строкой).
 */
function fileWidgetName(node) {
  const widget = widgetByName(node, 'image_file');
  const value = widget?.value;
  if (typeof value === 'string') return value.trim();
  if (value && typeof value === 'object') {
    const name = value.name ?? value.filename ?? '';
    const sub = value.subfolder ? `${value.subfolder}/` : '';
    return sub ? `${sub}${name}` : String(name);
  }
  return '';
}

/** Превью файла из input (как у LoadImage). */
function filePreviewUrl(node) {
  const name = fileWidgetName(node);
  if (!name) return null;
  const clean = name.replace(/\\/g, '/').replace(/^\//, '');
  return viewUrl({ filename: clean, subfolder: '', type: 'input' });
}

/**
 * Ссылка на файл с нарисованной маской (штатный Mask Editor ComfyUI).
 *
 * Фронтенд сохраняет результат рисования в папку input: альфа файла =
 * инвертированная маска, RGB = покрашенная картинка. Значение приходит
 * аннотированным путём («имя.png [input]»).
 */
function paintedMaskUrl(node) {
  const widget = widgetByName(node, 'mask_file');
  const raw = widget?.value;
  if (typeof raw !== 'string' || !raw.trim()) return null;
  const name = raw.trim().replace(/\s*\[(input|output|temp)\]\s*$/i, '');
  if (!name) return null;
  return viewUrl({ filename: name, subfolder: '', type: 'input' });
}

function previewFor(node) {
  const source = upstreamNode(node);
  if (!source) {
    // Сокет не подключён — показываем выбранный файл (если он есть).
    const url = filePreviewUrl(node);
    return url ? { key: `f:${fileWidgetName(node)}`, url } : { key: '', url: null };
  }

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

  // ── DOM ──────────────────────────────────────────────────────────────────
  const root = make('div', 'degg-crop');
  const bar = make('div', 'degg-crop__bar', root);
  const stage = make('div', 'degg-crop__stage', root);
  // Заливка лежит ПОД изображением: внутри картинки её закрывает сам кадр,
  // наружу (аутпейнт) она и есть фон результата.
  const fillEl = make('div', 'degg-crop__fill', stage);
  const image = make('img', 'degg-crop__img', stage);
  // Слой с маской, нарисованной в штатном Mask Editor: прозрачность файла =
  // инвертированная маска, поэтому выбитые маской места видны как «дырки».
  const maskLayer = make('img', 'degg-crop__masklayer', stage);
  image.draggable = false;
  image.alt = '';
  const dimEl = make('div', 'degg-crop__dim', stage);
  // Сетка золотого сечения — между затемнением и рамкой (рамка и уголки
  // остаются поверх). Линии стоят в точках 1/φ и 1−1/φ от краёв кадра.
  const gridEl = make('div', 'degg-crop__grid', stage);
  const gridLines = [];
  for (const pos of GOLDEN_LINES) {
    for (const axis of ['v', 'h']) {
      const line = make('div', 'degg-crop__gridline', gridEl);
      line.dataset.axis = axis;
      line.dataset.pos = String(pos);
      gridLines.push(line);
    }
  }
  const frameEl = make('div', 'degg-crop__frame', stage);
  const badge = make('div', 'degg-crop__badge', stage);
  const empty = make('div', 'degg-crop__empty', stage);
  // Иконка «?» — в правом верхнем углу ПРЕВЬЮ (как в Degg_Images_Save_Compare).
  const helpBtn = make('button', 'degg-crop__help', stage);
  helpBtn.type = 'button';
  helpBtn.textContent = '?';
  // Подсказка живёт в корне: сцена режет содержимое (overflow:hidden), а текст
  // подсказки должен перекрывать её целиком.
  const helpBox = make('div', 'degg-crop__helpbox', root);
  const handles = {};
  for (const name of ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w']) {
    handles[name] = make('div', 'degg-crop__h', stage);
    handles[name].dataset.h = name;
  }

  const buttons = [
    ['fit', 'fit'],
    ['center', 'center'],
    ['reset', 'reset'],
  ];
  const buttonEls = {};
  for (const [id] of buttons) {
    const button = make('button', 'degg-crop__btn', bar);
    button.type = 'button';
    buttonEls[id] = button;
  }

  // ── СОСТОЯНИЕ ───────────────────────────────────────────────────────────
  // Два источника истины:
  //   frac   — геометрия кадра в долях источника (сериализуется в crop_rect);
  //   manual — ручной размер результата в px (сериализуется в output_size),
  //            null = размер результата считается по мегапикселям.
  // Всё остальное — производные величины: они нигде не хранятся, а считаются
  // функциями (cropPx, cropRatio, resultSize, effectiveRatio).
  const state = {
    frac: { x: 0, y: 0, w: 1, h: 1 },
    manual: null,
    img: null, // размер источника в пикселях
    key: '', // ключ превью (чтобы не грузить одно и то же)
    imageKey: null, // ключ уже показанной картинки (для «новая картинка»)
    restoring: false, // нода восстанавливается из воркфлоу: рамку не трогаем

    scale: 1, // параметры отрисовки сцены
    offX: 0,
    offY: 0,
    drag: null,
    pending: false,
    ratioLock: null, // захваченные пропорции кадра (px), когда замок без пресета
    suppress: 0, // счётчик программных записей в виджеты
  };

  const cropWidget = widgetByName(node, 'crop_rect');
  const outputSizeWidget = widgetByName(node, 'output_size');
  const aspectWidget = widgetByName(node, 'aspect_ratio');
  const lockWidget = widgetByName(node, 'lock_ratio');
  const centerXWidget = widgetByName(node, 'center_x');
  const centerYWidget = widgetByName(node, 'center_y');
  const frameWWidget = widgetByName(node, 'frame_w');
  const frameHWidget = widgetByName(node, 'frame_h');
  const dimWidget = widgetByName(node, 'dim_outside');
  const multWidget = widgetByName(node, 'multiplicity');
  const mpWidget = widgetByName(node, 'megapixels');
  const fillWidget = widgetByName(node, 'fill');
  const goldenWidget = widgetByName(node, 'golden_ratio');

  if (cropWidget) cropWidget.hidden = true;
  setOptionLabels(aspectWidget, labels().aspect);
  setOptionLabels(fillWidget, labels().fill);

  // ── ПРОИЗВОДНЫЕ ВЕЛИЧИНЫ ────────────────────────────────────────────────
  /** Геометрия кадра в пикселях источника. */
  function cropPx() {
    if (!state.img) return { x: 0, y: 0, w: 0, h: 0 };
    return {
      x: state.frac.x * state.img.w,
      y: state.frac.y * state.img.h,
      w: state.frac.w * state.img.w,
      h: state.frac.h * state.img.h,
    };
  }

  /** Пропорции кадра по пикселям источника. */
  function cropRatio() {
    const r = cropPx();
    return r.w > 0 && r.h > 0 ? r.w / r.h : 1;
  }

  /** Пресет пропорций из списка (протокольное значение). */
  function ratio() {
    return RATIO_VALUES[aspectWidget?.value] ?? null;
  }

  function locked() {
    return !!lockWidget?.value;
  }

  /** «Зафиксировано» держит либо пресет, либо пропорции, которые были у кадра в
   * момент включения (когда в списке «Свободные»). */
  function effectiveRatio() {
    const preset = ratio();
    if (preset) return preset;
    if (!locked()) return null;
    return state.ratioLock ?? cropRatio();
  }

  function megapixels() {
    return Number(mpWidget?.value ?? 2);
  }

  function multiplicity() {
    return Math.max(1, Math.round(Number(multWidget?.value ?? 8)));
  }

  /** Итоговый размер результата: ручной размер важнее мегапикселей. */
  function resultSize() {
    if (state.manual) return snapSize(state.manual, multiplicity());
    return computeOutputSize(cropPx().w, cropPx().h, megapixels(), multiplicity());
  }

  // ── ЗАПИСЬ В ВИДЖЕТ (единственная точка) ─────────────────────────────────
  // call: true  — как это делает фронтенд: «виджет изменился, пересчитай ноду»;
  //        false — тихая запись: значение отражает состояние, нода не
  //                пересчитывается (иначе каждый кадр drag'а запускал бы пересчёт).
  function writeWidget(widget, value, { call = false } = {}) {
    if (!widget || widget.value === value) return false;
    widget.value = value;
    const id = widgetIdFor(widget);
    try {
      widgetStore()?.setValue?.(id, value);
    } catch {
      /* store may be unavailable in this frontend build */
    }
    if (call) {
      try {
        widget.callback?.(value, app?.canvas, node);
      } catch {
        /* a faulty callback must not break the editor */
      }
    }
    node.setDirtyCanvas?.(true, true);
    node.graph?.setDirtyCanvas?.(true, true);
    return true;
  }

  /** Программная запись: callback виджета не должен запускать пользовательскую
   * логику (иначе программная запись → callback → запись в виджет → цикл). */
  function programmatic(widget, value) {
    state.suppress += 1;
    try {
      return writeWidget(widget, value, { call: true });
    } finally {
      state.suppress -= 1;
    }
  }

  function widgetIdFor(widget) {
    const graphId = node.graph?.id ?? app?.rootGraph?.id;
    if (!graphId || !widget?.name) return null;
    return `${graphId}:${encodeURIComponent(String(node.id))}:${encodeURIComponent(widget.name)}`;
  }

  // Nodes 2.0 рисует виджеты из widgetValueStore, legacy — из widget.value.
  // Пишем в оба места, иначе программная смена видна только в одном из режимов.
  function widgetStore() {
    const candidates = [
      app?.widgetValueStore,
      app?.extensionManager?.widgetValueStore,
      window?.comfyAPI?.widgetValueStore,
      window?.comfyAPI?.stores?.widgetValueStore,
    ];
    for (const store of candidates) {
      if (store && typeof store.setValue === 'function') return store;
    }
    return null;
  }

  // ── СЕРИАЛИЗАЦИЯ ─────────────────────────────────────────────────────────
  function persistState() {
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
      if (outputSizeWidget) {
        const manual = state.manual ? `${state.manual.w},${state.manual.h}` : '';
        if (outputSizeWidget.value !== manual) outputSizeWidget.value = manual;
      }
    };
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(write);
    else write();
  }

  // ── ВИДЫ (виджеты, которые только показывают состояние) ─────────────────
  function syncViews() {
    if (!state.img) {
      writeWidget(centerXWidget, 0);
      writeWidget(centerYWidget, 0);
      writeWidget(frameWWidget, 0);
      writeWidget(frameHWidget, 0);
      return;
    }
    const offset = frameCenterOffset(state.frac, state.img.w, state.img.h);
    writeWidget(centerXWidget, Math.round(offset.x));
    writeWidget(centerYWidget, Math.round(offset.y));
    const out = resultSize();
    writeWidget(frameWWidget, out.w);
    writeWidget(frameHWidget, out.h);
    // В ручном режиме мегапиксели показывают эквивалент результата.
    if (state.manual) writeWidget(mpWidget, (out.w * out.h) / MEGAPIXEL_UNIT);
  }

  // ── ПРИМЕНЕНИЕ СОСТОЯНИЯ (единственная точка) ───────────────────────────
  function commit() {
    persistState();
    syncViews();
    refreshLayoutSize();
    layout();
  }

  // ── ОПЕРАЦИИ (у каждой — явная политика) ─────────────────────────────────

  /** Мышь: кадр едет или меняет размер. Если задан ручной размер результата, его
   * пропорции следуют за кадром (ширина сохраняется) — иначе результат
   * растянулся бы. */
  function moveOrResizeCrop(frac) {
    if (state.manual) {
      const ratio = fracRatio(frac, state.img.w, state.img.h);
      state.manual = { w: state.manual.w, h: state.manual.w / ratio };
    }
    state.frac = clampFrac(frac);
    commit();
  }

  /** Пресет пропорций: ручной результат сбрасывается (размер снова от MP). */
  function applyRatioPreset(value) {
    state.manual = null;
    state.frac = frameWithRatio(state.frac, state.img.w, state.img.h, value);
    commit();
  }

  /** Ручной размер результата: пропорции кадра следуют за результатом
   * (площадь и центр сохраняются), иначе кадр растянулся бы. */
  function setResultSize(width, height) {
    const manual = snapSize({ w: width, h: height }, multiplicity());
    state.manual = manual;
    state.frac = frameWithRatio(state.frac, state.img.w, state.img.h, manual.w / manual.h);
    commit();
  }

  /** Правка мегапикселей: ручной результат сбрасывается, размер снова от MP. */
  function setMegaPixels() {
    state.manual = null;
    commit();
  }

  /** Координаты центра кадра (px, смещение от центра изображения). */
  function setFrameCenter(offsetX, offsetY) {
    if (!Number.isFinite(offsetX) || !Number.isFinite(offsetY)) return;
    state.frac = moveFrameToOffset(state.frac, state.img.w, state.img.h, offsetX, offsetY);
    commit();
  }

  /** Сброс к подключённому изображению: рамка по всему кадру, а значения ноды
   * принимают значения изображения:
   *  - рамка = всё изображение (0,0,1,1);
   *  - пропорции — пропорции изображения, фиксация на них (пресет не подходит:
   *    у произвольного изображения своего пресета нет);
   *  - мегапиксели — площадь изображения, значит результат = размер изображения;
   *  - ручной размер сброшен: он задавал бы другой результат.
   *
   * Используется и кнопкой «Сбросить», и при подключении/смене картинки —
   * одна операция на оба случая. */
  function resetToImage() {
    state.frac = { x: 0, y: 0, w: 1, h: 1 };
    // Размер результата = размер изображения. Задаём его ТОЧНО (ручной размер),
    // а не через мегапиксели: их шаг 0.1 дал бы «2728 × 1536» вместо
    // «2720 × 1536». Мегапиксели покажут эквивалент (syncViews).
    state.manual = state.img
      ? snapSize({ w: state.img.w, h: state.img.h }, multiplicity())
      : null;
    state.ratioLock = state.img ? state.img.w / state.img.h : null;
    programmatic(aspectWidget, 'free');
    programmatic(lockWidget, true);
    commit();
  }

  /** Ручной размер, расходящийся с пропорциями кадра, устарел — отбрасываем:
   * иначе MP и пресет пропорций молча игнорировались бы, а кадр растягивался бы. */
  function dropStaleManual() {
    if (!state.manual || !state.img) return false;
    if (ratiosMatch(state.frac, state.manual, state.img.w, state.img.h)) return false;
    state.manual = null;
    return true;
  }

  /** Загрузка состояния из виджетов (открытие воркфлоу). */
  function loadStateFromWidgets() {
    if (cropWidget) state.frac = parseRectFrac(cropWidget.value);
    state.manual = parseManualSize(outputSizeWidget?.value);
    dropStaleManual();
  }

  // ── ОТРИСОВКА (чистая: только читает состояние) ─────────────────────────
  function union() {
    const r = cropPx();
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
    // Размер сообщается фронтенду через getMinHeight (см. addDOMWidget):
    // он зовёт его при каждой раскладке, поэтому пересчитывать вручную
    // и переопределять computeLayoutSize здесь не нужно.
    node.graph?.setDirtyCanvas?.(true);
  }

  function layoutBadge() {
    const out = resultSize();
    const megapixels = (out.w * out.h) / 1e6;
    // Одна строка: размер и мегапиксели, разделённые отступом (шрифт моноширинный).
    badge.textContent = `${out.w} × ${out.h} px  ${megapixels.toFixed(2)} MP`;
    badge.style.display = '';
  }

  // Подсказка открывается под кнопкой «?» — она лежит в превью, а сам блок
  // текста перекрывает сцену (сцена режет содержимое, поэтому блок — в корне).
  function syncHelpAnchor() {
    const top = (stage.offsetTop || 0) + 26;
    helpBox.style.top = `${top}px`;
    helpBox.style.maxHeight = `max(80px, calc(100% - ${top + 8}px))`;
  }

  function layout() {
    syncHelpAnchor();
    const width = stage.clientWidth;
    const height = stage.clientHeight;
    if (!width || !height) return;

    if (!state.img) {
      for (const el of [image, maskLayer, fillEl, dimEl, frameEl, badge, ...Object.values(handles)]) {
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

    const r = cropPx();
    const u = union();
    const usableW = Math.max(20, width - 2 * PAD);
    const usableH = Math.max(20, height - 2 * PAD);
    const scale = Math.min(usableW / u.w, usableH / u.h);
    state.scale = scale;
    state.offX = PAD + (usableW - u.w * scale) / 2 - Math.min(0, r.x) * scale;
    state.offY = PAD + (usableH - u.h * scale) / 2 - Math.min(0, r.y) * scale;

    setRect(image, state.offX, state.offY, state.img.w * scale, state.img.h * scale);

    // Нарисованная маска (штатный Mask Editor) показывается поверх картинки:
    // файл имеет ту же геометрию, его альфа = инвертированная маска.
    const maskUrl = paintedMaskUrl(node);
    if (maskUrl) {
      if (maskLayer.dataset.src !== maskUrl) {
        maskLayer.dataset.src = maskUrl;
        maskLayer.src = maskUrl;
      }
      setRect(maskLayer, state.offX, state.offY, state.img.w * scale, state.img.h * scale);
      maskLayer.style.display = '';
    } else {
      maskLayer.style.display = 'none';
      maskLayer.dataset.src = '';
    }

    const fx = state.offX + r.x * scale;
    const fy = state.offY + r.y * scale;
    const fw = r.w * scale;
    const fh = r.h * scale;

    const fill = fillWidget?.value ?? 'transparent';
    fillEl.className = `degg-crop__fill${fill === 'transparent' ? ' degg-crop__fill--checker' : ''}`;
    fillEl.style.background = fill === 'transparent' ? '' : fill === 'gray' ? '#808080' : fill;
    setRect(fillEl, fx, fy, fw, fh);

    // Сетка золотого сечения: видна только по флажку, линии пересчитываются
    // под текущий размер кадра.
    const showGolden = !!goldenWidget?.value;
    gridEl.style.display = showGolden ? '' : 'none';
    if (showGolden) {
      setRect(gridEl, fx, fy, fw, fh);
      for (const line of gridLines) {
        const pos = Number(line.dataset.pos);
        if (line.dataset.axis === 'v') {
          line.style.left = `${pos * 100}%`;
          line.style.top = '0';
          line.style.width = '1px';
          line.style.height = '100%';
        } else {
          line.style.top = `${pos * 100}%`;
          line.style.left = '0';
          line.style.width = '100%';
          line.style.height = '1px';
        }
      }
    }

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

    layoutBadge();
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

  // ── ПРЕВЬЮ ИСТОЧНИКА ────────────────────────────────────────────────────
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
    if (!force && nextKey === state.key) {
      // Картинка уже показана — грузить нечего, значит и восстановление завершено.
      state.restoring = false;
      return;
    }
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
      // Штатный Mask Editor берёт картинку из node.imgs (как у LoadImage).
      node.imgs = [image];
      const changed = !state.img || state.img.w !== w || state.img.h !== h;
      // Новая картинка (другой файл) — тот же сброс, что и у кнопки «Сбросить».
      // При восстановлении воркфлоу (restore) рамку трогать нельзя: она пришла
      // из crop_rect.
      const isNewImage = state.imageKey !== state.key;
      state.imageKey = state.key;
      state.img = { w, h };
      // Восстановление воркфлоу: рамка пришла из crop_rect — её не трогаем.
      // Связи в воркфлоу восстанавливаются ПОСЛЕ onConfigure, поэтому флаг живёт
      // в состоянии, а не в аргументе загрузки.
      const restoring = state.restoring;
      state.restoring = false;
      if (isNewImage && !restoring) {
        resetToImage();
        return;
      }
      if (!changed) {
        layout();
        return;
      }
      // Новая картинка: кадр остаётся в долях, а пропорции приводятся к
      // действующей политике — к ручному размеру, к пресету или к текущим.
      if (dropStaleManual()) {
        commit();
      } else if (state.manual) {
        state.frac = frameWithRatio(state.frac, w, h, state.manual.w / state.manual.h);
        commit();
      } else if (locked() && ratio()) {
        state.frac = frameWithRatio(state.frac, w, h, ratio());
        commit();
      } else if (locked()) {
        state.ratioLock = cropRatio();
        commit();
      } else {
        commit();
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

  // ── DRAG ─────────────────────────────────────────────────────────────────
  function localFromEvent(event, view) {
    const rect = view?.rect || stage.getBoundingClientRect();
    const zoom = view?.zoom || rect.width / Math.max(1, stage.clientWidth);
    return {
      x: (event.clientX - rect.left) / (zoom || 1),
      y: (event.clientY - rect.top) / (zoom || 1),
    };
  }

  // Масштаб и смещение сцены меняются, пока рамка растёт (union расширяется).
  // Точку отсчёта drag фиксируем на pointerdown, иначе каждый следующий move
  // пересчитывается новым scale и рамка разгоняется до клампа.
  function sourceFromLocal(point, view) {
    const scale = view?.scale || state.scale || 1;
    const offX = view ? view.offX : state.offX;
    const offY = view ? view.offY : state.offY;
    return {
      x: (point.x - offX) / scale,
      y: (point.y - offY) / scale,
    };
  }

  function hitTest(point) {
    if (!state.img) return null;
    const r = cropPx();
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
    const view = {
      rect: stage.getBoundingClientRect(),
      zoom: 0,
      scale: state.scale,
      offX: state.offX,
      offY: state.offY,
    };
    view.zoom = view.rect.width / Math.max(1, stage.clientWidth);
    state.drag = {
      kind,
      start: sourceFromLocal(local, view),
      rect: cropPx(),
      ratio: cropRatio(),
      view,
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
    const point = sourceFromLocal(localFromEvent(event, state.drag.view), state.drag.view);
    const dx = point.x - state.drag.start.x;
    const dy = point.y - state.drag.start.y;

    if (state.drag.kind === 'move') {
      const start = state.drag.rect;
      const delta = event.shiftKey ? axisDelta(dx, dy) : { dx, dy };
      moveOrResizeCrop({
        x: (start.x + delta.dx) / state.img.w,
        y: (start.y + delta.dy) / state.img.h,
        w: start.w / state.img.w,
        h: start.h / state.img.h,
      });
      return;
    }

    // В свободном режиме Shift сохраняет текущие пропорции рамки, не включая
    // «Зафиксировано»: ratio берётся из состояния на момент начала drag.
    const dragRatio = locked() ? effectiveRatio() : event.shiftKey ? state.drag.ratio : null;
    moveOrResizeCrop(
      resizeFrame(
        state.drag.rect,
        dx,
        dy,
        state.drag.kind,
        dragRatio,
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

  // ── КНОПКИ ──────────────────────────────────────────────────────────────
  // «Вписать» — равномерный масштаб текущей рамки до касания ближайших
  // границ изображения (и вверх, и вниз) + центр. Пропорции и режим не меняются.
  buttonEls.fit.addEventListener('click', (event) => {
    event.stopPropagation();
    if (!state.img) return;
    moveOrResizeCrop(fitFrameFrac(state.frac));
  });
  // «По центру» — только перенос центра рамки в центр изображения: размер,
  // пропорции и режим сохраняются, выход за границы разрешён.
  buttonEls.center.addEventListener('click', (event) => {
    event.stopPropagation();
    if (!state.img) return;
    moveOrResizeCrop(centerFrameFrac(state.frac));
  });
  // «Сбросить» — рамка и значения ровно как у подключённого изображения.
  buttonEls.reset.addEventListener('click', (event) => {
    event.stopPropagation();
    resetToImage();
  });

  // ── ПОДСКАЗКА ───────────────────────────────────────────────────────────
  function renderHelp() {
    const t = labels();
    helpBox.textContent = '';
    const title = document.createElement('b');
    title.textContent = t.helpTitle;
    helpBox.appendChild(title);
    for (const line of t.help) {
      const row = document.createElement('div');
      row.textContent = `• ${line}`;
      helpBox.appendChild(row);
    }
    helpBtn.title = t.helpTitle;
  }

  let helpPinned = false;
  helpBtn.addEventListener('mouseenter', () => {
    helpBox.style.display = 'block';
  });
  helpBtn.addEventListener('mouseleave', () => {
    if (!helpPinned) helpBox.style.display = 'none';
  });
  helpBtn.addEventListener('click', (event) => {
    event.stopPropagation();
    helpPinned = !helpPinned;
    helpBox.style.display = helpPinned ? 'block' : 'none';
  });
  helpBox.addEventListener('pointerdown', (event) => event.stopPropagation());

  // ── ЗАГРУЗКА ФАЙЛА ────────────────────────────────────────────────────────
  // Фронтенд рисует иконку загрузки прямо в комбо выбора файла, но без
  // подписи её легко не заметить. Поэтому рядом с комбо есть собственная
  // кнопка «Загрузить файл» (нативный виджет-кнопка LiteGraph) — она
  // выбирает файл, отправляет его в папку input и подставляет имя файла
  // в комбо.
  function setFileValue(value) {
    const target = widgetByName(node, 'image_file');
    if (!target) return;
    const values = target.options?.values;
    if (Array.isArray(values) && !values.includes(value)) values.push(value);
    target.value = value;
    target.callback?.(value, app.canvas, node);
  }

  async function uploadFile(file) {
    try {
      const body = new FormData();
      body.append('image', file);
      body.append('overwrite', 'false');
      body.append('type', 'input');
      const response = await fetch(apiUrl('/upload/image'), { method: 'POST', body });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      const name = data?.name || file.name;
      const subfolder = data?.subfolder || '';
      setFileValue(subfolder ? `${subfolder}/${name}` : name);
    } catch (error) {
      console.warn('[DeggCropImage] не удалось загрузить файл:', error);
    }
  }

  // ── ШТАТНЫЙ MASK EDITOR COMFYUI ─────────────────────────────────────────
  // Фронтенд сохраняет результат рисования в виджет с именем 'image'
  // (writeImageWidgetValue → setNodeWidgetValue(node, 'image', …)), поэтому у
  // ноды должен быть такой виджет. Он скрыт: это служебный канал, а не
  // элемент управления.
  //
  // Чтобы редактор открылся, ноде нужна картинка: фронтенд требует
  // node.imgs (как у LoadImage) — выдаём ему наш <img> из превью.
  function openMaskEditor() {
    const appClass = app?.constructor;
    if (!appClass) return;
    appClass.clipspace_return_node = node;
    if (typeof appClass.open_maskeditor === 'function') {
      appClass.open_maskeditor();
    }
  }

  const maskButton = node.addWidget(
    'button',
    'mask_editor',
    null,
    () => openMaskEditor(),
    {
      serialize: false,
      surfaces: { canvas: 'shown', vueNode: 'shown', panel: 'never' },
    },
  );

  // Значение, которое записал Mask Editor: «имя.png [input]». Переносим его в
  // вход mask_file — по нему сервер прочитает файл и возьмёт маску из альфы.
  const maskEditorWidget = node.addWidget(
    'string',
    'image',
    '',
    (value) => {
      const target = widgetByName(node, 'mask_file');
      if (!target) return;
      const next = typeof value === 'string' ? value : '';
      if (target.value === next) return;
      target.value = next;
      target.callback?.(next, app.canvas, node);
      layout();
    },
    {
      serialize: true,
      surfaces: { canvas: 'never', vueNode: 'never', panel: 'never' },
    },
  );

  const uploadButton = node.addWidget(
    'button',
    'upload_file',
    null,
    () => {
      const picker = document.createElement('input');
      picker.type = 'file';
      picker.accept = 'image/*';
      picker.onchange = () => {
        const file = picker.files?.[0];
        if (file) uploadFile(file);
      };
      picker.click();
    },
    {
      serialize: false,
      surfaces: { canvas: 'shown', vueNode: 'shown', panel: 'never' },
    },
  );

  // ── ЯЗЫК ────────────────────────────────────────────────────────────────
  // Язык берётся из Comfy.Locale при создании ноды и переприменяется при смене
  // настройки — без перезапуска ComfyUI. Значения combo (протокол) не трогаем.
  function applyLanguage() {
    const t = labels();
    for (const [id, key] of buttons) {
      const el = buttonEls[id];
      if (el) {
        el.textContent = t[key];
        el.title = t[`${key}Tip`];
      }
    }
    empty.textContent = t.empty;
    if (uploadButton) uploadButton.label = t.upload;
    if (maskButton) maskButton.label = t.mask;
    renderHelp();
    setOptionLabels(aspectWidget, t.aspect);
    setOptionLabels(fillWidget, t.fill);
    if (lockWidget) {
      lockWidget.options = lockWidget.options || {};
      lockWidget.options.on = t.lockOn;
      lockWidget.options.off = t.lockOff;
      lockWidget.options.label_on = t.lockOn;
      lockWidget.options.label_off = t.lockOff;
      try {
        widgetStore()?.updateOptions?.(widgetIdFor(lockWidget), {
          on: t.lockOn,
          off: t.lockOff,
        });
      } catch {
        /* store may be unavailable in this frontend build */
      }
      node.setDirtyCanvas?.(true, true);
    }
  }

  const onLocaleChange = () => applyLanguage();
  try {
    app?.ui?.settings?.addEventListener?.('Comfy.Locale.change', onLocaleChange);
  } catch {
    /* ignore frontend without legacy settings events */
  }
  applyLanguage();

  // ── ОБРАБОТКА ВИДЖЕТОВ ──────────────────────────────────────────────────
  // Обёртка callback'а: сначала штатная обработка фронтенда, затем политика
  // ноды. Программные записи (state.suppress) политику не запускают.
  function onWidgetChange(widget, handler) {
    if (!widget?.callback) return;
    const original = widget.callback;
    widget.callback = (...args) => {
      const result = original.apply(widget, args);
      if (state.suppress) return result;
      handler(args[0]);
      return result;
    };
  }

  // Пресет пропорций: применяем пропорции и включаем «Зафиксировано».
  onWidgetChange(aspectWidget, () => {
    if (aspectWidget.value === 'free') {
      // «Свободные» снимает фиксацию: рамка остаётся как есть.
      state.ratioLock = null;
      programmatic(lockWidget, false);
      commit();
      return;
    }
    state.ratioLock = null;
    programmatic(lockWidget, true);
    applyRatioPreset(RATIO_VALUES[aspectWidget.value]);
  });

  // «Зафиксировано»: фиксируем ТЕКУЩИЕ пропорции кадра; пресет из списка имеет
  // приоритет.
  onWidgetChange(lockWidget, () => {
    if (lockWidget.value) {
      state.ratioLock = ratio() ? null : cropRatio();
    } else {
      state.ratioLock = null;
      programmatic(aspectWidget, 'free');
    }
    commit();
  });

  // Поля координат: пользователь задал X/Y → рамка едет.
  onWidgetChange(centerXWidget, (value) => {
    setFrameCenter(Number(value), Number(centerYWidget?.value));
  });
  onWidgetChange(centerYWidget, (value) => {
    setFrameCenter(Number(centerXWidget?.value), Number(value));
  });

  // Поля размера результата: та же операция, что перетаскивание мышью, — меняется
  // одна сторона, вторая сохраняется.
  onWidgetChange(frameWWidget, (value) => {
    const width = Number(value);
    if (!Number.isFinite(width) || width <= 0) {
      syncViews();
      return;
    }
    setResultSize(width, resultSize().h);
  });
  onWidgetChange(frameHWidget, (value) => {
    const height = Number(value);
    if (!Number.isFinite(height) || height <= 0) {
      syncViews();
      return;
    }
    setResultSize(resultSize().w, height);
  });

  // Правка мегапикселей возвращает обычный режим (размер по MP).
  onWidgetChange(mpWidget, () => {
    setMegaPixels();
  });

  // Смена выбранного файла (режим «без сокета») — перезагружаем превью.
  onWidgetChange(widgetByName(node, 'image_file'), () => {
    loadSource({ force: true });
  });

  // Изменение «косметики» и параметров выхода перерисовывает сцену сразу:
  // без этого затемнение и плашка отстают до следующего resize/drag.
  for (const widget of [dimWidget, fillWidget, multWidget, goldenWidget]) {
    onWidgetChange(widget, () => {
      commit();
    });
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

  // Фронтенд восстанавливает значения виджетов ПОЗИЦИОННО (настройка
  // Comfy.Workflow.NamedValuesRestore выключена по умолчанию), а порядок
  // виджетов задаёт редактор. Поэтому после восстановления выправляем значения
  // по именам из info.widgets_values_named — иначе старые воркфлои разъедутся.
  function restoreWidgetValuesByName(info) {
    const named = info?.widgets_values_named;
    if (!named) return false;
    let applied = false;
    for (const widget of node.widgets || []) {
      if (!widget || !(widget.name in named)) continue;
      const value = named[widget.name];
      if (widget.value === value) continue;
      widget.value = value;
      applied = true;
    }
    return applied;
  }

  // Подстраховка: если значения приехалиpositional-и с чужими типами (старый
  // воркфлоу без именованной карты), приводим их к корректным.
  function sanitizeWidgetValues() {
    // Комбо выбора файла: в старых воркфлою (без именованной карты значений)
    // фронтенд восстанавливает значения позиционно, и в комбо могло попасть
    // значение чужого виджета (например, булево от «фиксации пропорций»).
    const file = widgetByName(node, 'image_file');
    if (file && typeof file.value !== 'string') {
      const values = file.options?.values;
      file.value = Array.isArray(values) && values.length ? values[0] : '';
    }
    const aspect = widgetByName(node, 'aspect_ratio');
    if (aspect && !Object.prototype.hasOwnProperty.call(RATIO_VALUES, aspect.value)) {
      aspect.value = 'free';
    }
    const lock = widgetByName(node, 'lock_ratio');
    if (lock && typeof lock.value !== 'boolean') lock.value = false;
    for (const name of ['center_x', 'center_y', 'frame_w', 'frame_h']) {
      const widget = widgetByName(node, name);
      if (widget && !Number.isFinite(Number(widget.value))) widget.value = 0;
    }
  }

  const configure = node.onConfigure;
  node.onConfigure = function (info) {
    const result = configure?.apply(this, arguments);
    restoreWidgetValuesByName(info);
    sanitizeWidgetValues();
    state.restoring = true;
    loadStateFromWidgets();
    requestAnimationFrame(() => {
      watchSource();
      loadSource({ force: true });
      commit();
    });
    return result;
  };

  loadStateFromWidgets();
  refreshLayoutSize();
  requestAnimationFrame(() => {
    watchSource();
    loadSource({ force: true });
    commit();
  });

  const onExecuted = () => requestAnimationFrame(() => loadSource());
  api?.addEventListener?.('executed', onExecuted);
  api?.addEventListener?.('b_preview', onExecuted);

  const remove = node.onRemoved;
  node.onRemoved = function () {
    api?.removeEventListener?.('executed', onExecuted);
    api?.removeEventListener?.('b_preview', onExecuted);
    try {
      app?.ui?.settings?.removeEventListener?.('Comfy.Locale.change', onLocaleChange);
    } catch {
      /* ignore */
    }
    detachSource();
    return remove?.apply(this, arguments);
  };

  function refresh() {
    state.restoring = true;
    loadStateFromWidgets();
    loadSource();
    commit();
  }

  return {
    widget: root,
    refresh,
    layout,
    commit,
    state,
    desiredHeight,
    uploadFile,
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
    // Минимальная высота — через ОПЦИИ, а не поздним переопределением
    // computeLayoutSize: фронтенд вычисляет признак «растягиваемый виджет»
    // (hasLayoutSize) при ПЕРВОМ рендере, когда переопределения ещё нет.
    // Без этого строка сетки остаётся 'min-content' и превью не растёт
    // вместе с нодой.
    getMinHeight: () => {
      const editor = node.__deggCrop;
      const height = editor ? editor.desiredHeight() : MIN_H;
      return height + WIDGET_MARGIN * 2;
    },
  });
  widget.serialize = false;

  const editor = createEditor(node, widget);
  container.appendChild(editor.widget);

  // Порядок задаёт WIDGET_ORDER: выбор файла и кнопка загрузки — вверху,
  // следом превью (с кнопками «Вписать / По центру / Сбросить»), потом
  // остальные виджеты. Виджеты, которых нет в списке, уходят в конец.
  const ordered = (node.widgets || []).slice().sort((a, b) => {
    const ia = WIDGET_ORDER.indexOf(a?.name);
    const ib = WIDGET_ORDER.indexOf(b?.name);
    return (ia < 0 ? WIDGET_ORDER.length : ia) - (ib < 0 ? WIDGET_ORDER.length : ib);
  });
  node.widgets.length = 0;
  node.widgets.push(...ordered);

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
