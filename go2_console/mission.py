"""Turn a short Chinese instruction into a sequence of named places."""

from __future__ import annotations


def parse_mission(text: str, places: list[str]) -> dict:
    raw = (text or "").strip()
    if not raw:
        return {"ok": False, "error": "请写一句话，例如：先去饭堂，等人，再去教室"}
    if not places:
        return {"ok": False, "error": "还没有地点。先在「语义标注」里圈出区域并命名。"}
    ordered = sorted(places, key=len, reverse=True)
    found: list[tuple[int, str]] = []
    i = 0
    while i < len(raw):
        hit = None
        for name in ordered:
            if raw.startswith(name, i):
                hit = name
                break
        if hit:
            found.append((i, hit))
            i += len(hit)
        else:
            i += 1
    if not found:
        return {"ok": False, "error": "这句话里没有已命名的地点。"}
    steps = []
    for idx, (pos, name) in enumerate(found):
        steps.append({"op": "goto", "place": name})
        tail_end = found[idx + 1][0] if idx + 1 < len(found) else len(raw)
        between = raw[pos + len(name) : tail_end]
        if any(word in between for word in ("等", "停", "说", "交互", "拿", "放")):
            steps.append({"op": "wait", "place": name, "prompt": f"已到{name}。完成后点「继续」，或在下面输入一句话。"})
    # A wait mentioned before the first place still applies after arriving there.
    head = raw[: found[0][0]]
    if steps and steps[0]["op"] == "goto" and any(word in head for word in ("等", "停")):
        steps.insert(1, {"op": "wait", "place": steps[0]["place"], "prompt": f"已到{steps[0]['place']}。完成后点「继续」。"})
    return {"ok": True, "steps": steps, "text": raw}
