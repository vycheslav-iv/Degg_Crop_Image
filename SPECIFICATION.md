# Degg Crop Image — спецификация

Нода ComfyUI: кадрирование с интерактивной рамкой на теле ноды; рамка может
выходить за изображение (аутпейнт) без второго входа.

- Класс: `DeggCropImage`, отображаемое имя `Degg Crop Image`
- Категория: `My_custom_nodes/Image`
- Выходы: `IMAGE`, `MASK`, `width`, `height` (фактические размеры результата)

---

## 1. Файлы

| Файл | Роль |
|------|------|
| `crop_image.py` | Python-нода: `INPUT_TYPES`, `output_size`, `frame_geometry`, `render_frames`, `crop` |
| `__init__.py` | `NODE_CLASS_MAPPINGS` + `WEB_DIRECTORY = "./web"` |
| `web/js/crop_image.js` | LiteGraph-расширение: DOM-редактор, плашка, зум/пан, `computeOutputSize` |
| `locales/ru/nodeDefs.json` | переводы (EN — источник в Python) |
| `tests/` | Python-песочница, JS-смоук, статический аудит, живые пробы |

---

## 2. Контракт входов/выходов

| Вход | Тип | По умолчанию | Смысл |
|------|-----|--------------|-------|
| `image` | IMAGE | — | источник (батч `[B,H,W,C]`) |
| `lock_ratio` | BOOLEAN | `true` | удерживать пропорцию при перетаскивании |
| `aspect_ratio` | COMBO | `free` | протокол: `free,1:1,2:3,3:2,4:3,3:4,16:9,9:16,21:9` |
| `megapixels` | FLOAT 0.1…64 | `2.0` | целевая площадь, 1 MP = 1024² |
| `multiplicity` | INT 8…128, step 4 | `8` | кратность сторон результата |
| `resample` | COMBO | `lanczos` | `lanczos,bicubic,bilinear,nearest-exact` (без перевода) |
| `fill` | COMBO | `transparent` | `transparent,black,white,gray` — фон вне источника |
| `dim_outside` | INT 0…100 | `50` | затемнение вне рамки, только отображение |
| `mask` | MASK (опц.) | — | заменяет альфу источника (1 = непрозрачно) |
| `crop_rect` | STRING (скрыт) | полный кадр | `{x, y, w, h}` в долях изображения |

Порядок строк таблицы = порядок виджетов на теле ноды (после превью): `lock_ratio`,
`aspect_ratio`, `megapixels`, `multiplicity`, `resample`, `fill`, `dim_outside`.
`image`/`mask` — сокеты, `crop_rect` скрыт.

COMBO-значения — протокол: подписи меняются через `getOptionLabel`, значения в
workflow не переводятся.

---

## 3. Разрешение результата

Повторяет ядро ComfyUI (`comfy_extras/nodes_resolution.py`, «Выбор разрешения»):

```
total = megapixels * 1024 * 1024
scale = sqrt(total / (rw * rh))
width = max(step, round(rw * scale / step) * step)
height = max(step, round(rh * scale / step) * step)
step = max(1, int(multiplicity))
```

- `round` банковский (`py_round`), стороны округляются независимо;
- шаг не клампится внутри функции — диапазон `8…128` задаёт `INPUT_TYPES`
  (старые воркфлоу с меньшим шагом считаются как есть);
- примеры: `16:9 / 2 MP / 8 → 1928×1088`, `1:1 / 2 MP / 8 → 1448×1448`,
  `1:1 / 1 MP / 8 → 1024×1024`, `1:1 / 0.5 MP / 8 → 728×728`;
- плашка в JS показывает фактические `W × H` и фактические MP (1928×1088 = 2.10).

---

## 4. Геометрия кадра

`frame_geometry(src_w, src_h, rect, out_w, out_h)`:

1. рамка в долях → пиксельные границы источника, дробные границы
   раскрываются наружу (`floor` / `ceil`), чтобы не терять пиксели;
2. коэффициент `out_w / frame_w_px` (по X) и `out_h / frame_h_px` (по Y);
3. для каждой границы считаются координаты вставки в холст результата;
4. отрицательные координаты рамки = аутпейнт: холст предзаполняется цветом
   `fill`, изображение вставляется поверх.

`render_frames(...)` возвращает `(canvas, mask_out, out_w, out_h)`:

- `mask_out` — 1 внутри рамки, 0 в залитой области (для `transparent`);
- batch: общий размер, одинаковые координаты, каждый кадр обрабатывается отдельно;
- пустой вход → `(empty_image, zeros, 0, 0)`.

Заливка — **фон под изображением** (так же в DOM-редакторе: слой `fill` создаётся
раньше `img`) — внутри кадра картинку не перекрывает.

---

## 5. DOM-редактор (Nodes 2.0)

- `addDOMWidget(..., { hideInPanel: true, hideOnZoom: false })` — панель свойств
  иначе пишет `widget.width` в живой виджет и зажимает контент;
- `computeLayoutSize` возвращает `minHeight`, высота = ширина × соотношение +
  плашка (без `scrollHeight`/`offsetHeight`/`computeSize`);
- превью источника берётся по проводу мгновенно (`p:/o:/i:/n:`), без выполнения
  воркфлоу; смена изображения в upstream-ноде (загрузка в `LoadImage`) приходит
  ДВУМЯ каналами: legacy (`BaseWidget.setValue`, upload-виджет) зовёт
  `source.onWidgetChanged`, Nodes 2.0 (`createWidgetUpdateHandler`) зовёт только
  `widget.callback` и `node.onWidgetChanged` не вызывает. Поэтому обёрнуты оба
  канала: `watchedHandler` на хуке узла и `watchSourceWidgets()` на callback'ах
  виджетов источника (с восстановлением в `detachSource()`);
- смена картинки определяется подменой объекта `app.nodeOutputs[locator]`
  (`sourceOutputsChanged()`), а не ключом/именем файла: перезаливка файла с тем
  же именем не меняет ключ, но `setOutputsByLocatorId` всегда присваивает новый
  объект → принудительный `loadSource({ force: true })` с кэш-бастером;
- зум/пан графа не влияют на координаты: экранные координаты приводятся к
  локальным через `getBoundingClientRect`;
- изменения виджетов (`dim_outside`, `fill`, `megapixels`, `multiplicity`)
  прогоняются через обёртку callback → `layout()`, иначе DOM не перерисуется;
- бар редактора:
  - «Вписать» (`fitFrameFrac`) — равномерный масштаб ТЕКУЩЕЙ рамки
    `s = min(1/w, 1/h)` вверх и вниз до касания ближайших границ + центр;
    пропорции и режим не меняются;
  - «По центру» (`centerFrameFrac`) — только перенос центра рамки в центр
    изображения; размер и пропорции сохраняются, выход за границы разрешён;
  - «Сбросить» — рамка `0,0,1,1` + `aspect_ratio='free'` + `lock_ratio=false`;
    `megapixels`/`multiplicity`/`resample`/`fill`/`dim_outside` НЕ трогаются
    (это настройки результата, а не рамки); `applyAspect` не вызывается;
- язык: `Comfy.Locale` (через `app.extensionManager.setting.get`) → `ru` при
  префиксе `ru`, иначе `en`; словари в `LABELS`; подписи кнопок, combo
  (`getOptionLabel`) и замка (`options.on/off`) переприменяются на событии
  legacy-настроек `Comfy.Locale.change` — без перезапуска ComfyUI;
- состояние: `node.__deggCrop = { widget, layout, state, refresh, setFrac }`;
- `onConfigure`/`loadedGraphNode` → `refresh()`; сериализация рамки в `crop_rect`.

---

## 6. Инварианты

- никаких `setInterval` / `MutationObserver` / глобальных слушателей / canvas-рисования;
- только `window.comfyAPI` (не `scripts/app.js`), `registerExtension` +
  `beforeRegisterNodeDef`;
- `fill` не перекрывает изображение внутри кадра;
- `resample` и `aspect_ratio` — протокольные значения, переводятся только подписи;
- `IS_CHANGED` не нужен: все влияющие входы — обычные виджеты/входы.

---

## 7. Проверки

```bash
cd Degg_Crop_Image
python tests/_test_crop_image.py     # логика без torch
node   tests/_smoke_crop_image.mjs   # JS + паритет с Python
node   tests/_audit_crop_image.mjs   # контракт Python ↔ JS ↔ locales
python _process/check.py Degg_Crop_Image   # из корня бандла
```

Живые пробы (нужен запущенный ComfyUI): `tests/_probe_live_crop_image.py`,
`tests/_probe_live_dom_crop_image.py` — вторая проверяет ОБА канала смены входа:
legacy (`onWidgetChanged`) и Nodes 2.0 (`widget.callback` без `onWidgetChanged`),
плюс перезаливку файла с тем же именем.

Порядок виджетов проверяется дважды: `_test_crop_image.py` (ключи `INPUT_TYPES`) и
`_probe_live_dom_crop_image.py` (`node.widgets` живого узла) — оба сверяются со списком
из §2. Статический аудит держит тот же список (`EXPECTED`).

Кнопки проверяются на трёх уровнях: `_smoke_crop_image.mjs` — чистая математика
`centerFrameFrac`/`fitFrameFrac` (размер сохраняется; масштаб равномерный вверх и
вниз; центр совпадает), `_probe_live_dom_crop_image.py` — реальные клики по кнопкам
(рамка, `crop_rect`, `aspect_ratio`, `lock_ratio`, геометрия на сцене) и смена языка
на лету через `dispatchChange('Comfy.Locale', …)`, `_audit_crop_image.mjs` — связка
«кнопка → функция» и отсутствие старого «Вписать всё». Детекторы проверены
мутациями: `Math.min → Math.max` даёт 5 FAIL в смоуке, удаление
`setWidgetValue(lockWidget, false)` — FAIL в аудите.
