# Память сессии — Degg Crop Image (2026-10-22)

> Покажи этот файл агенту, чтобы продолжить работу.
> Всегда сверяйся с `AGENTS.md` и `SPECIFICATION.md`.

---

## 1. Что делали в этой сессии

1. **Баг «на выходе 8×8»** (источник из файла и/или после покраски маски).
   Причина: служебный виджет `image` (канал штатного Mask Editor) сериализовался
   в промпт **по имени** (`inputs[widget.name] = value`), а у ноды есть вход-сокет
   `image` (IMAGE) → туда попадала СТРОКА (`"example.png"`,
   `"clipspace-painted-masked-… [input]"`). Сервер считал источник поданным, не
   читал файл и отдавал пустую заглушку `multiplicity × multiplicity` = 8×8.
2. Фикс: виджет `image` больше не сериализуется (`serialize: false` в опциях
   `addWidget` **и** `widget.serialize = false`) + серверная страховка «не-тензор
   в `image` = источник не подан, читаем `image_file`».
3. Добавлена живая регрессия `tests/_probe_live_prompt_crop_image.py` (промпт
   ровно от `app.graphToPrompt()` + сквозной прогон через очередь).

## 2. Итоговое состояние кода

- `crop_image.py:632` (в `crop()`, строка 603) `crop(...)` — перед `load_source_file`: `if image is not
  None and not hasattr(image, "shape"): image = None` (страховка от строки в
  входе из старых промптов/воркфлоу).
- `web/js/crop_image.js:1547` `maskEditorWidget` (`addWidget('string','image',
  …)`) — `serialize: false` в опциях **и** `maskEditorWidget.serialize = false`.
  ⛔ Оба канала обязательны: фронтенд читает `i.options.serialize === false`,
  AGENTS требует ещё и свойство. Значение маски живёт во входе `mask_file`,
  поэтому из промпта ничего не теряется.
- `web/js/crop_image.js:1603` `currentSourceRef()` / `syncMaskEditorSource()` —
  виджет `image` держит ТЕКУЩИЙ источник (имя файла / пусто в режиме сокета),
  иначе Mask Editor открывается со старой картинкой.
- `crop_image.py:245` `input_image_files()`, `crop_image.py:266`
  `load_source_file(name)` — файл из `input` (PIL+numpy), альфа → маска,
  снимает аннотации фронтенда (`[input]`, `[output]`, `[temp]`).
- `crop_image.py:130` `output_size()` — размер по МП и кратности (1 МП = 1024²).
- `web/js/crop_image.js:21` `WIDGET_ORDER` — `image` последний, поэтому его
  исключение из сериализации не сдвигает позиционные `widgets_values`.
- `web/js/crop_image.js:1891` `setupNode()` — DOM-превью (`getMinHeight` в
  опциях `addDOMWidget`, без позднего override).
- `crop_image.py:221` `_mask_alpha()`, `crop_image.py:484` вход `mask_file` —
  приоритет маски: `mask` → `mask_file` → альфа источника.

## 3. Проблемы, которые встречались (и как решали)

- **8×8 на выходе** — строка из служебного виджета в сокете `image`.
  Доказательство: `tests/_probe_live_prompt_crop_image.py` печатал
  `image_type='string'` и `image_value='example.png'` (2 провала). После фикса
  ключа `image` в промпте НЕТ, сквозной прогон даёт 768×768.
- **Старые промпты** (строка уже записана) лечатся только серверной страховкой:
  на запущенном ComfyUI со старым Python проба с промптом старого вида всё ещё
  печатает `(8, 8)` → нужен рестарт ComfyUI после правки `.py`.
- **Детектор зеленел от собственного комментария** — в аудите
  `/serialize:\s*false/` матчил строку `// serialize:false ОБЯЗАТЕЛЬНО…`.
  Лечение: `/^\s*serialize:\s*false,/m` (только строка опции).
- **Смоук/живая проба подхватывала чужой воркфлоу** — headless Chrome
  восстанавливает последний граф, промпт падал на чужом `KSampler`. Лечение:
  `app.graph.clear()` в setup пробы.
- **`sync.py` копирует все `.py`** — временные `_tmp_*.py` удалены из проекта
  и рабочей копии.
- Остальные (прошлые сессии, актуально): маска = 1 − альфа у файла Mask Editor;
  поздно подставленное «второе превью» убирается через `app.nodeOutputs`;
  позиционное восстановление виджетов чинится `widgets_values_named`.

## 4. Что важно не сломать при продолжении работы

- ⛔ **Служебный виджет `image` не сериализуется** (оба канала). Вернуть
  `serialize: true` — значит вернуть 8×8.
- ⛔ **Серверная страховка в `crop()`** — без неё старые промпты снова дают 8×8.
- ⛔ **Маска** — конвенция ComfyUI (1 = убрать, альфа файла = 1 − маска).
- ⛔ **Мегапиксели затираются при смене картинки** (SPEC §8.1) — не менять без
  решения пользователя; размер в UI и API считается по-разному (SPEC §8.5).
- ⛔ Шесть видимых входов сервер не читает (`aspect_ratio`, `lock_ratio`,
  `center_x/y`, `frame_w/h`) — состояние в `crop_rect`/`output_size` (SPEC §8.2).
- ⛔ Батч: размер от первого кадра (SPEC §8.3).
- ⛔ Не добавлять свои кнопки «Маска»/«Загрузить файл» в Nodes 2.0: загрузка —
  иконка-папка в комбо, маска — штатные точки входа фронтенда.
- `getMinHeight` вместо позднего `computeLayoutSize`; upstream по имени сокета;
  `hideInPanel` для скрытых DOM-виджетов; никаких таймеров для layout.
- Хук `.githooks/commit-msg`: названный в сообщении файл обязан быть в индексе,
  а утверждение об успехе — иметь след приёмки.

## 5. Следующие шаги

- **Перезапустить ComfyUI** и перепрогнать
  `D:/ComfyUI_windows_portable/python_embeded/python.exe tests/_probe_live_prompt_crop_image.py`
  — проверка «старый промпт со строкой в `image`» должна позеленеть.
- Проверить в живом UI: файл-источник + маска → результат не 8×8.
- `tests/_diag_workflow_links.py` (untracked) — разовая диагностика связей,
  в репозиторий не коммитился.
- `start-comfyui.bat` в `G:\ИИ web` — по аналогии с `stop-comfyui.bat`.

## 6. Связанные файлы

- Код: `crop_image.py`, `web/js/crop_image.js`, `__init__.py`,
  `locales/{ru,en}/nodeDefs.json`.
- Документация: `SPECIFICATION.md` (§8 решения, §9.4 Mask Editor + регрессия
  8×8), `README.md`.
- Проверки: `tests/_test_crop_image.py` (110), `tests/_smoke_crop_image.mjs`,
  `tests/_audit_crop_image.mjs` (179), `tests/_probe_live_prompt_crop_image.py`
  (7, живая, промпт + очередь), `tests/_probe_live_dom_crop_image.py` (живая
  DOM), `tests/_check_mask_file.py`, `tests/_check_file_mode.py`,
  `tests/_check_image_size.py` (серверные).
- Рабочая копия: `D:\ComfyUI_windows_portable\ComfyUI\custom_nodes\Degg_Crop_Image`
  (синхронизирована; тесты не копируются).
- Репро-воркфлоу: `D:\ComfyUI_windows_portable\ComfyUI\user\default\workflows\Test Crop.json`.
