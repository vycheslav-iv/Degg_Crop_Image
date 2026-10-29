# Память сессии — Degg_Crop_Image (2026-10-29)

> Покажи этот файл агенту, чтобы продолжить работу.
> Всегда сверяйся с `AGENTS.md` и `SPECIFICATION.md`.

---

## 1. Что делали в этой сессии (кратко)

- **Жалоба:** «Кратность» действует только в режиме кропа. При загрузке файла и
  кнопке «Сбросить» размер брался от картинки как есть (1001×751 при
  кратности 8 уходил в выход 1001×751).
- **Решение (согласовано с пользователем, вариант без BOOLEAN):** кратность
  применяется ВСЕГДА — к кропу, к размеру картинки и к ручному размеру.
  Новый дефолт/минимум `multiplicity = 0` (= без округления), диапазон `0…128`
  шагом 4. Округление выводится в момент вывода, а не хранится в состоянии.
- Красная фаза → правка → зелёная, коммит `b7983f4` и пуш в origin/master.
- **Живая проба НЕ прогнана:** пользователь сам перезапустит ComfyUI позже
  (шёл прогон, prompt `c36244e8`). После рестарта запустить пробу.

## 2. Итоговое состояние кода

- `crop_image.py:107` `snap()` — `max(1, int(multiplicity))`, `0` → шаг 1.
- `crop_image.py:134` `output_size()` — ручная ветка (`manual is not None`)
  теперь проходит шаг: `max(step, py_round(width / step) * step)` по каждой
  стороне. Докстринг переписан под новый контракт.
- `crop_image.py:513` — `multiplicity` в `INPUT_TYPES`: `default 0`, `min 0`,
  `max 128`, `step 4`, новый tooltip.
- `crop_image.py:435/755` — дефолты `render_frames()` и `crop()` → `0`.
- `crop_image.py:816` — пустой результат: `max(int(multiplicity), 1)` → 1×1 при `0`.
- `web/js/crop_image.js:311` `outputSizeFor()` — ручная ветка: `pyRound` затем
  `snapSize(base, multiplicity)`; обе ветки округляются.
- `web/js/crop_image.js:934` `multiplicity()` — `Math.max(0, …)`, не `max(1…)`.
- `web/js/crop_image.js:939` `resultSize()` — ручная ветка через `snapSize`.
- `web/js/crop_image.js:1085` `setResultSize()` — хранит ЗАПРОШЕННЫЙ размер,
  кадр подгоняет под ОКРУГЛЁННЫЙ (`snapSize(manual, multiplicity())`).
- `web/js/crop_image.js` `dropStaleManual()` — сверка `ratiosMatch(state.frac,
  resultSize(), …)`, а не `state.manual`.
- `locales/ru|en/nodeDefs.json` — tooltip: «0 — без округления, применяется
  всегда»; `README.md` §опции/формула, `SPECIFICATION.md` §3 (формула, ручной
  режим, сброс), §7 (числа прогонов, описание пробы).

## 3. Проблемы, которые встречались (и как решали)

- **Старые тесты держали старый контракт** (`render_frames(..., 8, (37,29))`
  ждал 37×29; Mask Editor гонял `multiplicity=8` на картинке 4×4 → 8×8).
  По §2.3 правились ТЕСТЫ: identity-проверки и Paint-хелперы переведены на
  `multiplicity=0`, добавлена проверка `37×29 + 8 → 40×32`.
- **Детектор аудита хранил отпечаток старого выражения**
  (`ratiosMatch(state.frac, state.manual, …)`) → переустановлен на `resultSize()`.
- **Мутация `resultSize()` без `snapSize`** дала зелёный smoke (`ok: 110`):
  смоук не видит замыкания ноды. Краснеет статический аудит — покрытие есть,
  но слоем ниже; записано в SPEC.
- **Хук commit-msg ложно блокировал** сообщение из-за упоминаний `check.py` и
  `sync.py` (это команды, а не изменённые файлы) → переформулировал сообщение,
  `--no-verify` не использовался.
- **Проба падала с `NullGraphError`** (`__probeSetFile` держал узел, отсоединённый
  `g.clear()` в `WIRE_JS`) → секция 5 заново вызывает `SETUP_JS` перед загрузкой.
- **Интерпретатор:** проба запускается только
  `/d/ComfyUI_windows_portable/python_embeded/python.exe` — в системном Python
  нет `numpy`/`websockets`.

## 4. Что важно не сломать при продолжении работы

- `multiplicity = 0` — дефолт; `0` НЕ значит «выключить проверку»: он значит
  «шаг 1», и все формулы обязаны это переживать (`snapSize`, `snap`,
  `computeOutputSize`).
- `output_size` хранит **запрошенный** размер; округление — только при выводе.
  Иначе снятие кратности оставляет «залипшее» округление.
- Идентичность «полный кадр + ручной размер = размер источника» пиксель в
  пиксель держится только при `multiplicity = 0`; при >0 растяжение <0.3 %
  (1000/752 vs 1001/751 < `RATIO_TOLERANCE = 0.005`), поэтому
  `dropStaleManual` не должен сверяться с неокруглённым `state.manual`.
- Паритет Python ↔ JS: `pyRound` (банковское) и `max(step, …)` в обеих
  реализациях; любая правка формулы — в двух файлах + тест паритета.
- Тесты живут в `tests/`, `sync.py` их не копирует.

## 5. Следующие шаги (не сделано)

- **Перезапустить ComfyUI** и прогнать живую пробу:
  `/d/ComfyUI_windows_portable/python_embeded/python.exe tests/_probe_live_reset_params.py`
  (до правки было `КРАСНОЕ: провалов 4, ок 47`; ждём `0`).
- После зелёной пробы — записать в SPEC/README фактический вывод пробы.
- При желании: связать кратность с пресетами разрешения (сейчас `0` по
  умолчанию не даёт «ровных» 1088/1928 автоматически).

## 6. Связанные файлы

- `F:/AI_projects/Custom_node_ComfyUI/Degg_Crop_Image/crop_image.py`
- `F:/AI_projects/Custom_node_ComfyUI/Degg_Crop_Image/web/js/crop_image.js`
- `tests/_test_crop_image.py`, `tests/_smoke_crop_image.mjs`,
  `tests/_audit_crop_image.mjs`, `tests/_probe_live_reset_params.py`
- `locales/ru/nodeDefs.json`, `locales/en/nodeDefs.json`
- `README.md`, `SPECIFICATION.md`, `AGENTS.md` (процедура §2.2/§2.3)
- Рабочая копия: `D:/ComfyUI_windows_portable/ComfyUI/custom_nodes/Degg_Crop_Image`
