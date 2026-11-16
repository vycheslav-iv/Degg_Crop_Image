# Память сессии — Degg_Crop_Image (2026-11-16)

> Актуально на 2026-11-16. Всегда сверяйся с `AGENTS.md` и `SPECIFICATION.md`.
> Задачи и чем закрыты — `TASKS.md` (появился 2026-11-16, §7 ниже).

---

## 1. Что делали в этой сессии (кратко)
- **Код ноды не менялся** (`git diff` по отслеживаемым файлам пуст), в ComfyUI
  ничего не синхронизировалось — рабочая копия от 2026-11-09.
- Появился `TASKS.md`: 4 задачи закрыты с артефактами (`tests/…`, commit `3da2741`),
  2 открыты (T-5, T-6). Причина: задачи жили только в чате и в этой памяти, а память
  перезаписывается — прецедент 2026-10-03 (три задачи не выполнены при «успешном» отчёте).
- В корне бандла (вне git этой ноды) — правки процесса: `_process/PROCESS.md` §7.1
  (правила `TASKS.md`, словарь статусов) и §7.2 (минимум разделов SPEC + «отклонённые
  альтернативы»); `_process/check.py` — проверка задач (`tasks_problems`); `AGENTS.md`
  §2.3; скилы `comfyui-task-acceptance` §0 «вход в проект» и
  `comfyui-negative-result-audit` (формат отчёта аудита).

## 2. Итоговое состояние кода
- `crop_image.py:204` — `linked_inputs()`: имена, поданные ПРОВОДОМ (по
  исходному промпту через скрытые `PROMPT`/`UNIQUE_ID`).
- `crop_image.py:226` — `_wired_sockets()`: две подписи провода (литерал
  `[id, slot]` в промпте + `None` в kwargs при валидации); строка в сокете —
  НЕ провод (legacy-виджет Mask Editor).
- `crop_image.py:956,976` — `VALIDATE_INPUTS` / `IS_CHANGED` пропускают сокет,
  поданный проводом; `image_file` всё равно проверяется.
- `crop_image.py:398` — `_resize`: `lanczos` → fallback `bicubic`
  (`F.interpolate` такого режима не знает), `antialias` только bilinear/bicubic.
- `crop_image.py:483` — `input_file_path`: корень `input`, затем fallback
  `input/clipspace/` (файлы Mask Editor до фронтенда 1.58); containment по
  `realpath`+`commonpath` (`_input_path_checked`, :518).
- `crop_image.py:672` — `render_frames`: `device = getattr(image, "device", None)`;
  `canvas`/`mask_out` на этом device, альфа и `torch.ones` — к
  `patch.dtype/device`, painted-батч — `.to(device=image.device, dtype=image.dtype)`.
- `web/js/crop_image.js:327` — `formatBadge()`: MP через `MEGAPIXEL_UNIT`
  (=1024², :76); плашка и виджет печатают одно и то же число.
- `web/js/crop_image.js:1249` — комментарий `resetToImage`: кратность
  применяется позже в `resultSize()` (SPEC §8.3), не «не применяется».
- `SPECIFICATION.md` §6.1 (clipspace + провод), §8.5 (единица MP), §10.4
  (таблица «находка → красный замер → фикс»), §7 (числа прогонов).

## 3. Проблемы, которые встречались (и как решали)
- Валидация считала битым файл, который заменит провод → `_wired_sockets`.
- Старые детекторы аудита держали отпечаток старого выражения (`outputSizeFor`,
  `layoutBadge`) → правились ДЕТЕКТОРЫ, не откатывалась задача.
- Мутационный скрипт падал на кириллице в stdout (cp1251) →
  `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` + ascii-tail.
- Хук `commit-msg` блокирует `.py`-упоминание вне индекса: в сообщении писать
  «прогон проверки папки», а не `python _process/check.py`.
- 2026-11-16: VS Code показывал один репозиторий вместо всех — это не git, а
  `git.repositoryScanMaxDepth` (по умолчанию 1) против новой глубины
  `Custom_node/<нода>/.git`; вылечено настройкой `<корень>/.vscode/settings.json` = 3.

## 4. Что важно не сломать при продолжении работы
- Единица мегапикселя ОДНА: 1024² (паритет с ResolutionSelector и с Python
  `output_size`); `1e6`/`1 000 000` в коде, подсказках и локалях запрещены.
- Провод vs значение: строку в сокете `image` НЕЛЬЗЯ считать проводом.
- Маска: конвенция ComfyUI `1 = убрать`, видимая альфа = `1 − маска`.
- Кратность применяется ВСЕГДА (в т.ч. размер от картинки/«Сбросить»).
- Значения combo не переводить; RU-текст только в `locales/ru`.
- Python/`locales` → перезапуск ComfyUI; JS → Ctrl+F5.
- `TASKS.md`: `DONE` только с артефактом проверки в «Чем закрыта»; статус — из
  словаря §7.1, иначе прогон проверки проекта краснеет.

## 5. Следующие шаги (совпадают с `TASKS.md`)
- T-5: перезапустить ComfyUI + Ctrl+F5, перепрогнать живые пробы
  (`_probe_live_dom_crop_image.py`, `_probe_live_reset_params.py`,
  `_probe_live_workflow_switch.py`) — они измерялись до правок `3da2741`.
- T-6: глазами — плашка 1024×1024 → «1.00 MP»; проводной MP; mask-редактор с файлом
  из старого `input/clipspace/`.

## 6. Связанные файлы
- `crop_image.py`, `web/js/crop_image.js`, `SPECIFICATION.md`, `README.md`,
  `TASKS.md`, `locales/ru|en/nodeDefs.json`, `tests/_test_crop_image.py`,
  `tests/_smoke_crop_image.mjs`, `tests/_audit_crop_image.mjs`, `check.json`.
- Скилы: `.opencode|.kilo|.agents/skills/comfyui-mask-editor`,
  `comfyui-is-changed`, `comfyui-task-acceptance` (в корне бандла, не в git этой ноды).

## 7. Откуда правила про `TASKS.md`
- `_process/PROCESS.md` §7.1 — назначение, шаблон, словарь статусов, что проверяет
  `check.py`; §7.2 — минимум разделов `SPECIFICATION.md` (в т.ч. «отклонённые
  альтернативы»). `AGENTS.md` §2.3 — приёмка задачи.
- Живой пример формата — сам `TASKS.md` этого проекта.
