// JS-смоук Degg_Crop_Image: исполняет web/js/crop_image.js в vm-контексте с
// заглушкой window/comfyAPI и проверяет ЧИСТЫЕ функции, которыми редактор
// считает итоговый размер, и регистрацию расширения:
//   • pyRound повторяет банковское округление Python (JS Math.round — вверх);
//   • computeOutputSize совпадает с Python output_size на одних и тех же
//     входах (иначе превью показывает не тот размер, что реально получится);
//   • parseRectFrac/clampFrac не пропускают мусор и держат рамку в границах;
//   • resizeFrame сохраняет пропорцию и неподвижный якорь;
//   • RATIO_VALUES совпадает с Python-словарём;
//   • расширение регистрируется и не трогает чужие ноды.
//
// Запуск:  cd Degg_Crop_Image && node tests/_smoke_crop_image.mjs
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const JS = path.join(ROOT, "web", "js", "crop_image.js");
const PY = path.join(ROOT, "crop_image.py");

const errors = [];
const oks = [];
const check = (label, cond, extra = "") => {
  (cond ? oks : errors).push(label);
  if (!cond) console.log(`  FAIL  ${label}${extra ? "  [" + extra + "]" : ""}`);
  else console.log(`  ok    ${label}`);
};

// ── заглушка Nodes 2.0 ──────────────────────────────────────────────────────
const registered = [];
const ctx = {
  console,
  Math,
  JSON,
  Number,
  String,
  Array,
  Object,
  Date,
  requestAnimationFrame: (fn) => fn(),
  setTimeout,
  clearTimeout,
  document: {
    getElementById: () => null,
    createElement: () => ({ style: {}, appendChild() {}, addEventListener() {} }),
    head: { appendChild() {} },
  },
  window: {
    comfyAPI: {
      app: {
        registerExtension: (ext) => registered.push(ext),
      },
      api: {
        api: {
          addEventListener() {},
          removeEventListener() {},
          apiURL: (p) => p,
        },
      },
    },
  },
};
ctx.globalThis = ctx;
ctx.window.comfyAPI.app.app = { registerExtension: ctx.window.comfyAPI.app.registerExtension };
vm.createContext(ctx);

// Модуль — ESM; для vm убираем только синтаксис экспорта.
const src = fs
  .readFileSync(JS, "utf8")
  .replace(/^export\s+function\s/gm, "function ")
  .replace(/^export\s*\{[^}]*\};?\s*$/gm, "");
// `const` не становится свойством контекста — выносим объект пропорций наружу.
vm.runInContext(src + "\nglobalThis.__RATIOS = RATIO_VALUES;\n", ctx, { filename: JS });

const { pyRound, computeOutputSize, parseRectFrac, clampFrac, resizeFrame } = ctx;

// ── 1. pyRound = Python round() (banker's) ──────────────────────────────────
check("pyRound(0.5) == 0", pyRound(0.5) === 0, pyRound(0.5));
check("pyRound(1.5) == 2", pyRound(1.5) === 2, pyRound(1.5));
check("pyRound(2.5) == 2", pyRound(2.5) === 2, pyRound(2.5));
check("pyRound(3.5) == 4", pyRound(3.5) === 4, pyRound(3.5));
check("pyRound(-0.5) == 0", pyRound(-0.5) === 0, pyRound(-0.5));
check("pyRound(2.4) == 2", pyRound(2.4) === 2);
check("pyRound(2.6) == 3", pyRound(2.6) === 3);

// ── 2. паритет computeOutputSize ↔ Python output_size ───────────────────────
// Значения слева измерены вызовом Python output_size (crop_image.py).
const PARITY = [
  [[1000, 1000, 2, 8], [1448, 1448]],
  [[1920, 1080, 2, 8], [1928, 1088]],
  [[1600, 900, 1, 64], [1344, 768]],
  [[850, 1000, 1, 10], [940, 1110]],
  [[1150, 1000, 1, 64], [1088, 960]],
  [[3000, 2000, 4, 32], [2496, 1664]],
  [[1, 1000, 2, 8], [48, 45792]],
];
for (const [[rw, rh, mp, mult], [pw, ph]] of PARITY) {
  const got = computeOutputSize(rw, rh, mp, mult);
  check(
    `output ${rw}x${rh} @${mp}MP/${mult} -> ${pw}x${ph}`,
    got.w === pw && got.h === ph,
    `${got.w}x${got.h}`,
  );
}
const odd = computeOutputSize(1000, 1000, 2, 12);
check("multiplicity is honoured", odd.w % 12 === 0 && odd.h % 12 === 0, `${odd.w}x${odd.h}`);
const seven = computeOutputSize(1000, 1000, 2, 7);
check("multiplicity 7 honoured", seven.w % 7 === 0 && seven.h % 7 === 0, `${seven.w}x${seven.h}`);
check("degenerate size stays positive", computeOutputSize(0, 0, 0, 0).w >= 1);

// ── 3. parseRectFrac / clampFrac ────────────────────────────────────────────
const full = { x: 0, y: 0, w: 1, h: 1 };
for (const raw of ["", "   ", "not json", "{}", [], [1, 2, 3], null, "null", { x: "a" }]) {
  const r = parseRectFrac(raw);
  check(
    `parseRectFrac(${JSON.stringify(raw)}) -> full`,
    r.x === 0 && r.y === 0 && r.w === 1 && r.h === 1,
    JSON.stringify(r),
  );
}
const arr = parseRectFrac("[0.1,0.2,0.3,0.4]");
check("array form is accepted", arr.x === 0.1 && arr.h === 0.4, JSON.stringify(arr));
const obj = parseRectFrac({ x: -20, y: 5, w: 500, h: 0 });
check("out of range is clamped", obj.x === -20 && obj.w === 100 && obj.h === 1e-4, JSON.stringify(obj));

const clamped = clampFrac({ x: 1e9, y: -1e9, w: 0, h: 1e9 });
check(
  "clampFrac bounds",
  clamped.x === 100 && clamped.y === -100 && clamped.w === 1e-4 && clamped.h === 100,
  JSON.stringify(clamped),
);

// ── 4. resizeFrame ─────────────────────────────────────────────────────────
const start = { x: 100, y: 100, w: 200, h: 200 };
const plain = resizeFrame(start, 50, 0, "e", 0, 1000, 1000);
check("drag east moves only the right edge", plain.x === 0.1 && plain.w === 0.25, JSON.stringify(plain));
check("drag east keeps the height", plain.y === 0.1 && plain.h === 0.2, JSON.stringify(plain));

const locked = resizeFrame(start, 50, 0, "e", 1, 1000, 1000);
check(
  "ratio lock keeps w/h",
  Math.abs(locked.w / locked.h - 1) < 1e-9,
  JSON.stringify(locked),
);
check("ratio lock grows from the west edge", Math.abs(locked.x - 0.1) < 1e-9, JSON.stringify(locked));

const west = resizeFrame(start, 20, 0, "w", 1, 1000, 1000);
check(
  "west drag keeps the east edge",
  Math.abs(west.x + west.w - 0.3) < 1e-9,
  JSON.stringify(west),
);

const tiny = resizeFrame(start, -999, 0, "e", 0, 1000, 1000);
check(
  "drag stops at the 16px minimum",
  Math.abs(tiny.w - 16 / 1000) < 1e-9,
  JSON.stringify(tiny),
);

const beyond = resizeFrame({ x: 0.9, y: 0.9, w: 0.2, h: 0.2 }, 500, 500, "se", 0, 100, 100);
check("result stays normalized", beyond.w >= 1e-4 && beyond.x <= 100, JSON.stringify(beyond));

// ── 5. RATIO_VALUES ↔ Python ───────────────────────────────────────────────
const py = fs.readFileSync(PY, "utf8");
const block = py.slice(py.indexOf("RATIO_VALUES = {"), py.indexOf("}", py.indexOf("RATIO_VALUES = {")));
const pyRatios = {};
for (const m of block.matchAll(/"(?<k>[0-9]+:[0-9]+)":\s*(?<expr>[0-9./\s]+),/g)) {
  pyRatios[m.groups.k] = Function(`return ${m.groups.expr.trim()}`)();
}
const jsRatios = ctx.__RATIOS;
check("ratio keys match python", Object.keys(pyRatios).join(",") === Object.keys(jsRatios).join(","), Object.keys(jsRatios).join(","));
for (const key of Object.keys(pyRatios)) {
  check(
    `ratio ${key} matches python`,
    Math.abs(pyRatios[key] - jsRatios[key]) < 1e-12,
    `${pyRatios[key]} vs ${jsRatios[key]}`,
  );
}

// ── 6. регистрация расширения ──────────────────────────────────────────────
check("one extension registered", registered.length === 1, String(registered.length));
const ext = registered[0] ?? {};
check("extension name", ext.name === "Degg.CropImage", String(ext.name));
check("has nodeCreated", typeof ext.nodeCreated === "function");

// Чужая нода не должна получать DOM-виджет.
let added = 0;
const foreign = {
  comfyClass: "KSampler",
  type: "KSampler",
  widgets: [],
  addDOMWidget() {
    added += 1;
    return { options: {} };
  },
};
try {
  ext.nodeCreated(foreign);
  check("foreign node is ignored", added === 0, String(added));
} catch (e) {
  check("foreign node is ignored", false, String(e));
}

// Повторный вызов для уже настроенной ноды не создаёт второй превью.
const ready = { comfyClass: "DeggCropImage", type: "DeggCropImage", widgets: [], __deggCrop: {}, addDOMWidget() { added += 1; } };
try {
  ext.nodeCreated(ready);
  check("setup is idempotent", added === 0, String(added));
} catch (e) {
  check("setup is idempotent", false, String(e));
}

// ── итог ───────────────────────────────────────────────────────────────────
console.log("");
console.log(`ok: ${oks.length}   FAIL: ${errors.length}`);
if (errors.length) {
  console.log("SMOKE FAIL");
  process.exit(1);
}
console.log("SMOKE OK");
