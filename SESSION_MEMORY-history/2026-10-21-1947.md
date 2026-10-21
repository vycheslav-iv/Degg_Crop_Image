# Память сессии — Degg Crop Image (2026-10-21, ночь)

> Покажи этот файл агенту, чтобы продолжить работу.
> Всегда сверяйся с `AGENTS.md`, `SPECIFICATION.md` и `Техническое задание.txt`.

---

## 1. Что делали в этой сессии (кратко)
- «Зафиксировано» фиксирует ТЕКУЩИЕ пропорции рамки; выбор пресета применяет его
  и включает замок; «Свободные» снимают.
- Shift-приёмы: перенос строго по одной оси, ресайз края с сохранением пропорций.
- Координаты центра рамки — нативные опции `center_x`/`center_y` под «Фиксацией
  пропорций».
- ⭐ РАЗМЕР РЕЗУЛЬТАТА — нативные опции `frame_w`/`frame_h` (ИТОГОВЫЙ размер
  картинки — те же числа, что в плашке и в выходах width/height):
  вбил 1000 × 1000 → в плашке 1000 × 1000; кратность учитывается (1333 → 1336);
  остальные параметры подстраиваются (пропорции кадра = пропорциям результата,
  мегапиксели показывают эквивалент); обратное направление: выбор пресета или
  правка MP возвращают режим «по мегапикселям» (16:9 + 2 МП → 1928 × 1088).
- ⛔ ИНВАРИАНТ (баг пользователя): пропорции кадра и ручного результата всегда
  совпадают; разошлись (ручной размер остался от прошлого воркфлоу) — ручной
  режим сбрасывается, иначе MP/пресет молча игнорировались, а кадр растягивался.
  Сверка `reconcileManual()` — ДО отрисовки плашки.
- Кнопка «?» — в правом верхнем углу ПРЕВЬЮ; подсказка в корне.
- Разгон рамки при drag починен: масштаб/смещение сцены фиксируются на
  `pointerdown` (`state.drag.view`).

## 2. Итоговое состояние кода
- `crop_image.py` — входы (порядок = порядок виджетов): `lock_ratio`, `center_x`,
  `center_y`, `frame_w`, `frame_h`, `aspect_ratio`, `megapixels`, `multiplicity`,
  `resample`, `fill`, `dim_outside`; скрытые `crop_rect` и `output_size`
  (ручной размер результата; `parse_output_size` + параметр `manual` в
  `output_size()` — ручной размер важнее мегапикселей).
- `web/js/crop_image.js` — `snapSize(manual, mult)`, `outputSizeFor(rectW, rectH,
  mp, mult, manual)`, `frameWithRatio(rect, imgW, imgH, ratio)` (площадь+центр);
  `state.manual`; `currentOutput()`, `syncSize()`, `reconcileManual()`,
  `applySize()`, `writeManual()/readManual()/clearManual()`,
  `syncMpToManual()` (эквивалент MP), `adaptManualToFrame(nextFrac)`;
  плашка = `currentOutput()`; `readManual` отдан наружу (для проб).
- `web/js/crop_image.js` — `state.drag.view` (rect/zoom/scale/offX/offY) на
  `pointerdown`; `state.ratioLock`, `state.suppress`, `setWidgetValueQuiet`.
- Проверки: Python 93, смоук 73, аудит 174, живая DOM-проба 82 — всё зелёное;
  `python _process/check.py Degg_Crop_Image` → «ЗЕЛЁНОЕ: провалов 0».

## 3. Проблемы, которые встречались (и как решали)
- ⛔ РУЧНОЙ РАЗМЕР ПЕРЕБИВАЛ ПРЕСЕТ И MP (баг пользователя: 16:9 + 2 МП, а в полях
  1448 × 1448). Причина: ручной размер из прошлого воркфлоу жил в `output_size`
  и выигрывал, хотя пропорции кадра с ним расходились. Лечение: инвариант +
  `reconcileManual()` до отрисовки плашки.
- Плашка показывала старый размер, а поля — новый: сверка была в `syncSize()`,
  а `layoutBadge()` считал раньше → сверку перенесли в `layout()`.
- Разгон рамки при drag (доля 88, пропорции 1:1) → фиксация `state.drag.view`.
- Ложные «пропорция не 16:9» / «координаты не те» / «Shift не держит ось»:
  пропорции по `frac`×`natural`, ожидания от `natural`, для Shift — рамка крупнее
  зоны захвата края (12 px).
- Слабые детекторы (найдены мутациями) → ужесточены до конкретных форм; проверка
  на мутацию: без `reconcileManual` в `layout` живая проба даёт ровно картину
  пользователя (поля 1928 × 1088, плашка 1448 × 1448).
- Свободный drag и сверка конфликтовали: сверка успевала сбросить ручной режим
  до адаптации → `adaptManualToFrame(next)` вызывается ДО `setFrac(next)`.
- ⚠️ Потеря реализации: `cp` старого бэкапа поверх свежего файла откатил всю
  переработку → восстановлено из рабочей копии ComfyUI. Правило: бэкапы только
  из `git` (`git checkout -- file`), не из /tmp.
- Сервер не подхватил новые входы: ComfyUI запущен ДО `sync.py` → стоп → sync →
  старт.

## 4. Что важно не сломать при продолжении работы
- `frame_w`/`frame_h` = ИТОГОВЫЙ размер результата, НЕ размер обреза.
- ⛔ ИНВАРИАНТ пропорций: кадр ↔ ручной результат. Сверка — в `layout()` ДО
  плашки; в `syncSize()` тоже.
- Ручной режим: MP = эквивалент (информационно), правка MP = возврат в режим MP;
  выбор пресета = сброс ручного режима; свободный drag = адаптация ручного
  размера (ширина сохраняется) ДО setFrac.
- Ручной размер ТОЧНО живёт в скрытом `output_size`; через MP его гонять нельзя
  (шаг 0.1 теряет точность).
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
