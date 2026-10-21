from pathlib import Path

ROOT = Path("F:/AI_projects/Custom_node_ComfyUI/Degg_Crop_Image")
audit = ROOT / "tests" / "_audit_crop_image.mjs"
a = audit.read_text(encoding="utf-8")


def sub(old, new):
    global a
    assert old in a, "NOT FOUND: " + old[:80]
    a = a.replace(old, new, 1)


# ── детекторы под новую архитектуру ────────────────────────────────────────
sub(
    """check("JS: persist через requestAnimationFrame", /persist[\\s\\S]{0,400}?requestAnimationFrame/.test(JS));""",
    """check("JS: сериализация через requestAnimationFrame", /persistState[\\s\\S]{0,400}?requestAnimationFrame/.test(JS));""",
)

sub(
    """check(
  "JS: «Сбросить» = рамка 0,0,1,1 + свободные + замок off",
  /buttonEls\\.reset[\\s\\S]{0,400}?state\\.frac = \\{ x: 0, y: 0, w: 1, h: 1 \\}[\\s\\S]{0,240}?programmatic\\(lockWidget, false\\)[\\s\\S]{0,200}?programmatic\\(aspectWidget, 'free'\\)/.test(JS),
);
check(
  "JS: «Сбросить» не применяет пропорцию (нет applyAspect в обработчике)",
  !/buttonEls\\.reset[\\s\\S]{0,500}?applyAspect/.test(JS),
);""",
    """check(
  "JS: «Сбросить» = рамка 0,0,1,1 + свободные + замок off + ручной размер сброшен",
  /function resetCrop\\(\\)[\\s\\S]{0,400}?state\\.frac = \\{ x: 0, y: 0, w: 1, h: 1 \\}[\\s\\S]{0,200}?state\\.manual = null[\\s\\S]{0,200}?programmatic\\(lockWidget, false\\)[\\s\\S]{0,200}?programmatic\\(aspectWidget, 'free'\\)/.test(JS),
);
check(
  "JS: «Сбросить» не применяет пропорцию (нет applyRatioPreset в resetCrop)",
  !/function resetCrop\\(\\)[\\s\\S]{0,600}?applyRatioPreset/.test(JS),
);""",
)

sub(
    """const lockBlock = (JS.split("if (lockWidget?.callback)")[1] || "").split("if (aspectWidget?.callback)")[0];""",
    """const lockBlock = (JS.split("onWidgetChange(lockWidget")[1] || "").split("onWidgetChange(centerXWidget")[0];""",
)

sub(
    """const aspectBlock = (JS.split("if (aspectWidget?.callback)")[1] || "").split("if (lockWidget?.callback)")[0];""",
    """const aspectBlock = (JS.split("onWidgetChange(aspectWidget")[1] || "").split("onWidgetChange(lockWidget")[0];""",
)

sub(
    """check(
  "JS: замок фиксирует ТЕКУЩИЕ пропорции рамки (currentPxRatio, без applyAspect)",
  /currentPxRatio\\(\\)/.test(lockBlock) && !/applyAspect/.test(lockBlock),
);""",
    """check(
  "JS: замок фиксирует ТЕКУЩИЕ пропорции рамки (cropRatio, без applyRatioPreset)",
  /cropRatio\\(\\)/.test(lockBlock) && !/applyRatioPreset/.test(lockBlock),
);""",
)

sub(
    """check(
  "JS: выбор пресета пропорций включает замок и применяет его",
  /programmatic\\(lockWidget, true\\)[\\s\\S]{0,80}?applyAspect\\(\\)/.test(aspectBlock),
);
check(
  "JS: «Свободные» снимает замок без изменения рамки",
  /aspectWidget\\.value === 'free'[\\s\\S]{0,160}?programmatic\\(lockWidget, false\\)/.test(aspectBlock)
    && !/aspectWidget\\.value === 'free'[\\s\\S]{0,160}?applyAspect/.test(aspectBlock),
);""",
    """check(
  "JS: выбор пресета пропорций включает замок и применяет его",
  /programmatic\\(lockWidget, true\\)[\\s\\S]{0,120}?applyRatioPreset\\(/.test(aspectBlock),
);
check(
  "JS: «Свободные» снимает замок без изменения рамки",
  /aspectWidget\\.value === 'free'[\\s\\S]{0,200}?programmatic\\(lockWidget, false\\)/.test(aspectBlock)
    && !/aspectWidget\\.value === 'free'[\\s\\S]{0,200}?applyRatioPreset/.test(aspectBlock),
);""",
)

sub(
    """check(
  "JS: координаты центра рамки — пересчёт в пикселях через нативные виджеты",
  /frameCenterOffset\\(state\\.frac, state\\.img\\.w, state\\.img\\.h\\)/.test(JS)
    && /moveFrameToOffset\\(state\\.frac, state\\.img\\.w, state\\.img\\.h, ox, oy\\)/.test(JS),
);""",
    """check(
  "JS: координаты центра рамки — пересчёт в пикселях через нативные виджеты",
  /frameCenterOffset\\(state\\.frac, state\\.img\\.w, state\\.img\\.h\\)/.test(JS)
    && /moveFrameToOffset\\(state\\.frac, state\\.img\\.w, state\\.img\\.h, offsetX, offsetY\\)/.test(JS),
);""",
)

# ── блок «тихая запись» и прочие новые детекторы → новая архитектура ──────
sub(
    """check(
  "JS: запись координат БЕЗ callback'а (тихая)",
  /function setWidgetValueQuiet\\([\\s\\S]{0,700}?widget\\.value\\s*=\\s*value/.test(JS) &&
    /setWidgetValueQuiet\\(centerXWidget,\\s*Math\\.round\\(offset\\.x\\)\\)/.test(JS) &&
    /setWidgetValueQuiet\\(centerYWidget,\\s*Math\\.round\\(offset\\.y\\)\\)/.test(JS),
);""",
    """check(
  "JS: единственная точка записи в виджет (writeWidget), тихая по умолчанию",
  /function writeWidget\\(widget, value, \\{ call = false \\} = \\{\\}\\)/.test(JS) &&
    /writeWidget\\(centerXWidget, Math\\.round\\(offset\\.x\\)\\)/.test(JS) &&
    /writeWidget\\(centerYWidget, Math\\.round\\(offset\\.y\\)\\)/.test(JS) &&
    !/function setWidgetValueQuiet/.test(JS) &&
    !/function setWidgetValue\\(/.test(JS),
);
check(
  "JS: программная запись (programmatic) вызывает callback под защитой suppress",
  /function programmatic\\(widget, value\\)[\\s\\S]{0,300}?state\\.suppress \\+= 1/.test(JS) &&
    /state\\.suppress -= 1/.test(JS) &&
    /writeWidget\\(widget, value, \\{ call: true \\}\\)/.test(JS),
);""",
)

audit.write_text(a, encoding="utf-8", newline="\n")
print("audit: детекторы (первый блок) переписаны под новую архитектуру")
