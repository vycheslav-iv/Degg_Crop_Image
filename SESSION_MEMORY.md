# Память сессии — Degg Crop Image (2026-10-21)

> Покажи этот файл агенту, чтобы продолжить работу.
> Всегда сверяйся с `AGENTS.md`, `SPECIFICATION.md` и `Техническое задание.txt`.

---

## 1. Что делали в этой сессии (кратко)
- Довели ноду по замечаниям: папка/имя (`My_custom_nodes/Image`, `Degg Crop Image`),
  английские значения ресампла, заливка как фон аутпейнта (слой под изображением),
  разрешение и кратность — паритет с нативной «Выбор разрешения».
- Создали git-репозиторий, добавили `SPECIFICATION.md`, обновили README,
  закоммитили и запушили; обновили скил `comfyui-native-widget-look`.

## 2. Итоговое состояние кода
- `crop_image.py:111` `output_size` — `1 MP = 1024²`, `round` каждой стороны
  независимо, `step = max(1, int(multiplicity))`; 16:9/2MP/8 → 1928×1088.
- `crop_image.py:135` `frame_geometry` — границы наружу (`floor`/`ceil`),
  отрицательные координаты = аутпейнт, холст предзаполняется цветом.
- `crop_image.py:215` `render_frames` — `(canvas, mask_out, out_w, out_h)`, batch.
- `crop_image.py:281` `INPUT_TYPES` — `multiplicity` INT 8…128 step 4,
  категория `My_custom_nodes/Image`.
- `web/js/crop_image.js:91` `computeOutputSize` — та же формула, шаг из виджета.
- `web/js/crop_image.js:352` `createEditor` — `fill`-слой создаётся раньше
  изображения; `hideInPanel: true`; `computeLayoutSize` на виджете.
- `web/js/crop_image.js:491` `layout()` — пересчёт DOM; callback'и виджетов
  (`dim_outside/fill/megapixels/multiplicity`) зовут его явно.
- `locales/ru/nodeDefs.json` — `display_name: "Degg Crop Image"`, ресампл не переводится.

## 3. Проблемы, которые встречались (и как решали)
- Превью/плашка не появлялись или DOM-виджет ломался в панели свойств —
  `hideInPanel: true` (не `surfaces.vueNode:'never'`), callback → `layout()`.
- Заливка рисовалась поверх изображения — поменяли порядок слоёв в DOM.
- Размер 1880×1064 вместо 1928×1088 — переписали на формулу ядра (1024², `round`).
- Шаг INT-виджета в пробе читался как 40 — это `options.step = step*10`
  (legacy), реальный шаг в `options.step2`; зафиксировано в скиле.

## 4. Что важно не сломать при продолжении работы
- Формула `output_size` и `computeOutputSize` обязаны совпадать (тест паритета).
- Шаг кратности не клампить внутри функций — диапазон задаёт `INPUT_TYPES`.
- Плашка показывает фактические MP (ТЗ §4.5), не заказанные.
- `fill` — фон под изображением, не заливка внутри кадра.
- Значения combo — протокол, переводить только подписи (`getOptionLabel`).
- После правок Python — `python sync.py Degg_Crop_Image` и рестарт ComfyUI.

## 5. Следующие шаги (идеи, не сделано)
- Проверить в живом UI перетаскивание при зуме ×2 (координаты уже в долях).
- При желании вынести общий `output_size` в скил по resolution-parity.
- Прогнать полный `check.py` по всей папке перед следующим релизом.

## 6. Связанные файлы
- `crop_image.py`, `web/js/crop_image.js`, `__init__.py`, `check.json`
- `SPECIFICATION.md`, `README.md`, `Техническое задание.txt`
- `tests/_test_crop_image.py`, `_smoke_crop_image.mjs`, `_audit_crop_image.mjs`,
  `_probe_live_crop_image.py`, `_probe_live_dom_crop_image.py`
- Скил: `.opencode|.kilo|.agents/skills/comfyui-native-widget-look/SKILL.md`
