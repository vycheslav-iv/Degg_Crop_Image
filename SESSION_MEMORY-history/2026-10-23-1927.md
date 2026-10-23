# Память сессии — Degg_Crop_Image (2026-10-23, вечер)

> Покажи этот файл агенту, чтобы продолжить работу.
> Всегда сверяйся с `AGENTS.md` и `SPECIFICATION.md`.

---

## 1. Что делали в этой сессии (кратко)

- **Ослабил магнит краёв** по жалобе «сильная привязка»: `SNAP_PX` `20 → 16`
  экранных px (замер живой пробой: край в 14 px прилипает, в 18 px уже нет).
- **Добавил кнопку включения/выключения привязки** `snap_edges` (BOOLEAN,
  default `true`): самый нижний виджет на теле ноды, сразу под «Сеткой
  золотого сечения». Выключена → допуск обнуляется, магнит и подсветка не
  работают; `Alt` тоже выключает. Значение живёт в воркфлоу.
- **Поймал и вылечил гонку в живой пробе**: проверка «координаты пересчитаны
  из состояния» читала кадр и поля двумя `eval`, а асинхронный сброс от
  `loadSource` успевал подвинуть состояние между ними → мнимый
  `FAIL: [-272, -154]` вместо `[0, 0]`. Лечение: кадр и поля читаются одним `eval`.
- Скил `comfyui-edge-snapping` обновлён под `snap_edges` и `SNAP_PX = 16`
  (все три зеркала), из `comfyui-drag-view-pinning` (§4.1) обновлены числа.
- Закоммичено и запушено: `c261503` → `master`
  (`github.com/vycheslav-iv/Degg_Crop_Image`).

## 2. Итоговое состояние кода

- `web/js/crop_image.js:68` — `SNAP_PX = 16` (**ЭКРАННЫХ** px), `:67` — `SNAP_COLOR`;
  допуск переводится в пиксели источника `SNAP_PX / state.drag.view.scale`
  (снимок view на pointerdown, `onPointerMove`).
- `web/js/crop_image.js:882` — `snapWidget = widgetByName(node, 'snap_edges')`,
  `:886` — `function snapEnabled() { return snapWidget?.value !== false; }`
  (виджета нет → включено), `:1587` — `snapTol = event.altKey ||
  !snapEnabled() ? 0 : SNAP_PX / …`.
- `web/js/crop_image.js:2169` — `sanitizeWidgetValues()`: в старых (позиционных)
  воркфлоу в `snap_edges` могла попасть чужая строка `crop_rect` →
  не-boolean приводится к `true`.
- `web/js/crop_image.js:2119` — `snapWidget` входит в список
  `[dimWidget, fillWidget, multWidget, goldenWidget, snapWidget]` (перерисовка).
- `crop_image.py:675` — вход `snap_edges` (BOOLEAN, default `True`), `:755` —
  параметр `snap_edges=True` в сигнатуре `crop()` (только отображение).
- `web/js/crop_image.js:388` — `snapMoveFrame(rect, tolPx, imgW, imgH)` —
  перенос: жёсткий сдвиг без изменения размера, из двух близких краёв берётся
  ближайший.
- `web/js/crop_image.js:415` — `snappedEdges(rect, imgW, imgH, eps=0.01)` —
  какие края на границе → подсветка; гасится в `endDrag`.
- `web/js/crop_image.js:456` — `resizeFrame(..., snapTol)` — притягиваются
  только края, которые двигает жест; прилипший край перебивает выбор оси при
  замке (`heightWins`).
- `tests/_probe_live_dom_crop_image.py:208` — `snap_plan_css_js(handle, off_px)` —
  план жеста, доводящий край до N ЭКРАННЫХ px от границы; `:1502` — цикл
  проверок замера («14 px прилипает» / «18 px не прилипает»); `:1523` —
  проверки кнопки `snap_edges` (вкл/выкл).
- `tests/_audit_crop_image.mjs:448` — «допуск магнита в диапазоне 10..20
  экранных пикселей» — потолок силы.

## 3. Проблемы, которые встречались (и как решали)

- «Слишком слабое прилипание» не переводится в пиксели источника: лечится
  только замером в ЭКРАННЫХ px — граница край→граница считается
  `frac.x * naturalW * scale`, проверки ставятся парами (прилипает / не
  прилипает), иначе любое усиление выглядит зелёным.
- **Гонка в живой пробе** (см. §1): сверка «поля ↔ кадр» обязана читать
  состояние одним `eval` (кадр `state.frac` там же, где поля виджетов).
- `eps = -1` в `snappedEdges` — **эквивалентная** мутация (допуск клампится в
  0): детектор её не видит и не должен.

## 4. Что важно не сломать при продолжении работы

- Допуск магнита — **в экранных пикселях** и только через `state.drag.view.scale`
  (живой `state.scale` меняется в `layout()` на каждом кадре жеста → рамка
  разгоняется, см. `comfyui-drag-view-pinning`).
- Пара проверок замера обязательна: одна «прилипает» без пары «не прилипает»
  даёт ложную зелёную при любом увеличении допуска.
- `snapEnabled()` возвращает `snapWidget?.value !== false` (виджета нет →
  включено); `sanitizeWidgetValues` приводит не-boolean к `true`.
- Подсветка `SNAP_COLOR` живёт только во время жеста (гасится в `endDrag`).
- Правки JS синхронизируются `python sync.py Degg_Crop_Image`; живые пробы —
  `D:/ComfyUI_windows_portable/python_embeded/python.exe`.

## 5. Следующие шаги (идеи, не сделано)

- Нет подсказки «зелёной полосы» у границы во время приближения (только цвет
  рамки) — можно обсудить, если пользователю мало обратной связи.
- Скил `comfyui-edge-snapping` — кандидат на перенос в глобальные скилы, если
  понадобится другим проектам.

## 6. Связанные файлы

- `web/js/crop_image.js`, `crop_image.py`, `README.md`, `SPECIFICATION.md`
- `tests/_smoke_crop_image.mjs`, `tests/_audit_crop_image.mjs`,
  `tests/_probe_live_dom_crop_image.py`
- Скилы: `.agents/skills/comfyui-edge-snapping/SKILL.md` (+ зеркала
  `.kilo/`, `.opencode/`), `.agents/skills/comfyui-drag-view-pinning/SKILL.md`
