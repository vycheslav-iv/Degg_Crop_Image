# Память сессии — Degg Crop Image (2026-10-23)

> Покажи этот файл агенту, чтобы продолжить работу.
> Всегда сверяйся с `AGENTS.md` и `SPECIFICATION.md`.

---

## 1. Что делали в этой сессии

1. **Маска по картинке, пришедшей ПРОВОДОМ** — `node.imgs` отдаёт текущий
   источник, редактор открывается по любой картинке (`sourceKind`/`noteSourceKind`
   сбрасывают покраску только на СМЕНЕ рода источника, не при загрузке воркфлоу).
2. **Покраска по кабелю не попадала в результат** — `crop_image.py` выбрасывал
   RGB-слой Paint при поданном сокете. Фикс: `painted_matches_source()` (медиана
   по сетке 48×48, допуск 0.08) — если основа файла = картинка из сокета,
   источник = файл редактора, иначе побеждает сокет.
3. **Сохранённая маска не возвращалась в редактор** (жалоба: «рисую, сохраняю,
   открываю снова — ничего нет»). Фронтенд находит слои только по имени файла в
   служебном виджете `image` (`clipspace-painted-masked-<ts>.png` → `-mask-` в
   альфе, `-paint-`), а нода держала там источник. Фикс: `editorSourceRef()` +
   JS-`paintedMatchesSource()` (сетка 32×32, медиана ≤ 0.08), файл рисуется
   ПОВЕРХ источника (альфа = 1 − маска → без подложки прозрачные пиксели чёрные).
4. Доки (`SPECIFICATION.md` §9.4, `README.md`), скил `comfyui-mask-editor`
   (все три зеркала) приведены к фактам; работа закоммичена и запушена.

## 2. Итоговое состояние кода

- `web/js/crop_image.js`:
  - `currentSourceRef():1661`, `editorSourceRef():1681` — что класть в виджет
    `image`: файл редактора, только если он нарисован по текущей картинке.
  - `paintedMatchesSource(ref):1704` (canvas 32×32, медиана максимума разницы по
    каналам ≤ 0.08, гейт `maskLayer.dataset.src`, try/catch → false),
    `sampleGrid(el, n, under):1739` — off-screen сэмплер, `syncMaskEditorSource():1757`.
  - `maskLayer.onload:688` → `layout()` + `syncMaskEditorSource()`; вызовы также
    из `image.onload` и callback служебного виджета (`setTimeout(..., 0)`).
  - `loadStateFromWidgets({ dropStale = true }):1025` — `dropStaleManual()` только
    когда картинка актуальна (`onConfigure` передаёт `dropStale: false`).
  - `dropPaintedMask():1944`, `sourceKind():1972`, `noteSourceKind():1979`.
  - `image.onerror` — `state.img = null`, `node.imgs = []`, снятие `src`, retry.
- `crop_image.py` — `painted_matches_source(painted, source, tolerance=0.08)`;
  в `crop()` ветка `elif painted_image is not None and painted_matches_source(...)`:
  одиночный кадр — замена целиком, батч — `torch.cat([painted, image[1:]])`.
- Тесты: `_test_crop_image.py` 139/0, `_smoke_crop_image.mjs` 92/0,
  `_audit_crop_image.mjs` 187/0 (чек восстановления + суженный «канвас только
  off-screen сэмплер»), `_check_mask_file.py` 4 сценария.
- Живые пробы: `_probe_live_dom_crop_image.py` 131/0, `_probe_live_wire_mask.py`
  19/0, `_probe_live_mask_preview.py` 12/0, `_probe_live_mask_restore.py` 11/0.
- `python _process/check.py Degg_Crop_Image` — провалов 0. Коммит `31db388`
  запушен в `origin/master`.

## 3. Проблемы, которые встречались (и как решали)

- **«нет файла: node.imgs погашен»** — не регрессия: в этой точке пробы провод
  подключён, `previewFor` берёт картинку провода. Проба исправлена.
- **«значения встали не на свои виджеты»** — `dropStaleManual()` сравнивал с
  ПРЕДЫДУЩЕЙ картинкой; лечение — флаг `dropStale`.
- **Открытие редактора в пробе** — `app.extensionManager.command.execute(
  'Comfy.MaskEditor.OpenMaskEditor')` + `.maskEditor-dialog-root` (команды
  `CloseMaskEditor` в бандле НЕТ).
- ⛔ **`app.loadGraphData()` ВЫЗЫВАЕТ `onConnectionsChange`** — один раз на
  входной слот, связи уже восстановлены, `previous === null`. Сброс на
  `previous === null` запрещён (иначе маска стирается при открытии воркфлоу).
- **Paint по кабелю терялся** — нельзя ни выбрасывать файл редактора, ни брать
  его слепо: только после `painted_matches_source()`.
- **Маска не возвращалась** — причина была в НАШЕЙ записи источника в виджет
  `image` из `image.onload`; оригинальный LoadImage виджет не переписывает.
- `upstreamNode(node)` в `nodeCreated` бросает `NullGraphError` → только try/catch.

## 4. Что важно не сломать при продолжении работы

- Сброс маски — ТОЛЬКО на смене рода источника или файла; при открытии воркфлоу
  и на новой картинке из провода маску не трогать.
- `dropStaleManual()` в `loadStateFromWidgets()` — только под флагом `dropStale`.
- `node.imgs` — единственный канал «нода = картинка» для штатных точек входа;
  гасить только когда превью реально пропало.
- Служебный виджет `image` НЕ сериализуется (`serialize: false` в опциях и в
  свойстве) — иначе возвращается баг «на выходе 8×8».
- В виджет `image` файл редактора кладётся ТОЛЬКО после пиксельного сравнения
  (`paintedMatchesSource`): мутация `return true` краснит 3 проверки пробы.
- Перед живым прогоном — `python sync.py Degg_Crop_Image` (пробы читают рабочую
  копию `D:\ComfyUI_windows_portable\...`); живые пробы — только встроенным
  Python `D:/ComfyUI_windows_portable/python_embeded/python.exe`.

## 5. Следующие шаги

- Открытых дефектов нет: пользователь проверил реальный путь вручную на
  воркфлоу «Test Crop» — маска и кисть на месте после Save и переоткрытия.
- Если жалоба вернётся — начинать с живого пути (рисование мышью → Save →
  переоткрытие), а не с синтетических проб: этот путь пробами пока не покрыт.

## 6. Связанные файлы

- `web/js/crop_image.js` — вся логика превью/маски/источника.
- `crop_image.py` — серверная часть (страховка «не-тензор», `load_source_file`,
  маска из альфы: `mask` → `mask_file` → альфа; `painted_matches_source`).
- `tests/_probe_live_*.py` — живые пробы; `tests/_test_crop_image.py`,
  `tests/_smoke_crop_image.mjs`, `tests/_audit_crop_image.mjs` — без ComfyUI.
- `SPECIFICATION.md` §9.4 — контракт Mask Editor; `README.md` — для человека.
- Скил `comfyui-mask-editor` (`.opencode/`, `.kilo/`, `.agents/` — побайтово равны).
- Фронтенд: `comfyui_frontend_package/static/assets/*.map` →
  `src/extensions/core/maskeditor.ts`, `src/composables/maskeditor/useMaskEditor.ts`.
