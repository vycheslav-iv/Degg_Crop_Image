# Память сессии — Degg Crop Image (2026-10-21, вечер)

> Покажи этот файл агенту, чтобы продолжить работу.
> Всегда сверяйся с `AGENTS.md`, `SPECIFICATION.md` и `Техническое задание.txt`.

---

## 1. Что делали в этой сессии (кратко)
- Починили главный баг: в Nodes 2.0 превью входа не обновлялось при смене
  изображения в upstream — `createWidgetUpdateHandler` зовёт только
  `widget.callback` и НЕ зовёт `node.onWidgetChanged`.
- Доказали red на реальном воркфлоу пользователя «Test Crop», починили, сняли
  мутации, обновили скил `comfyui-js-extension`, закоммитили и запушили.

## 2. Итоговое состояние кода
- `web/js/crop_image.js:691` `watchSource()` — вешает `watchedHandler` на
  `source.onWidgetChanged` и вызывает `watchSourceWidgets(source)`; повторный
  вызов перепривязывает, если обёртки потеряны (`bindingsIntact()`).
- `web/js/crop_image.js:677` `watchSourceWidgets()` — оборачивает
  `widget.callback` каждого виджета источника (канал Nodes 2.0), оригиналы
  восстанавливаются в `detachSource()` (`:635`).
- `web/js/crop_image.js:661` `sourceOutputsChanged()` — смена картинки = подмена
  объекта `app.nodeOutputs[locator]`; `:668` `noteSourceChanged()` → rAF →
  `loadSource({ force })` (кэш-бастер при том же имени файла).
- `web/js/crop_image.js:328` `previewFor()` — `p:/o:/i:/n:`; `:352` `createEditor`;
  `:491` `layout()`; `:898` `refresh()`.
- `crop_image.py:111` `output_size` — 1 MP = 1024², шаг не клампится внутри.
- `SPECIFICATION.md` §5/§7 — оба канала обновления + что покрывают живые пробы.
- `tests/_probe_live_dom_crop_image.py` — 36 проверок, включая Nodes 2.0-путь и
  перезаливку того же имени; `tests/_audit_crop_image.mjs` — 126 проверок.

## 3. Проблемы, которые встречались (и как решали)
- Превью не обновлялось только в Nodes 2.0 — подписка на `onWidgetChanged` не
  покрывает Vue-путь; обёрнуты callback'и виджетов источника.
- Перезаливка файла с тем же именем не видна — сравниваем объект
  `app.nodeOutputs[locator]`, а не ключ/имя.
- Ложнозелёная проба: загрузка через legacy-кнопку зовёт `onWidgetChanged` и
  проходит даже со сломанным Vue-путём; красная версия — `w.value = v;
  w.callback(v, app.canvas, node)` без `onWidgetChanged`.
- `CreateBoundingBoxes` не имеет `imgs` — сравнивать нативное превью по `imgs`
  нельзя, у него другой механизм.

## 4. Что важно не сломать при продолжении работы
- Слежение за upstream обязано покрывать ОБА канала (хук узла + `widget.callback`).
- `detachSource()` возвращает оригиналы только если `widget.callback === wrapper`.
- Формула `output_size`/`computeOutputSize` — паритет (тест паритета).
- Значения combo — протокол; переводить только подписи (`getOptionLabel`).
- После правок JS/Python — `python sync.py Degg_Crop_Image`, затем Hard Reload
  страницы (вкладка держит старый JS); Python требует рестарта ComfyUI.

## 5. Следующие шаги (идеи, не сделано)
- Проверить drag&drop файла на ноду LoadImage в живом UI (не покрыто пробой).
- Проверить перетаскивание рамки при зуме ×2 (координаты в долях).
- Прогнать `python _process/check.py` по всей папке перед релизом.

## 6. Связанные файлы
- `crop_image.py`, `web/js/crop_image.js`, `__init__.py`, `check.json`
- `SPECIFICATION.md`, `README.md`, `Техническое задание.txt`
- `tests/_test_crop_image.py`, `_smoke_crop_image.mjs`, `_audit_crop_image.mjs`,
  `_probe_live_crop_image.py`, `_probe_live_dom_crop_image.py`
- Скилы: `comfyui-js-extension` (два канала обновления виджета),
  `comfyui-native-widget-look` (`options.step` vs `step2`)
- Коммит фикса: `f20514c` (запушен в `origin/master`)
