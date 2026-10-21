from pathlib import Path

p = Path("F:/AI_projects/Custom_node_ComfyUI/Degg_Crop_Image/tests/_probe_live_dom_crop_image.py")
s = p.read_text(encoding="utf-8")

# вставляем регресс в конец сценария (перед проверкой ошибок консоли)
anchor = """            # ── ошибки в консоли ──"""
block = '''            # ── РЕГРЕСС: НЕКВАДРАТНЫЙ источник ───────────────────────────
            # Пропорции результата считаются по ПИКСЕЛЯМ кадра. На квадратной
            # картинке (example.png 768×768) доли и пиксели совпадают, и ошибка
            # не видна. Здесь источник 2720×1536 (как в воркфлоу «Test Crop»),
            # а рамка задана долями — в пикселях это 16:9.
            await cdp.eval(
                "(() => { const n = window.__probeCrop, e = n.__deggCrop;"
                " e.state.img = { w: 2720, h: 1536 };"
                " e.setFrac({ x: 0.181794, y: 0.183037, w: 0.636412, h: 0.633926 });"
                " return true; })()"
            )
            await asyncio.sleep(0.35)
            wide_src = await cdp.eval(STATE_JS)
            fw = await cdp.eval("(() => window.__probeCrop.__deggCrop.frameW.value)()")
            fh = await cdp.eval("(() => window.__probeCrop.__deggCrop.frameH.value)()")
            check(
                "неквадратный источник: размер по ПИКСЕЛЯМ кадра (16:9 → 1928 × 1088)",
                int(fw) == 1928 and int(fh) == 1088
                and "1928 × 1088 px" in (wide_src.get("badge") or ""),
                {"w": fw, "h": fh, "badge": wide_src.get("badge")},
            )
            wfrac = wide_src.get("frac") or {}
            check(
                "неквадратный источник: доля кадра почти квадратная, но пиксели — 16:9",
                abs(wfrac.get("w", 0) / max(1e-6, wfrac.get("h", 0)) - 1) < 0.01
                and abs((wfrac.get("w", 0) * 2720) / (wfrac.get("h", 0) * 1536) - 16 / 9) < 0.01,
                wfrac,
            )
            # Возвращаем квадратный источник, чтобы следующие проверки считались как раньше.
            await cdp.eval(
                "(() => { const n = window.__probeCrop, e = n.__deggCrop;"
                " e.state.img = { w: 768, h: 768 };"
                " e.setFrac({ x: 0.25, y: 0.25, w: 0.5, h: 0.5 });"
                " return true; })()"
            )
            await asyncio.sleep(0.25)

'''
assert anchor in s, "anchor not found"
s = s.replace(anchor, block + anchor, 1)
p.write_text(s, encoding="utf-8", newline="\n")
print("probe: регресс на неквадратный источник")
