# Память сессии — Degg Crop Image (2026-10-21, ночь)

> Покажи этот файл агенту, чтобы продолжить работу.
> Всегда сверяйся с `AGENTS.md`, `SPECIFICATION.md` и `Техническое задание.txt`.

---

## 1. Что делали в этой сессии (кратко)
- Новая семантика «Зафиксировано»: фиксирует ТЕКУЩИЕ пропорции рамки
  (`state.ratioLock`), а не пресет из списка; выбор пресета применяет его и
  включает замок; «Свободные» снимают замок.
- Shift-приёмы в режиме «Свободно»: Shift+перенос = строго одна ось
  (`axisDelta`), Shift+край/угол = ресайз с сохранением пропорций.
- Координаты центра рамки — нативные опции ноды `center_x`/`center_y` (INT)
  сразу под «Фиксацией пропорций» (после правки пользователя: были самодельные
  `<input>` в DOM — выглядело «не как у остальных» и не ездило в workflow).
- Кнопка «?» перенесена в правый верхний угол ПРЕВЬЮ (внутри `.degg-crop__stage`),
  подсказка — в корень (сцена режет содержимое), позиция через `syncHelpAnchor()`.
- Починен баг «разгона» рамки при drag: масштаб/смещение сцены фиксируются на
  `pointerdown` (`state.drag.view`) — иначе union→scale→delta разгоняли рамку.
- Добавлены `tests/_shot_live_node.py` (снимок живого узла PNG) и 4 скила/2
  дополнения скилов (см. §5).

## 2. Итоговое состояние кода
- `crop_image.py:285` — входы `center_x`/`center_y` (INT, ±100000, step 1) сразу
  после `lock_ratio`; в `crop()` приняты, на выход не влияют (рамку считает
  `crop_rect`) — как `aspect_ratio`/`lock_ratio`.
- `web/js/crop_image.js` — `state.ratioLock`, `state.suppress`,
  `currentPxRatio()`, `effectiveRatio()`, `programmatic()`, `setWidgetValueQuiet()`,
  `syncCoords()`, `applyCoords()`.
- `web/js/crop_image.js` — `localFromEvent(event, view)` / `sourceFromLocal(point,
  view)` берут зафиксированный `view`; `onPointerDown` сохраняет
  `view` (rect/zoom/scale/offX/offY) в `state.drag.view`.
- `web/js/crop_image.js:~1013` — `dragRatio = locked() ? effectiveRatio() :
  event.shiftKey ? state.drag.ratio : null`.
- `web/js/crop_image.js:~1226` — захват пропорций при включении замка.
- `web/js/crop_image.js` — `syncHelpAnchor()`: подсказка в корне, `top` от
  `stage.offsetTop`; кнопка «?» — дочерняя `stage` (`top:6px; right:8px`).
- `web/js/crop_image.js` — координаты пишутся ТИХО (`setWidgetValueQuiet`, без
  callback'а), правка координат → callback → `applyCoords` → `moveFrameToOffset`.
- `locales/{ru,en}/nodeDefs.json` — подписи `center_x`/`center_y`
  («Центр рамки X/Y» / «Frame centre X/Y»).
- Проверки: Python 91, смоук 65, аудит 157, живая DOM-проба 70 — всё зелёное;
  `python _process/check.py Degg_Crop_Image` → «ЗЕЛЁНОЕ: провалов 0».

## 3. Проблемы, которые встречались (и как решали)
- Разгон рамки при drag (доля кадра 88, пропорции 1:1) — фиксация `state.drag.view`
  на `pointerdown`; детектор: аудит + живая проба «drag не разгоняет рамку».
- Ложное «пропорция не 16:9» в пробе: сравнивался пиксельный `frameBox`
  (округление до целых CSS-пикселей) → теперь пропорция по `frac` × `natural`.
- Ложное «координаты не те»: ожидание считалось от 1000×500, а в ComfyUI 768×768 →
  ожидание пересчитывается от `natural`.
- Ложное «Shift не держит ось»: маленькая рамка → центр попадал в зону захвата
  края (12 px) → для перемещения взята рамка 0.8×0.6.
- Слабые детекторы (найдены мутациями): «тихая запись» смотрел на любое
  упоминание `setWidgetValueQuiet(centerXWidget` → ужесточено до формы
  `setWidgetValueQuiet(centerXWidget, Math.round(offset.x))`; проверка «правка
  координат двигает рамку» пропускала `if (false) applyCoords(...)` → теперь
  требует обе ветки `if (widget === centerXWidget) applyCoords(` / `else applyCoords(`.
- `G:\ИИ web\stop-comfyui.bat` (вне проекта): первая версия врала — кавычки
  вокруг путей ломали `for /f` (PID не находился), а редирект `>nul 2>&1` у
  `taskkill` давал «Input redirection is not supported»; починено: пути без
  кавычек, редиректа нет, проверка порта — через temp-файл (независимо от парсера).
- Bash heredoc с длинным python-кодом рвётся → писать патч-скрипт файлом и
  запускать `python <path>`; temp-папка `C:\Users\dggio\AppData\Local\Temp\opencode`
  бывает недоступна из bash → надёжнее класть скрипт в папку проекта и удалять.

## 4. Что важно не сломать при продолжении работы
- «Сбросить» НЕ вызывает `applyAspect` и НЕ меняет настройки результата.
- «По центру» никогда не масштабирует; «Вписать» масштабирует равномерно.
- Захват пропорций в замке — ТЕКУЩИЕ пропорции рамки, не пресет.
- Координаты — ТОЛЬКО нативные виджеты `center_x`/`center_y` под «Фиксацией
  пропорций»; самодельной строки в DOM-редакторе больше нет.
- Писать координаты ТИХО (`setWidgetValueQuiet`): громкая запись через callback
  перезапускает ноду на каждом кадре drag'а.
- Кнопка «?» — внутри `stage` (правый верхний угол превью), подсказка — в корне.
- DRAG-ИНВАРИАНТ: `state.drag.view` нельзя убирать (без него рамка разгоняется).
- Значения COMBO — протокол; переводы только через `getOptionLabel`.
- Язык переключается без F5; слушатель `Comfy.Locale.change` снимается в `onRemoved`.
- `sync.py` после правок; Python-правка = рестарт ComfyUI, JS — `Ctrl+Shift+R`.
- Запрещены: `setInterval`/`MutationObserver`/`visibilitychange`, глобальные
  `window`-слушатели, `options.disabled`, `scripts/app.js`, canvas-рисование.

## 5. Скилы (сделано в этой сессии)
- `comfyui-localization` §6.1 (3 копии): событие `Comfy.Locale.change` (не
  `Comfy.Locale`; не всплывает → слушать на `app.ui.settings`), BOOLEAN
  `label_on/label_off → widget.options.on/off` (+ `updateOptions`, `WidgetId`).
- НОВЫЙ `comfyui-drag-view-pinning` (3 копии): обратная связь union→scale→delta,
  снимок `view` на pointerdown, приёмка (аудит + проба + 4 мутации).
- `comfyui-js-extension` § «Запись значения виджета ИЗ JS»: тихая vs громкая
  запись, `state.suppress` против реентерабельности.
- `comfyui-native-widget-look` §0: сначала спроси, нужен ли свой DOM-контрол
  (одно число → нативный `INPUT_TYPES`).
- Таблица скилов в `Custom_node_ComfyUI/AGENTS.md` дополнена (4 строки).

## 6. Следующие шаги (идеи, не сделано)
- Проверить новый виджет в сохранённом воркфлоу «Test Crop» после Hard Reload.
- `start-comfyui.bat` в `G:\ИИ web` — по аналогии с `stop-comfyui.bat` (по
  просьбе пользователя).

## 7. Связанные файлы
- `crop_image.py`, `web/js/crop_image.js`, `__init__.py`, `check.json`
- `SPECIFICATION.md`, `README.md`, `Техническое задание.txt`
- `tests/_test_crop_image.py`, `tests/_smoke_crop_image.mjs`,
  `tests/_audit_crop_image.mjs`, `tests/_probe_live_crop_image.py`,
  `tests/_probe_live_dom_crop_image.py`, `tests/_shot_live_node.py`
- `SESSION_MEMORY-history/` (снапшоты: 2026-10-21-1701, -1726, -1740 и этот)
- Репро-воркфлоу: `D:\ComfyUI_windows_portable\ComfyUI\user\default\workflows\Test Crop.json`
