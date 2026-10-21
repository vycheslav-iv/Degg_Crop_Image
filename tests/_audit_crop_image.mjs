// Статический аудит Degg_Crop_Image — ловит расхождения, которые не видны
// функциональным прогонам: контракт Python ↔ JS, протокольные значения combo,
// запрещённые приёмы Nodes 2.0 и дыры в локализации.
//
// Запуск:  cd Degg_Crop_Image && node tests/_audit_crop_image.mjs
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const PY = fs.readFileSync(path.join(ROOT, "crop_image.py"), "utf8");
const INIT = fs.readFileSync(path.join(ROOT, "__init__.py"), "utf8");
const JS = fs.readFileSync(path.join(ROOT, "web", "js", "crop_image.js"), "utf8");
const RU = JSON.parse(fs.readFileSync(path.join(ROOT, "locales", "ru", "nodeDefs.json"), "utf8"));
const EN = JSON.parse(fs.readFileSync(path.join(ROOT, "locales", "en", "nodeDefs.json"), "utf8"));

const problems = [];
const oks = [];
const check = (label, cond, extra = "") => {
  (cond ? oks : problems).push(label);
  console.log(`  ${cond ? "ok  " : "FAIL"}  ${label}${!cond && extra ? "  [" + extra + "]" : ""}`);
};

const NODE = "DeggCropImage";
const typeOf = (name) => {
  const m = PY.match(new RegExp(`"${name}":\\s*\\(([^)]*)\\)`, "s"));
  return m ? m[1].trim() : "";
};

// ── Python: каркас ──────────────────────────────────────────────────────────
check("Python: класс DeggCropImage объявлен", /class\s+DeggCropImage\b/.test(PY));
check("Python: CATEGORY = My_custom_nodes/Image", /CATEGORY\s*=\s*"My_custom_nodes\/Image"/.test(PY));
check("Python: FUNCTION = crop", /FUNCTION\s*=\s*"crop"/.test(PY));
check("Python: def crop определён", /\bdef\s+crop\s*\(/.test(PY));
check(
  "Python: RETURN_TYPES (IMAGE, MASK, INT, INT)",
  /RETURN_TYPES\s*=\s*\("IMAGE",\s*"MASK",\s*"INT",\s*"INT"\)/.test(PY),
);
check(
  "Python: RETURN_NAMES совпадают по смыслу",
  /RETURN_NAMES\s*=\s*\("image",\s*"mask",\s*"width",\s*"height"\)/.test(PY),
);
check("Python: DESCRIPTION есть", /DESCRIPTION\s*=\s*\(/.test(PY));
check("Python: нет IS_CHANGED (кэш зависит от входа)", !/def\s+IS_CHANGED/.test(PY));

// ── Python: входы ───────────────────────────────────────────────────────────
const required = PY.slice(PY.indexOf('"required": {'), PY.indexOf('"optional": {'));
const inputNames = [...required.matchAll(/^ {16}"?([a-z_]+)"?:\s*\(/gm)].map((m) => m[1]);
const EXPECTED = [
  "image",
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
  "crop_rect",
  "output_size",
];
check("Python: набор входов", inputNames.join(",") === EXPECTED.join(","), inputNames.join(","));
check("Python: crop_rect — скрытый STRING", /"crop_rect":\s*\("STRING",\s*HIDDEN_OPTIONS\)/.test(PY));
check("Python: hidden-опции не сериализуются в UI", /HIDDEN_OPTIONS\s*=\s*\{[^}]*"hidden"\s*:\s*True/.test(PY));
check("Python: crop_rect по умолчанию полный кадр", /DEFAULT_RECT\s*=\s*\{[^}]*"w":\s*1\.0/.test(PY));
check("Python: mask — необязательный MASK", /"mask":\s*\(\s*"MASK"/.test(PY));
check("Python: lock_ratio по умолчанию включён", /"lock_ratio":[\s\S]{0,120}?"default":\s*True/.test(PY));
check("Python: dim_outside 0..100", /"dim_outside":[\s\S]{0,160}?"max":\s*100/.test(PY));
const multBlock = PY.slice(PY.indexOf('"multiplicity":'), PY.indexOf('"resample":'));
check("Python: кратность 8..128 шагом 4 (как ResolutionSelector)", /"min":\s*8,/.test(multBlock) && /"max":\s*128,/.test(multBlock) && /"step":\s*4,/.test(multBlock));
const mpBlock = PY.slice(PY.indexOf('"megapixels":'), PY.indexOf('"resample":'));
check(
  "Python: мегапиксели 0.1..64, шаг 0.1 (ТЗ §7.2)",
  /"min":\s*0\.1,/.test(mpBlock) && /"max":\s*64\.0,/.test(mpBlock) && /"step":\s*0\.1,/.test(mpBlock),
);
check("Python: multiplicity по умолчанию 8", /"multiplicity":[\s\S]{0,120}?"default":\s*8/.test(PY));
check("Python: megapixels по умолчанию 2.0", /"megapixels":[\s\S]{0,120}?"default":\s*2\.0/.test(PY));
check("Python: 1 MP = 1024² (как ResolutionSelector)", /1024\.0 \* 1024\.0/.test(PY));
check("Python: resample по умолчанию lanczos", /"resample":[\s\S]{0,120}?"default":\s*"lanczos"/.test(PY));
check("Python: fill по умолчанию transparent", /"fill":[\s\S]{0,120}?"default":\s*"transparent"/.test(PY));

// ── Python: протокольные значения enum ─────────────────────────────────────
const RATIOS = ["free", "1:1", "2:3", "3:2", "4:3", "3:4", "16:9", "9:16", "21:9"];
const ASPECT_BLOCK = PY.slice(PY.indexOf("ASPECT_RATIOS"), PY.indexOf("]", PY.indexOf("ASPECT_RATIOS")));
const RESAMPLE_BLOCK = PY.slice(PY.indexOf("RESAMPLE_METHODS"), PY.indexOf("]", PY.indexOf("RESAMPLE_METHODS")));
const FILL_BLOCK = PY.slice(PY.indexOf("FILL_MODES"), PY.indexOf("]", PY.indexOf("FILL_MODES")));
for (const key of RATIOS) {
  check(`Python: aspect_ratio содержит "${key}"`, ASPECT_BLOCK.includes(`"${key}"`));
}
for (const key of ["lanczos", "bicubic", "bilinear", "nearest-exact"]) {
  check(`Python: resample содержит "${key}"`, RESAMPLE_BLOCK.includes(`"${key}"`));
}
for (const key of ["transparent", "black", "white", "gray"]) {
  check(`Python: fill содержит "${key}"`, FILL_BLOCK.includes(`"${key}"`));
}

// ── Python: реализация ─────────────────────────────────────────────────────
check("Python: банковское округление вынесено в py_round", /def\s+py_round\b/.test(PY));
check("Python: snap использует py_round", /def\s+snap[\s\S]{0,200}?py_round\(/.test(PY));
check("Python: шаг кратности применяется как есть", /step = max\(1, int\(multiplicity\)\)/.test(PY));
check("Python: multiplicity 8..128 step 4 (как ResolutionSelector)", /"multiplicity":[\s\S]{0,120}?"min":\s*8[\s\S]{0,80}?"max":\s*128[\s\S]{0,80}?"step":\s*4/.test(PY));
check("Python: кадр хранится в долях (нормализован)", /MIN_FRACTION|MAX_FRACTION/.test(PY));
check("Python: mask заменяет альфу источника", /mask is not None/.test(PY));
check("Python: нет транспортного хака _mp в rect", !/_mp\b/.test(PY));
check("Python: нет folder_paths/server (нода без ComfyUI)", !/^\s*(import|from)\s+(folder_paths|server|aiohttp)\b/m.test(PY));

// ── __init__.py ────────────────────────────────────────────────────────────
check("init: WEB_DIRECTORY = ./web", /WEB_DIRECTORY\s*=\s*"\.\/web"/.test(INIT));
check("init: оба маппинга", /NODE_CLASS_MAPPINGS/.test(INIT) && /NODE_DISPLAY_NAME_MAPPINGS/.test(INIT));
check("Python: ключ маппинга DeggCropImage", new RegExp(`"${NODE}":\\s*DeggCropImage`).test(PY));

// ── JS: импорт и Nodes 2.0 ─────────────────────────────────────────────────
check("JS: только window.comfyAPI (нет scripts/app.js)", !/scripts\/app\.js/.test(JS) && /window\.comfyAPI/.test(JS));
check("JS: имя класса совпадает с Python", new RegExp(`NODE_CLASS\\s*=\\s*'${NODE}'`).test(JS));
check("JS: имя виджета crop_rect совпадает", /widgetByName\(node,\s*'crop_rect'\)/.test(JS));
check("JS: превью через addDOMWidget", /addDOMWidget\(/.test(JS));
check("JS: панель свойств скрыта (hideInPanel)", /hideInPanel:\s*true/.test(JS));
check(
  "JS: DOM-виджет не спрятан из Vue-нод (surfaces.vueNode != never)",
  !/surfaces:\s*\{[^}]*vueNode:\s*'never'/.test(JS) && !/canvasOnly:\s*true/.test(JS),
);
check("JS: hideOnZoom: false", /hideOnZoom:\s*false/.test(JS));
check("JS: serialize: false", /serialize:\s*false/.test(JS));
check("JS: нет перезаписи this.computeSize", !/this\.computeSize\s*=/.test(JS));
check("JS: нет setInterval/setTimeout-поллинга", !/setInterval\s*\(/.test(JS));
check("JS: нет window.addEventListener", !/window\.addEventListener/.test(JS));
check("JS: нет options.disabled (мёртвое поле)", !/options\.disabled/.test(JS));
check("JS: нет canvas-рисования", !/createCanvas|getContext\s*\(/.test(JS));
const sizeFn = JS.slice(JS.indexOf("function desiredHeight()"), JS.indexOf("function refreshLayoutSize()"));
check("JS: sizing без замеров DOM (desiredHeight)", !/scrollHeight|offsetHeight|clientHeight|getBoundingClientRect/.test(sizeFn));
check("JS: нет plScale", !/plScale/.test(JS));
check("JS: layout через computeLayoutSize", /computeLayoutSize/.test(JS));
check("JS: затемнение подписью theme (dim_outside)", /dim_outside/.test(JS));
check("JS: подписи combo через getOptionLabel", /getOptionLabel/.test(JS));
check("JS: язык из Comfy.Locale", /Comfy\.Locale/.test(JS));
check("JS: оба языка в LABELS", /ru:\s*\{/.test(JS) && /en:\s*\{/.test(JS));
check("JS: CSS вставляется один раз", /getElementById\(CSS_ID\)/.test(JS));
check(
  "JS: состояние пишется в crop_rect при drag",
  /cropWidget\.value\s*=\s*value/.test(JS) && /cropWidget\s*=\s*widgetByName\(node,\s*'crop_rect'\)/.test(JS),
);
check("JS: подписка на executed снимается в onRemoved", /onRemoved[\s\S]{0,300}?removeEventListener/.test(JS));
check("JS: сериализация через requestAnimationFrame", /function persistState\(\)[\s\S]{0,1200}?requestAnimationFrame/.test(JS));
check("JS: сброс кадра кнопкой есть", /reset/i.test(JS) && /Сброс|Reset/.test(JS));
check("JS: кнопки вписывания (fit/center)", /fit/i.test(JS) && /center/i.test(JS));
check("JS: «Вписать» через fitFrameFrac", /buttonEls\.fit[\s\S]{0,200}?fitFrameFrac\(state\.frac\)/.test(JS));
check("JS: «По центру» через centerFrameFrac", /buttonEls\.center[\s\S]{0,200}?centerFrameFrac\(state\.frac\)/.test(JS));
check(
  "JS: «Сбросить» = рамка 0,0,1,1 + свободные + замок off + ручной размер сброшен",
  /function resetCrop\(\)[\s\S]{0,400}?state\.frac = \{ x: 0, y: 0, w: 1, h: 1 \}[\s\S]{0,220}?state\.manual = null[\s\S]{0,220}?programmatic\(lockWidget, false\)[\s\S]{0,220}?programmatic\(aspectWidget, 'free'\)/.test(JS),
);
check(
  "JS: «Сбросить» не применяет пропорцию (нет applyRatioPreset в resetCrop)",
  !/function resetCrop\(\)[\s\S]{0,700}?applyRatioPreset/.test(JS),
);
const lockBlock = (JS.split("onWidgetChange(lockWidget")[1] || "").split("onWidgetChange(centerXWidget")[0];
check(
  "JS: замок фиксирует ТЕКУЩИЕ пропорции рамки (cropRatio, без applyRatioPreset)",
  /cropRatio\(\)/.test(lockBlock) && !/applyRatioPreset/.test(lockBlock),
);
check(
  "JS: выключение замка переводит «Пропорции» в свободные",
  /programmatic\(aspectWidget, 'free'\)/.test(lockBlock),
);
const aspectBlock = (JS.split("onWidgetChange(aspectWidget")[1] || "").split("onWidgetChange(lockWidget")[0];
check(
  "JS: выбор пресета пропорций включает замок и применяет его",
  /programmatic\(lockWidget, true\)[\s\S]{0,140}?applyRatioPreset\(/.test(aspectBlock),
);
check(
  "JS: «Свободные» снимает замок без изменения рамки",
  /aspectWidget\.value === 'free'[\s\S]{0,220}?programmatic\(lockWidget, false\)/.test(aspectBlock)
    && !/aspectWidget\.value === 'free'[\s\S]{0,220}?applyRatioPreset/.test(aspectBlock),
);
check(
  "JS: свободный режим + Shift сохраняет текущие пропорции при ресайзе",
  /const dragRatio = locked\(\) \? effectiveRatio\(\) : event\.shiftKey \? state\.drag\.ratio : null/.test(JS),
);
check(
  "JS: Shift при перемещении фиксирует ось (axisDelta)",
  /event\.shiftKey \? axisDelta\(dx, dy\)/.test(JS),
);
check(
  "JS: координаты центра рамки — пересчёт в пикселях через нативные виджеты",
  /frameCenterOffset\(state\.frac, state\.img\.w, state\.img\.h\)/.test(JS)
    && /moveFrameToOffset\(state\.frac, state\.img\.w, state\.img\.h, offsetX, offsetY\)/.test(JS),
);
check(
  "JS: подсказка «?» собирается из LABELS (ru/en) и перерисовывается при смене языка",
  /const helpBtn = make\('button', 'degg-crop__help'/.test(JS)
    && /function renderHelp\(\)/.test(JS)
    && /for \(const line of t\.help\)/.test(JS)
    && /renderHelp\(\);/.test(JS),
);
check(
  "locales: подписи координат RU/EN (у нативных виджетов)",
  (RU[NODE]?.inputs?.center_x?.name ?? "") === "Центр рамки X"
    && (RU[NODE]?.inputs?.center_y?.name ?? "") === "Центр рамки Y"
    && (EN[NODE]?.inputs?.center_x?.name ?? "") === "Frame centre X"
    && (EN[NODE]?.inputs?.center_y?.name ?? "") === "Frame centre Y",
);
check("JS: подписи замка локализуются (options.on/off)", /lockWidget\.options\.on = t\.lockOn/.test(JS) && /lockWidget\.options\.off = t\.lockOff/.test(JS));
check(
  "JS: смена языка на лету (Comfy.Locale + removeEventListener)",
  /addEventListener\?\.\('Comfy\.Locale\.change'/.test(JS) && /removeEventListener\?\.\('Comfy\.Locale\.change'/.test(JS),
);
check("JS: RU-кнопки «Вписать/По центру/Сбросить»", /fit: 'Вписать'/.test(JS) && /center: 'По центру'/.test(JS) && /reset: 'Сбросить'/.test(JS));
check("JS: нет старого «Вписать всё»/Fit all", !/Вписать всё|Fit all|fitAll|fitCenter/.test(JS));
check("JS: замок RU «Зафиксировано/Свободно»", /lockOn: 'Зафиксировано'/.test(JS) && /lockOff: 'Свободно'/.test(JS));
check("JS: замок EN «Locked/Free»", /lockOn: 'Locked'/.test(JS) && /lockOff: 'Free'/.test(JS));
check("JS: превью берёт nodePreviewImages|nodeOutputs", /nodePreviewImages|nodeOutputs/.test(JS));
check("JS: превью подписано на subgraph-резолв", /isSubgraphNode|resolveSubgraphOutputLink/.test(JS));
check(
  "JS: заливка создаётся ПЕРЕД изображением (слой аутпейнта под кадром)",
  JS.indexOf("degg-crop__fill', stage") > 0
    && JS.indexOf("degg-crop__fill', stage") < JS.indexOf("degg-crop__img', stage"),
);
check(
  "JS: метод масштабирования не переводится (значения как в стандартных нодах)",
  !/setOptionLabels\(\s*resampleWidget/.test(JS) && !/resample:\s*\{/.test(JS),
);

// ── Локализация ────────────────────────────────────────────────────────────
const locale = (obj) => obj[NODE] ?? {};
for (const [lang, data] of [["ru", RU], ["en", EN]]) {
  const node = locale(data);
  check(`locales/${lang}: узел ${NODE} описан`, Boolean(node && node.display_name), lang);
  check(`locales/${lang}: есть description`, Boolean(node.description), lang);
  const inputs = node.inputs ?? {};
  for (const name of inputNames) {
    check(`locales/${lang}: вход ${name} переведён`, Boolean(inputs[name]?.name), name);
  }
  check(`locales/${lang}: mask переведён`, Boolean(inputs.mask?.name), lang);
  for (const slot of ["0", "1", "2", "3"]) {
    check(`locales/${lang}: выход ${slot} переведён`, Boolean(node.outputs?.[slot]?.name), slot);
  }
}

// Значения combo не переводятся: ключи подписей пропорций = протокольные значения.
const jsRatioBlock = JS.slice(JS.indexOf("const RATIO_VALUES = {"), JS.indexOf("};", JS.indexOf("const RATIO_VALUES = {")));
const jsRatioKeys = [...jsRatioBlock.matchAll(/'([0-9]+:[0-9]+)'\s*:/g)].map((m) => m[1]);
const pyRatioKeys = RATIOS.filter((k) => k !== "free");
check("JS: RATIO_VALUES повторяет ключи Python", jsRatioKeys.join(",") === pyRatioKeys.join(","), jsRatioKeys.join(","));

const labelBlock = JS.slice(JS.indexOf("aspect: {"), JS.indexOf("}", JS.indexOf("aspect: {")));
for (const key of pyRatioKeys) {
  check(`JS: подпись пропорции "${key}" не меняет значение`, labelBlock.includes(`'${key}':`));
}

// Превью обязано реагировать на смену изображения в upstream-ноде: загрузка
// в LoadImage пишет app.nodeOutputs[key] и зовёт node.onWidgetChanged, поэтому
// редактор должен подписаться на этот хук источника.
check(
  "JS: подписка на смену виджета upstream (onWidgetChanged)",
  /watchedSource\.onWidgetChanged\s*=\s*watchedHandler/.test(JS),
);
check(
  "JS: смена входа перерисовывает превью принудительно",
  /loadSource\(\{\s*force:\s*true\s*\}\)/.test(JS),
);
// Nodes 2.0 обновляет виджет через createWidgetUpdateHandler: зовёт
// widget.callback, но НЕ node.onWidgetChanged, поэтому подписки на хук мало —
// callback'и виджетов источника обязаны быть обёрнуты.
check(
  "JS: Nodes 2.0 — превью реагирует на widget.callback upstream",
  /widget\.callback\s*=\s*wrapper/.test(JS) &&
    /watchedWidgets\.push\(\{\s*widget,\s*original,\s*wrapper\s*\}\)/.test(JS),
);
check(
  "JS: watchSource подключает обёртку callback'ов источника",
  /function watchSource\(\)[\s\S]*?watchSourceWidgets\(source\);/.test(JS),
);
check(
  "JS: перезаливка файла с тем же именем перерисовывает превью",
  /loadSource\(\{\s*force:\s*sourceOutputsChanged\(\)\s*\}\)/.test(JS) &&
    /const\s+changed\s*=\s*next\s*!==\s*watchedOutputs/.test(JS),
);

// ── Drag: масштаб сцены фиксируется на pointerdown ───────────────────────────
// Иначе рост рамки меняет union → scale → следующий pointermove считает
// дельту по новому масштабу, и рамка разгоняется до клампа.
check(
  "JS: масштаб и смещение сцены фиксируются на pointerdown",
  /state\.drag\s*=\s*\{[\s\S]{0,400}?view\s*,/.test(JS) &&
    /(view\.scale\s*=\s*state\.scale|scale:\s*state\.scale)/.test(JS) &&
    /(view\.offX\s*=\s*state\.offX|offX:\s*state\.offX)/.test(JS) &&
    /(view\.offY\s*=\s*state\.offY|offY:\s*state\.offY)/.test(JS),
);
check(
  "JS: pointermove считает точку по зафиксированному view",
  /sourceFromLocal\(localFromEvent\(event,\s*state\.drag\.view\),\s*state\.drag\.view\)/.test(JS),
);

// ── Координаты — нативные опции ноды, «?» — в превью ────────────────────────
check(
  "Python: координаты — INT-опции сразу под lock_ratio",
  /"lock_ratio":[\s\S]{0,900}?"center_x":\s*\(\s*"INT"/.test(PY) &&
    /"center_y":\s*\(\s*"INT"/.test(PY),
);
check(
  "JS: собственной строки координат в DOM нет",
  !/degg-crop__coords/.test(JS) && !/degg-crop__num/.test(JS),
);
check(
  "JS: координаты живут в нативных виджетах center_x/center_y",
  /widgetByName\(node,\s*'center_x'\)/.test(JS) &&
    /widgetByName\(node,\s*'center_y'\)/.test(JS),
);
check(
  "JS: единственная точка записи в виджет (writeWidget), тихая по умолчанию",
  /function writeWidget\(widget, value, \{ call = false \} = \{\}\)/.test(JS) &&
    /writeWidget\(centerXWidget, Math\.round\(offset\.x\)\)/.test(JS) &&
    /writeWidget\(centerYWidget, Math\.round\(offset\.y\)\)/.test(JS) &&
    !/function setWidgetValueQuiet/.test(JS) &&
    !/function setWidgetValue\(/.test(JS),
);
check(
  "JS: программная запись (programmatic) вызывает callback под защитой suppress",
  /function programmatic\(widget, value\)[\s\S]{0,300}?state\.suppress \+= 1/.test(JS) &&
    /state\.suppress -= 1/.test(JS) &&
    /writeWidget\(widget, value, \{ call: true \}\)/.test(JS),
);
check(
  "JS: правка координат двигает рамку (setFrameCenter → moveFrameToOffset)",
  /function setFrameCenter\(offsetX, offsetY\)[\s\S]{0,400}?moveFrameToOffset\(/.test(JS) &&
    /onWidgetChange\(centerXWidget[\s\S]{0,200}?setFrameCenter\(/.test(JS) &&
    /onWidgetChange\(centerYWidget[\s\S]{0,200}?setFrameCenter\(/.test(JS),
);
check(
  "JS: кнопка «?» живёт в превью (внутри stage), а подсказка — в корне",
  /make\('button',\s*'degg-crop__help',\s*stage\)/.test(JS) &&
    /make\('div',\s*'degg-crop__helpbox',\s*root\)/.test(JS),
);

// ── Ручной размер рамки (ширина/высота в пикселях) ──────────────────────────
check(
  "Python: размер рамки — INT-опции сразу под координатами",
  /"center_y":[\s\S]{0,900}?"frame_w":\s*\(\s*"INT"/.test(PY) &&
    /"frame_h":\s*\(\s*"INT"/.test(PY),
);
check(
  "JS: итоговый размер результата (ручной или по мегапикселям)",
  /function resultSize\(\)[\s\S]{0,400}?snapSize\(state\.manual/.test(JS) &&
    /function resultSize\(\)[\s\S]{0,400}?computeOutputSize\(cropPx\(\)\.w, cropPx\(\)\.h/.test(JS),
);
check(
  "JS: плашка показывает ИТОГОВЫЙ размер (а не размер кадра)",
  /function layoutBadge\(\)[\s\S]{0,200}?const out = resultSize\(\)/.test(JS),
);
check(
  "JS: размер пишется в нативные виджеты тихо (frame_w/frame_h)",
  /widgetByName\(node,\s*'frame_w'\)/.test(JS) &&
    /widgetByName\(node,\s*'frame_h'\)/.test(JS) &&
    /writeWidget\(frameWWidget, out\.w\)/.test(JS) &&
    /writeWidget\(frameHWidget, out\.h\)/.test(JS),
);
check(
  "JS: правка размера задаёт итоговый размер и подгоняет пропорции кадра",
  /function setResultSize\(width, height\)[\s\S]{0,600}?snapSize\(/.test(JS) &&
    /function setResultSize\(width, height\)[\s\S]{0,900}?frameWithRatio\(/.test(JS) &&
    /onWidgetChange\(frameWWidget/.test(JS) &&
    /onWidgetChange\(frameHWidget/.test(JS),
);
check(
  "JS: ручной размер хранится в скрытом output_size и переживает перезагрузку",
  /widgetByName\(node,\s*'output_size'\)/.test(JS) &&
    /function persistState\(\)[\s\S]{0,900}?outputSizeWidget/.test(JS) &&
    /parseManualSize\(outputSizeWidget\?\.value\)/.test(JS),
);
check(
  "JS: правка мегапикселей возвращает режим «по мегапикселям»",
  /function setMegaPixels\(\)[\s\S]{0,200}?state\.manual = null/.test(JS) &&
    /onWidgetChange\(mpWidget[\s\S]{0,200}?setMegaPixels\(\)/.test(JS),
);
check(
  "JS: в ручном режиме мегапиксели показывают эквивалент результата",
  /function syncViews\(\)[\s\S]{0,900}?writeWidget\(mpWidget, \(out\.w \* out\.h\) \/ MEGAPIXEL_UNIT\)/.test(JS),
);
check(
  "Python: ручной размер результата учитывается при расчёте",
  /def output_size\([\s\S]{0,400}?manual: tuple\[int, int\] \| None = None/.test(PY) &&
    /if manual is not None:/.test(PY) &&
    /parse_output_size\(output_size\)/.test(PY),
);

check(
  "JS: ручной размер сверяется с пропорциями кадра (инвариант)",
  /function dropStaleManual\(\)[\s\S]{0,600}?state\.manual = null/.test(JS) &&
    /ratiosMatch\(state\.frac, state\.manual, state\.img\.w, state\.img\.h\)/.test(JS),
);
check(
  "JS: свободный drag подстраивает ручной размер (пропорции результата следуют за кадром)",
  /function moveOrResizeCrop\(frac\)[\s\S]{0,600}?fracRatio\(frac, state\.img\.w, state\.img\.h\)/.test(JS) &&
    /state\.manual = \{ w: state\.manual\.w, h: state\.manual\.w \/ ratio \}/.test(JS),
);
check(
  "JS: сверка устаревшего ручного размера — до применения состояния, а не в отрисовке",
  !/function layout\(\)[\s\S]{0,400}?dropStaleManual\(\)/.test(JS),
);

check(
  "JS: итоговый размер считается по ПИКСЕЛЯМ кадра (как в Python), а не по долям",
  /function resultSize\(\)[\s\S]{0,400}?computeOutputSize\(cropPx\(\)\.w, cropPx\(\)\.h/.test(JS) &&
    !/computeOutputSize\(state\.frac\.w, state\.frac\.h/.test(JS),
);

// ── Итог ───────────────────────────────────────────────────────────────────
console.log("");
console.log(`ok: ${oks.length}   FAIL: ${problems.length}`);
if (problems.length) {
  console.log("аудит НЕ чист");
  process.exit(1);
}
console.log("аудит чист");
