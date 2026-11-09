# Память сессии — Degg_Crop_Image (2026-11-09)

> Актуально на 2026-11-09. Всегда сверяйся с `AGENTS.md` и `SPECIFICATION.md`.

---

## 1. Что делали в этой сессии (кратко)
- Приведены к фактическому контракту подсказки `mask` и `frame_w/frame_h`:
  Python-источник, локали RU/EN, README.
- Добавлены проверки-детекторы (Python-песочница + статический аудит), каждая
  подтверждена мутацией (сломанный текст → FAIL: 1 → восстановление).
- Изменения закоммичены и запушены в `origin/master`.

## 2. Итоговое состояние кода
- `crop_image.py:684` — `mask` tooltip: конвенция ComfyUI `1 = remove`,
  видимая альфа `1 - mask`.
- `crop_image.py:659,670` — `frame_w/frame_h` tooltip: ИТОГОВЫЙ размер
  результата; убрано ложное «crop region in source pixels / 0 keeps size».
- `locales/en/nodeDefs.json` — EN зеркало Python; `locales/ru/nodeDefs.json` —
  `1 = убрать`, видимая альфа `1 − маска`.
- `README.md:96` — строка `mask` по конвенции ComfyUI.
- `tests/_test_crop_image.py` — +2 проверки (`mask`, `frame_w/frame_h`);
  прогон: `PASS: 149  FAIL: 0`.
- `tests/_audit_crop_image.mjs` — +3 проверки (ru mask, en mask, README-строка);
  прогон: `ok: 201  FAIL: 0`, «аудит чист».

## 3. Проблемы, которые встречались (и как решали)
- Подсказки расходились с кодом: `mask` называл `1 = непрозрачно` (обратный
  смысл конвенции), `frame_w/h` — «crop region… 0 keeps current frame size».
  Исправлено текстом и закреплено проверками.
- README-детектор сначала был слишком слабым: матчил другую строку README про
  сокет. Сузил до табличной строки `` `mask` | MASK `` + запрет «1 = непрозрачно».

## 4. Что важно не сломать при продолжении работы
- Маска: конвенция ComfyUI `1 = убрать`, видимая альфа = `1 − маска`
  (`_mask_alpha`, `crop_image.py:235`).
- `mask` / `frame_w` / `frame_h` — тексты/UI: сервер их не читает, состояние в
  `crop_rect` / `output_size`; логику не «оптимизировать» под подсказки.
- Значения combo не переводить; RU-текст живёт только в `locales/ru`.
- Python-правки и `locales/` требуют перезапуска ComfyUI; правки JS — свежей
  загрузки страницы.
- Тесты — только в `tests/`; `sync.py` их не копирует в рабочую копию.

## 5. Следующие шаги (идеи, не сделано)
- После перезапуска ComfyUI проверить подсказки глазами в RU и EN.
- При необходимости — живые пробы (`_probe_live_dom_crop_image.py`,
  `_probe_live_reset_params.py`, `_probe_live_workflow_switch.py`).

## 6. Связанные файлы
- `crop_image.py`, `web/js/crop_image.js`, `SPECIFICATION.md`, `README.md`,
  `locales/ru/nodeDefs.json`, `locales/en/nodeDefs.json`, `tests/`, `check.json`.
