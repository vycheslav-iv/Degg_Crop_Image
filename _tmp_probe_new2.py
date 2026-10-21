from pathlib import Path

p = Path("F:/AI_projects/Custom_node_ComfyUI/Degg_Crop_Image/tests/_probe_live_dom_crop_image.py")
s = p.read_text(encoding="utf-8")


def sub(old, new):
    global s
    assert old in s, "NOT FOUND: " + old[:100]
    s = s.replace(old, new, 1)


# Оставшиеся setFrac → __setFrame
s = s.replace("window.__probeCrop.__deggCrop.setFrac(", "window.__setFrame(")

# Сценарий «устаревший ручной размер»: задаём кадр + скрытое поле ручного
# размера и перечитываем состояние — как при открытии воркфлоу.
sub(
    """                " e.outputSize.value = '1448,1448';"
                " e.readManual();"
                " e.setFrac(e.state.frac);"
                " return true; })()\"""",
    """                " window.__setFrame(e.state.frac, '1448,1448');"
                " return true; })()\"""",
)

p.write_text(s, encoding="utf-8", newline="\n")
print("probe: сценарий устаревшего размера — через реальную загрузку состояния")
