# Память сессии — Degg Crop Image (2026-10-21, вечер+)

> Покажи этот файл агенту, чтобы продолжить работу.
> Всегда сверяйся с `AGENTS.md`, `SPECIFICATION.md` и `Техническое задание.txt`.

---

## 1. Что делали в этой сессии (кратко)
- Починили баг Nodes 2.0: превью входа не обновлялось при смене изображения в
  upstream — `createWidgetUpdateHandler` зовёт только `widget.callback` и НЕ зовёт
  `node.onWidgetChanged`. Red снят на воркфлоу «Test Crop», фикс подтверждён
  пользователем, скил `comfyui-js-extension` дополнен (3 копии).
- Переставили нижние виджеты на теле ноды: `lock_ratio`, `aspect_ratio`,
  `megapixels`, `multiplicity`, `resample`, `fill`, `dim_outside`.
- Спека обновлена (§2 порядок входов, §7 проверки порядка), коммит `14b9bc8`
  запушен в `origin/master`.

## 2. Итоговое состояние кода
- `crop_image.py:277` `INPUT_TYPES` — порядок ключей = порядок виджетов:
  `image` (сокет), `lock_ratio`, `aspect_ratio`, `megapixels`, `multiplicity`,
  `resample`, `fill`, `dim_outside`, затем скрытый `crop_rect`.
- `crop_image.py:111` `output_size` — 1 MP = 1024², шаг не клампится внутри.
- `web/js/crop_image.js:691` `watchSource()` — `watchedHandler` на
  `source.onWidgetChanged` + `watchSourceWidgets(source)` (перепривязка при
  потере обёрток через `bindingsIntact()`).
- `web/js/crop_image.js:677` `watchSourceWidgets()` — оборачивает
  `widget.callback` виджетов источника (канал Nodes 2.0); снимается в
  `detachSource()` (`:635`).
- `web/js/crop_image.js:661` `sourceOutputsChanged()` — сравнение объекта
  `app.nodeOutputs[locator]`; `:668` `noteSourceChanged()` → rAF →
  `loadSource({ force })` (кэш-бастер при том же имени файла).
- `web/js/crop_image.js:328` `previewFor()` (`p:/o:/i:/n:`), `:352` `createEditor`,
  `:491` `layout()`, `:898` `refresh()`, `:1004` `setupNode()` (+ `widgets.unshift`
  превью наверх).
- `tests/_test_crop_image.py` — 88 проверок, есть чек «порядок виджетов».
- `tests/_probe_live_dom_crop_image.py` — 37 проверок: живой порядок
  `node.widgets`, оба канала смены входа, перезаливка того же имени.
- `tests/_audit_crop_image.mjs` — 126 проверок; `EXPECTED` держит новый порядок.

## 3. Проблемы, которые встречались (и как решали)
- Превью не обновлялось в Vue/Nodes 2.0 — добавлен канал обёрток
  `widget.callback`, детект по идентичности объекта `nodeOutputs`.
- После перестановки ключей статический аудит покраснел (детектор держал старый
  список и срез `multiplicity…megapixels`) — правился ДЕТЕКТОР, не задача.
- Тестовая ловушка: legacy-кнопка upload зелёная даже при сломанном Vue-пути;
  красный сценарий — `w.value=...; w.callback(w.value, app.canvas, l)` без
  `onWidgetChanged`.
- Фоновый запуск ComfyUI из tool-вызова умирает вместе с шеллом; 8188 мог
  оставаться занятым зомби-процессом — убивать по PID из `netstat`, запускать
  при необходимости вручную.

## 4. Что важно не сломать при продолжении работы
- Значения COMBO — протокол; переводы только через `getOptionLabel`
  (`locales/ru`), значения в workflow не менять.
- `crop_rect` и `HIDDEN_OPTIONS` остаются в конце `required`; `mask` — optional.
- Не добавлять `onWidgetChanged`-only путь: Nodes 2.0 его не вызывает; legacy
  `BaseWidget.setValue` вызывает оба.
- Порядок виджетов закреплён ТРЕМЯ проверками (Python-тест, аудит, live DOM) —
  меняешь `INPUT_TYPES`, обновляй все три `EXPECTED`-списка.
- `sync.py` обязателен после правок; Python-правка = рестарт ComfyUI, JS —
  Hard Reload.
- Запрещены: `setInterval`/`MutationObserver`/`visibilitychange`, глобальные
  `window`-слушатели, `options.disabled`, `scripts/app.js`, canvas-рисование.

## 5. Следующие шаги (идеи, не сделано)
- Прогнать живые пробы на пользовательском воркфлоу «Test Crop» после рестарта.
- Проверить, как новый порядок читается в сохранённых воркфлоу (`widgets_values`
  массивный — порядок значений не менялся, но старые графы стоит открыть).

## 6. Связанные файлы
- `crop_image.py`, `web/js/crop_image.js`, `__init__.py`, `check.json`
- `SPECIFICATION.md`, `Техническое задание.txt`, `AGENTS.md`
- `tests/_test_crop_image.py`, `tests/_smoke_crop_image.mjs`,
  `tests/_audit_crop_image.mjs`, `tests/_probe_live_crop_image.py`,
  `tests/_probe_live_dom_crop_image.py`
- `SESSION_MEMORY-history/2026-10-21.md`, `SESSION_MEMORY-history/2026-10-21-1626.md`
- Скилы: `comfyui-js-extension`, `comfyui-native-widget-look`
- Репро-воркфлоу: `D:\ComfyUI_windows_portable\ComfyUI\user\default\workflows\Test Crop.json`
