# Память сессии — Degg Crop Image (2026-10-23)

> Покажи этот файл агенту, чтобы продолжить работу.
> Всегда сверяйся с `AGENTS.md` и `SPECIFICATION.md`.

---

## 1. Что делали в этой сессии

1. **Маска по картинке, пришедшей ПРОВОДОМ.** Раньше Mask Editor в режиме
   сокета открывался по файлу (или не открывался): `node.imgs` отдаёт текущий
   источник, поэтому рисовать теперь можно по любой картинке.
2. **Сброс покраски при смене рода источника** (файл ↔ провод): `sourceKind()`
   + `noteSourceKind()`; первое наблюдение — усыновление БЕЗ сброса (иначе
   маска из воркфлоу стиралась бы при открытии), отличие → `dropPaintedMask()`.
3. **Разобраны 3 ложных провала живой пробы** (см. §3) — два были дефектами
   самой пробы, один настоящий (ручной размер стирался при открытии воркфлоу).
4. Документация (`SPECIFICATION.md` §9.4, `README.md`) приведена к фактам.
5. **Покраска по картинке из кабеля не попадала в результат** (жалоба после
   первого прогона: в превью ноды кисть есть, на выходе — исходник). Причина
   в `crop_image.py`: при поданном сокете `painted_image` (RGB-слой Paint)
   выбрасывался, из файла бралась только маска. Фикс: `painted_matches_source()`
   (медиана разницы по сетке 48×48, допуск 0.08) — если основа файла совпадает
   с картинкой из сокета, источник = файл редактора; иначе побеждает сокет.
6. **Сохранённая маска не возвращалась в редактор** (жалоба 2026-10-23:
   «рисую, сохраняю, всё работает; открываю редактор снова — ничего нет»).
   Фронтенд находит слои маски/кисти только по имени файла из служебного
   виджета `image` (`clipspace-painted-masked-<ts>.png` → `clipspace-mask-…`
   в альфе и `clipspace-paint-…`), а нода держала там источник. Фикс:
   `editorSourceRef()` + `paintedMatchesSource()` (пиксельное сравнение,
   сетка 32×32, медиана ≤ 0.08, файл рисуется ПОВЕРХ источника из-за альфы).
   RED-проба `tests/_probe_live_mask_restore.py`: было `ok:2 FAIL:4`, стало
   `ok:11 FAIL:0`; три мутации краснят её.

## 2. Итоговое состояние кода

- `web/js/crop_image.js`:
  - `loadStateFromWidgets({ dropStale = true })` — `dropStaleManual()` зовётся
    только когда картинка актуальна; `onConfigure` передаёт `dropStale: false`.
    ⛔ Причина: на `onConfigure` `state.img` ещё ПРЕДЫДУЩАЯ картинка, и
    сохранённый `output_size` (`930,1280`) вычищался как «несовпадающий».
    Точную проверку делает `image.onload` по реальному источнику.
  - `sourceKind()` / `noteSourceKind()` / `lastSourceKind` — рядом с
    `dropPaintedMask()`; вызов в rAF внутри `onConnectionsChange` и в стартовом rAF.
  - `image.onerror` — `state.img = null`, `node.imgs = []`, снятие `src`,
    текст пустого состояния; один `retry` перезапроса файла.
  - `node.imgs` — `Object.defineProperty` (get отдаёт `state.img`).
- `crop_image.py` — `painted_matches_source(painted, source, tolerance=0.08)`
  рядом с `_fill_color`; в `crop()` ветка `elif painted_image is not None and
  painted_matches_source(...)`: одиночный кадр — замена целиком, батч —
  `torch.cat([painted, image[1:]])` (редактор открывался по первому кадру).
- `tests/_check_mask_file.py` — 4 сценария (кабель/файл + маска, покраска по
  кабелю → красные пиксели есть, файл от другой картинки → красных 0).
- `tests/_probe_live_dom_crop_image.py` — `ok: 131`, `FAIL: 0`.
- `tests/_probe_live_wire_mask.py` — `ok: 19`, `FAIL: 0`.
- `tests/_probe_live_mask_preview.py` — `ok: 12`, `FAIL: 0`.
- `tests/_probe_live_mask_restore.py` — `ok: 11`, `FAIL: 0` (маска и кисть
  возвращаются при повторном открытии; файл от другой картинки не подставляется).
- `node tests/_smoke_crop_image.mjs` — 92/0; `node tests/_audit_crop_image.mjs`
  — 187/0; `python tests/_test_crop_image.py` — 139/0;
  `python _process/check.py Degg_Crop_Image` — провалов 0.
- `web/js/crop_image.js`: `editorSourceRef()` / `paintedMatchesSource()` /
  `sampleGrid()` рядом с `syncMaskEditorSource()`; `maskLayer.onload` зовёт
  `layout()` + `syncMaskEditorSource()` (слой мог догрузиться позже источника).

## 3. Проблемы, которые встречались (и как решали)

- **«нет файла: node.imgs погашен»** — НЕ регрессия: в этой точке пробы провод
  подключён, `previewFor` берёт картинку провода, `node.imgs` законно её держит.
  Проба исправлена: отключить провод → плохой файл → `imgs == 0` → вернуть.
- **«значения встали на свои виджеты»** — got `''` vs want `930,1280`:
  `dropStaleManual()` в `loadStateFromWidgets()` сравнивал с ПРЕДЫДУЩЕЙ
  картинкой. Лечение — параметр `dropStale` (см. §2).
- **«кнопка Маска открывает редактор»** — проба звала несуществующий
  `app.commandStore` и искала `.mask-editor-dialog`. Правильно:
  `app.extensionManager.command.execute('Comfy.MaskEditor.OpenMaskEditor')`
  и `.maskEditor-dialog-root` (замер: холстов 2 → 6, `[role=dialog]` 0 → 1).
- ⛔ **`app.loadGraphData()` ВЫЗЫВАЕТ `onConnectionsChange`** — один раз на
  входной слот, с уже восстановленными связями и `previous === null`. Прежнее
  «вызовов нет» было артефактом замера (обёртка `LiteGraph.createNode` не видит
  ноды внутри `loadGraphData`). Поэтому сброс на `previous === null` запрещён.
- `upstreamNode(node)` в `nodeCreated` бросает `NullGraphError` → граф трогать
  только в try/catch.
- **«нарисовал по проводу, сохранил — на выходе нет кисти»**: файл редактора
  несёт Paint в RGB, поэтому его нельзя выбрасывать при поданном сокете. Но и
  брать его слепо нельзя (файл мог остаться от прошлой картинки) — только после
  `painted_matches_source()`. Мутации: `return True` → краснеет проверка «файл
  от другой картинки», `return False` → краснеет «покраска по кабелю».

## 4. Что важно не сломать при продолжении работы

- Сброс маски — ТОЛЬКО на смене рода источника или файла; на каждой новой
  картинке из провода маску не трогать; при открытии воркфлоу — не трогать.
- `dropStaleManual()` в `loadStateFromWidgets()` обязан оставаться под флагом
  `dropStale` (иначе снова стирается ручной размер из воркфлоу).
- `node.imgs` — единственный канал «нода = картинка» для штатных точек входа;
  гасить его можно только когда превью реально пропало.
- Служебный виджет `image` НЕ сериализуется (`serialize: false` в опциях и в
  свойстве) — иначе возвращается баг «на выходе 8×8».
- В служебный виджет `image` кладётся файл редактора ТОЛЬКО после пиксельного
  сравнения с текущей картинкой (`paintedMatchesSource`). Убрать сравнение =
  редактор покажет картинку прошлой покраски (мутация `return true` краснит
  3 проверки пробы).
- Перед каждым живым прогоном — `python sync.py Degg_Crop_Image` (пробы читают
  рабочую копию `D:\ComfyUI_windows_portable\...`).

## 5. Следующие шаги

- Закоммитить работу (в рабочей копии правки уже синхронизированы).
- Пользователю: Hard Reload → воркфлоу «Test Crop» → нарисовать маску →
  закрыть редактор → открыть снова: маска и кисть должны быть на месте
  (проверено пробой `_probe_live_mask_restore.py` 11/0).

## 6. Связанные файлы

- `web/js/crop_image.js` — вся логика превью/маски/источника.
- `crop_image.py` — серверная часть (страховка «не-тензор», `load_source_file`,
  маска из альфы: `mask` → `mask_file` → альфа; `painted_matches_source` —
  «файл редактора = эта же картинка?»).
- `tests/_probe_live_dom_crop_image.py`, `tests/_probe_live_wire_mask.py`,
  `tests/_probe_live_mask_preview.py`, `tests/_probe_live_mask_restore.py` —
  живые пробы.
- `SPECIFICATION.md` §9.4 — контракт Mask Editor; `README.md` — для человека.
- Фронтенд: `comfyui_frontend_package/static/assets/*.map` →
  `src/extensions/core/maskeditor.ts`, `src/composables/maskeditor/useMaskEditor.ts`.
