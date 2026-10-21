# Память сессии — Degg Crop Image (2026-10-21, ночь)

> Покажи этот файл агенту, чтобы продолжить работу.
> Всегда сверяйся с `AGENTS.md`, `SPECIFICATION.md` и `Техническое задание.txt`.

---

## 1. Что делали в этой сессии (кратко)
- «Зафиксировано» фиксирует ТЕКУЩИЕ пропорции рамки; выбор пресета применяет
  его и включает замок; «Свободные» снимают.
- Shift-приёмы: перенос строго по одной оси, ресайз края с сохранением пропорций.
- Координаты центра рамки — нативные опции `center_x`/`center_y` под «Фиксацией
  пропорций» (были самодельные `<input>` в DOM — не как у остальных, не в workflow).
- РАЗМЕР РАМКИ — нативные опции `frame_w`/`frame_h` (px) под координатами:
  та же операция, что тянучка мышью (свободно — одна сторона, при фиксации —
  вторая следует), но ЦЕНТР НЕ УЕЗЖАЕТ.
- Кнопка «?» — в правом верхнем углу превью; подсказка в корне.
- Починен разгон рамки при drag: масштаб/смещение сцены фиксируются на
  `pointerdown` (`state.drag.view`).
- Скилы: `comfyui-localization` §6.1, новый `comfyui-drag-view-pinning`,
  `comfyui-js-extension` § «Запись значения виджета ИЗ JS»,
  `comfyui-native-widget-look` §0.

## 2. Итоговое состояние кода
- `crop_image.py` — входы (порядок = порядок виджетов): `lock_ratio`, `center_x`,
  `center_y`, `frame_w`, `frame_h`, `aspect_ratio`, `megapixels`, `multiplicity`,
  `resample`, `fill`, `dim_outside`, `crop_rect` (скрыт).
  `center_x/center_y/frame_w/frame_h` — UI-поля: рамку считает `crop_rect`.
- `web/js/crop_image.js` — `frameWithSize(rect, imgW, imgH, size, axis, ratio,
  minPx=16)`: размер в px, центр сохраняется, минимум 16 px, аутпейнт разрешён;
  свободный режим меняет одну сторону, при `effectiveRatio()` вторая следует.
- `web/js/crop_image.js` — `syncSize()` (тихая запись `frame_w`/`frame_h` из
  `pxRect()`), `applySize(axis, value)` (`frameWithSize` + `effectiveRatio()`),
  обёртки callback'ов `frame_w`/`frame_h`; `setFrac()` зовёт и `syncCoords()`,
  и `syncSize()`.
- `web/js/crop_image.js` — `state.drag.view` (rect/zoom/scale/offX/offY) на
  `pointerdown`; `state.ratioLock`, `state.suppress`, `setWidgetValueQuiet`.
- Проверки: Python 92, смоук 72, аудит 166, живая DOM-проба 78 — всё зелёное;
  `python _process/check.py Degg_Crop_Image` → «ЗЕЛЁНОЕ: провалов 0».

## 3. Проблемы, которые встречались (и как решали)
- Разгон рамки при drag (доля 88, пропорции 1:1) → фиксация `state.drag.view`.
- Ложные «пропорция не 16:9» / «координаты не те» / «Shift не держит ось» в пробе:
  пропорции считать по `frac`×`natural`, ожидания — от `natural`, для Shift-движения
  брать рамку крупнее зоны захвата края (12 px).
- Слабые детекторы (найдены мутациями) → ужесточены до конкретных форм.
- Сервер не подхватил новые входы: ComfyUI был запущен ДО `sync.py` → порт
  8188 держал старый Python; лечится: стоп → sync → старт.
- `G:\ИИ web\stop-comfyui.bat`: первая версия врала (кавычки ломали `for /f`,
  редирект у `taskkill` давал «Input redirection is not supported») → починено.

## 4. Что важно не сломать при продолжении работы
- РАЗМЕР рамки: свободно — одна сторона; при фиксации — вторая следует; ЦЕНТР
  НЕ УЕЗЖАЕТ (позиция и размер — независимые управления). Мышь по-прежнему
  двигает рамку как раньше (якорь на противоположной стороне).
- ⛔ МЕГАПИКСЕЛИ НЕ ОТКЛЮЧАЮТСЯ при ручном размере: MP = размер ВЫХОДА,
  `frame_w/frame_h` = размер ОБРЕЗА в пикселях источника. При сохранённых
  пропорциях плашка не меняется; в «Свободно» — пересчитывается.
- Координаты/размер — ТОЛЬКО нативные виджеты под «Фиксацией пропорций».
- Писать их ТИХО (`setWidgetValueQuiet`): громкая запись через callback
  перезапускает ноду на каждом кадре drag'а.
- Кнопка «?» — внутри `stage`, подсказка — в корне.
- DRAG-ИНВАРИАНТ: `state.drag.view` нельзя убирать.
- «Сбросить» НЕ зовёт `applyAspect` и НЕ меняет настройки результата.
- Значения COMBO — протокол; переводы только через `getOptionLabel`.
- `sync.py` после правок; Python-правка = рестарт ComfyUI, JS — `Ctrl+Shift+R`.
- Запрещены: `setInterval`/`MutationObserver`/`visibilitychange`, глобальные
  `window`-слушатели, `options.disabled`, `scripts/app.js`, canvas-рисование.

## 5. Скилы (сделано в этой сессии)
- `comfyui-localization` §6.1 (3 копии): `Comfy.Locale.change` (не `Comfy.Locale`;
  не всплывает → слушать на `app.ui.settings`), BOOLEAN `widget.options.on/off`.
- НОВЫЙ `comfyui-drag-view-pinning` (3 копии): union→scale→delta, снимок `view`.
- `comfyui-js-extension` § «Запись значения виджета ИЗ JS»: тихая/громкая запись.
- `comfyui-native-widget-look` §0: сначала спроси, нужен ли свой DOM-контрол.
- Реестр скилов в `Custom_node_ComfyUI/AGENTS.md` (4 строки).

## 6. Следующие шаги (идеи, не сделано)
- Проверить в сохранённом воркфлоу «Test Crop» после Hard Reload.
- `start-comfyui.bat` в `G:\ИИ web` — по аналогии с `stop-comfyui.bat`.
- Коммит/пуш — по команде пользователя.

## 7. Связанные файлы
- `crop_image.py`, `web/js/crop_image.js`, `__init__.py`, `check.json`
- `SPECIFICATION.md`, `README.md`, `Техническое задание.txt`
- `tests/_test_crop_image.py`, `tests/_smoke_crop_image.mjs`,
  `tests/_audit_crop_image.mjs`, `tests/_probe_live_crop_image.py`,
  `tests/_probe_live_dom_crop_image.py`, `tests/_shot_live_node.py`
- Репро-воркфлоу: `D:\ComfyUI_windows_portable\ComfyUI\user\default\workflows\Test Crop.json`
