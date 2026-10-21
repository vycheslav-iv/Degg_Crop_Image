# Память сессии — Degg Crop Image (2026-10-21, вечер)

> Покажи этот файл агенту, чтобы продолжить работу.
> Всегда сверяйся с `AGENTS.md`, `SPECIFICATION.md` и `Техническое задание.txt`.

---

## 1. Что делали в этой сессии (кратко)
- Переделали логику кнопок превью: «Вписать» (равномерный масштаб текущей рамки
  вверх/вниз до границ + центр), «По центру» (только перенос центра, размер не
  меняется), «Сбросить» (рамка `0,0,1,1` + свободные пропорции + замок off;
  `megapixels`/`multiplicity`/`resample`/`fill`/`dim_outside` не трогаются).
- Переименовали `Вписать всё`→`Вписать` / `Fit all`→`Fit`, локализовали замок
  (`Зафиксировано`/`Свободно`, EN `Locked`/`Free`) и сделали смену языка на лету.
- Спека §5/§7 обновлена, коммит `609efc6` запушен в `origin/master`.

## 2. Итоговое состояние кода
- `web/js/crop_image.js:149` `centerFrameFrac` — `x=(1-w)/2, y=(1-h)/2`, размер
  сохраняется, выход за границы разрешён.
- `web/js/crop_image.js:160` `fitFrameFrac` — `s=min(1/w,1/h)`, затем центр.
- `web/js/crop_image.js:882` / `:889` / `:895` — обработчики кнопок `fit` /
  `center` / `reset`.
- `web/js/crop_image.js:927` `setWidgetValue` — пишет в `widget.value` и в
  `widgetValueStore` (Nodes 2.0), зовёт callback, метит канвас.
- `web/js/crop_image.js:948` `applyLanguage` — кнопки, `empty`, combo через
  `getOptionLabel`, замок через `options.on/off` (+ store `updateOptions`).
- `web/js/crop_image.js:978` `onLocaleChange` — слушатель
  `Comfy.Locale.change`; снимается в `onRemoved` (`:1069`).
- `web/js/crop_image.js:495` `setFrac` — публикуется в `__deggCrop` для проб.
- `crop_image.py:289` — EN-подписи замка `Locked`/`Free` (Python — EN-источник).
- Порядок виджетов прежний: `lock_ratio`, `aspect_ratio`, `megapixels`,
  `multiplicity`, `resample`, `fill`, `dim_outside`, затем скрытый `crop_rect`.
- Проверки: Python 90, смоук 61, аудит 136, live DOM 53, live HTTP 29 — всё
  зелёное; `check.py` без провалов.

## 3. Проблемы, которые встречались (и как решали)
- Событие смены локали в legacy-настройках называется `'Comfy.Locale.change'`
  (`dispatchChange` шлёт `id + '.change'`), а не `'Comfy.Locale'`.
- BOOLEAN в Nodes 2.0: `useBooleanWidget` маппит `label_on/off` в
  `widget.options.on/off`; локализовать нужно их (generic-чтения `label_on` нет).
- Живая проба ложно падала на «Сбросить»: сравнивала рамку со сценой, а надо с
  `imgBox` (изображение letterbox внутри сцены).
- Сброс пропорций ломал более позднюю проверку 16:9/2MP — проба восстанавливает
  `aspect_ratio`/`lock_ratio` после блока кнопок.
- Старый сервер на 8188 отдавал прежний Python (`locked`/`free`) — убить PID из
  `netstat`, поднять заново, проверить `/object_info/DeggCropImage`.

## 4. Что важно не сломать при продолжении работы
- «Сбросить» НЕ вызывает `applyAspect` и НЕ меняет настройки результата.
- «По центру» никогда не масштабирует; «Вписать» масштабирует равномерно и в
  обе стороны, режим/пропорции не меняет.
- Значения COMBO — протокол; переводы только через `getOptionLabel`, значения
  в workflow не менять. Python остаётся EN-источником.
- Язык должен переключаться без F5/рестарта; снимать слушатель в `onRemoved`.
- Порядок виджетов закреплён ТРЕМЯ проверками (Python-тест, аудит, live DOM).
- `sync.py` обязателен после правок; Python-правка = рестарт ComfyUI, JS —
  `Ctrl+Shift+R`.
- Запрещены: `setInterval`/`MutationObserver`/`visibilitychange`, глобальные
  `window`-слушатели, `options.disabled`, `scripts/app.js`, canvas-рисование.

## 5. Следующие шаги (идеи, не сделано)
- Опция «Сбросить» с сохранением пропорций (сейчас всегда свободные) — только
  если пользователь попросит.
- Проверить новый бар в сохранённом воркфлоу «Test Crop» после Hard Reload.

## 6. Связанные файлы
- `crop_image.py`, `web/js/crop_image.js`, `__init__.py`, `check.json`
- `SPECIFICATION.md`, `Техническое задание.txt`, `AGENTS.md`
- `tests/_test_crop_image.py`, `tests/_smoke_crop_image.mjs`,
  `tests/_audit_crop_image.mjs`, `tests/_probe_live_crop_image.py`,
  `tests/_probe_live_dom_crop_image.py`
- `SESSION_MEMORY-history/2026-10-21-1701.md` (предыдущая версия)
- Репро-воркфлоу: `D:\ComfyUI_windows_portable\ComfyUI\user\default\workflows\Test Crop.json`
