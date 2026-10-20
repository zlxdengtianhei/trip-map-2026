"""读行程数据：地点库、天页、要提前做的、决定、图片来源。格式由 docs/格式.md 定，解析只在这一个文件里。

check.py 与 build_site.py 都从这里读；读到不合格式的地方记进 problems（文件、行号、说明），不抛异常，
让检查能一次报全、网站也尽量照常生成。
"""
import re
from datetime import date
from pathlib import Path

import yaml

AREAS = {"布达佩斯": "budapest", "维也纳": "vienna", "湖区": "lakes"}
KINDS = {"徒步", "抱石", "景点", "小镇", "美食", "观景", "交通", "住宿", "购物", "其他"}
PRIORITIES = ["必去", "最建议", "可选"]
PACES = ["紧", "稍紧", "中", "松"]
TODO_STATES = ["待办", "进行中", "已完成", "不做了"]
SECTIONS = ["一句话", "推荐的一套", "按时段的候选", "分头与汇合", "下雨怎么办", "交通", "明天注意"]
PLACE_REQUIRED = ["id", "name", "area", "kind", "who", "priority", "reason"]
DAY_REQUIRED = ["date", "title", "area", "pace"]
REF = re.compile(r"\[\[([^\]\n]+)\]\]")
ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
HHMM = re.compile(r"^\d{1,2}:\d{2}$")
WEEK = "一二三四五六日"


class Problems(list):
    def add(self, path, line, msg):
        self.append((str(path), line, msg))


def _rel(root, p):
    return Path(p).relative_to(root) if Path(p).is_absolute() else Path(p)


def load_places(root, problems):
    path = root / "data" / "places.yaml"
    if not path.exists():
        problems.add("data/places.yaml", 0, "没有地点库")
        return {}
    text = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text) or []
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        problems.add("data/places.yaml", mark.line + 1 if mark else 0, f"YAML 读不了：{getattr(e, 'problem', e)}")
        return {}
    starts = [text[:m.start()].count("\n") + 1 for m in re.finditer(r"^- id:", text, re.M)]
    places = {}
    if not isinstance(data, list):
        problems.add("data/places.yaml", 1, "顶层要是列表（每个地点一条“- id: …”）")
        return {}
    for i, p in enumerate(data):
        if not isinstance(p, dict):
            problems.add("data/places.yaml", 0, f"第 {i + 1} 条不是键值对")
            continue
        pid = str(p.get("id", ""))
        ln = starts[i] if len(starts) == len(data) else 0
        p["_line"] = ln
        for k in PLACE_REQUIRED:
            if p.get(k) in (None, ""):
                problems.add("data/places.yaml", ln, f"{pid or '（无 id）'} 缺必填字段 {k}")
        if pid and not ID.match(pid):
            problems.add("data/places.yaml", ln, f"id “{pid}” 只能用小写字母、数字、连字符")
        if pid in places:
            problems.add("data/places.yaml", ln, f"id “{pid}” 重复（第一次在第 {places[pid]['_line']} 行）")
            continue
        if p.get("area") and p["area"] not in AREAS:
            problems.add("data/places.yaml", ln, f"{pid} 的 area “{p['area']}” 不是 {' | '.join(AREAS)}（地区页收不到它）")
        if p.get("priority") and p["priority"] not in PRIORITIES:
            problems.add("data/places.yaml", ln, f"{pid} 的 priority “{p['priority']}” 不是 {' | '.join(PRIORITIES)}")
        if p.get("kind") and p["kind"] not in KINDS:
            problems.add("data/places.yaml", ln, f"{pid} 的 kind “{p['kind']}” 不在格式页的取值里")
        c = p.get("coords")
        if c is not None and not (isinstance(c, list) and len(c) == 2 and all(isinstance(x, (int, float)) for x in c)
                                  and -90 <= c[0] <= 90 and -180 <= c[1] <= 180):
            problems.add("data/places.yaml", ln, f"{pid} 的 coords 要写成 [纬度, 经度]")
            p["coords"] = None
        if pid:
            places[pid] = p
    return places


def load_images(root, problems, places):
    path = root / "data" / "images.yaml"
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        problems.add("data/images.yaml", 0, f"YAML 读不了：{e}")
        return {}
    lines = {m.group(1): text[:m.start()].count("\n") + 1 for m in re.finditer(r"^([^\s#][^:]*):", text, re.M)}
    for pid, img in data.items():
        if pid not in places:
            problems.add("data/images.yaml", lines.get(pid, 0), f"{pid} 不在地点库里")
        if isinstance(img, dict) and img.get("file") and not (root / img["file"]).exists():
            problems.add("data/images.yaml", lines.get(pid, 0), f"{pid} 的图片文件 {img['file']} 不存在")
    return data


def _front_matter(text):
    m = re.match(r"---\n(.*?)\n---\n?", text, re.S)
    if not m:
        return None, text, 0
    return m.group(1), text[m.end():], m.group(0).count("\n")


def parse_candidate(text):
    """“[[id]] 最建议｜队友｜理由” → {ref, priority, who, reason}；不合这个样子的整条当 reason。"""
    parts = [x.strip() for x in re.split(r"[｜|]", text)]
    head = parts[0]
    m = REF.search(head)
    if not m:
        return {"ref": None, "priority": None, "who": None, "reason": text.strip()}
    rest = REF.sub("", head).strip()
    return {"ref": m.group(1).strip(), "priority": rest or None, "who": parts[1] if len(parts) > 1 else None,
            "reason": "｜".join(parts[2:]) if len(parts) > 2 else ""}


def load_day(root, path, problems):
    rel = _rel(root, path)
    text = Path(path).read_text(encoding="utf-8")
    fm, body, offset = _front_matter(text)
    day = {"file": str(rel), "slug": "day-" + Path(path).stem, "meta": {}, "sections": {}, "order": [],
           "slots": [], "refs": [], "recommended": []}
    if fm is None:
        problems.add(rel, 1, "缺 YAML 头（--- … ---）")
    else:
        try:
            day["meta"] = yaml.safe_load(fm) or {}
        except yaml.YAMLError as e:
            problems.add(rel, 1, f"YAML 头读不了：{e}")
    meta = day["meta"]
    for k in DAY_REQUIRED:
        if meta.get(k) in (None, ""):
            problems.add(rel, 1, f"YAML 头缺 {k}")
    d = meta.get("date")
    if isinstance(d, str):
        try:
            d = date.fromisoformat(d)
        except ValueError:
            problems.add(rel, 1, f"date “{d}” 要写成 YYYY-MM-DD")
            d = None
    if isinstance(d, date):
        meta["date"] = d
        if Path(path).stem != f"{d:%m-%d}":
            problems.add(rel, 1, f"文件名 {Path(path).name} 和 date {d} 对不上")
    else:
        meta["date"] = None
    if meta.get("pace") and meta["pace"] not in PACES:
        problems.add(rel, 1, f"pace “{meta['pace']}” 不是 {' | '.join(PACES)}")
    anchors = []
    for a in meta.get("anchors") or []:
        t = str(a.get("time", "")) if isinstance(a, dict) else ""
        if not isinstance(a, dict) or not a.get("text") or not HHMM.match(t):
            problems.add(rel, 1, f"anchors 里这一条要有 time（HH:MM）和 text：{a}")
            continue
        anchors.append({"time": t.zfill(5), "text": str(a["text"])})
    meta["anchors"] = sorted(anchors, key=lambda x: x["time"])

    cur, slot = None, None
    for i, line in enumerate(body.split("\n"), offset + 1):
        h2 = re.match(r"^##\s+(.+?)\s*$", line)
        if h2:
            cur, slot = h2.group(1), None
            if cur not in SECTIONS:
                problems.add(rel, i, f"二级标题“{cur}”不在格式页的固定标题里（{'、'.join(SECTIONS)}）")
            if cur in day["sections"]:
                problems.add(rel, i, f"二级标题“{cur}”重复")
            day["sections"].setdefault(cur, {"line": i, "lines": []})
            day["order"].append(cur)
            continue
        for m in REF.finditer(line):
            day["refs"].append((m.group(1).strip(), i))
            if cur == "推荐的一套" and m.group(1).strip() not in day["recommended"]:
                day["recommended"].append(m.group(1).strip())
        if cur is None:
            if line.strip():
                problems.add(rel, i, "正文要放在二级标题下")
            continue
        day["sections"][cur]["lines"].append(line)
        if cur == "按时段的候选":
            h3 = re.match(r"^###\s+(.+?)\s*$", line)
            if h3:
                slot = {"title": h3.group(1), "line": i, "items": []}
                day["slots"].append(slot)
            elif re.match(r"^[-*]\s+", line):
                if slot is None:
                    slot = {"title": "", "line": i, "items": []}
                    day["slots"].append(slot)
                item = parse_candidate(re.sub(r"^[-*]\s+", "", line))
                item["line"] = i
                slot["items"].append(item)
                if item["priority"] and item["priority"] not in PRIORITIES:
                    problems.add(rel, i, f"候选的优先级“{item['priority']}”不是 {' | '.join(PRIORITIES)}")
            elif line.startswith((" ", "\t")) and line.strip() and slot and slot["items"]:
                slot["items"][-1]["reason"] += " " + line.strip()
    for s in day["slots"]:
        if len(s["items"]) > 3:
            problems.add(rel, s["line"], f"时段“{s['title']}”有 {len(s['items'])} 条候选，格式页要求 1—3 条")
    for name, sec in day["sections"].items():
        sec["text"] = "\n".join(sec["lines"]).strip()
    return day


def load_days(root, problems):
    folder = root / "trip" / "days"
    days = [load_day(root, p, problems) for p in sorted(folder.glob("*.md"))] if folder.exists() else []
    if not days:
        problems.add("trip/days", 0, "没有天页")
    days.sort(key=lambda d: (d["meta"].get("date") or date.max, d["file"]))
    return days


def parse_tables(text, offset=0):
    """Markdown 表格 → [{"section": 所在二级标题, "header": [...], "rows": [(行号, {列名: 值})]}]。"""
    tables, cur_sec, tbl = [], None, None
    for i, line in enumerate(text.split("\n"), offset + 1):
        h = re.match(r"^##\s+(.+?)\s*$", line)
        if h:
            cur_sec, tbl = h.group(1), None
            continue
        if not line.strip().startswith("|"):
            tbl = None
            continue
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip())[1:-1]]
        if tbl is None:
            tbl = {"section": cur_sec, "header": cells, "line": i, "rows": []}
            tables.append(tbl)
        elif all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        else:
            tbl["rows"].append((i, dict(zip(tbl["header"], cells + [""] * (len(tbl["header"]) - len(cells))))))
    return tables


def load_todo(root, problems):
    path = root / "trip" / "要提前做的.md"
    if not path.exists():
        problems.add("trip/要提前做的.md", 0, "没有这个文件")
        return []
    tables = parse_tables(path.read_text(encoding="utf-8"))
    if not tables or "状态" not in tables[0]["header"]:
        problems.add("trip/要提前做的.md", 0, "找不到带“状态”列的表")
        return []
    rows = []
    for ln, r in tables[0]["rows"]:
        if r.get("状态") not in TODO_STATES:
            problems.add("trip/要提前做的.md", ln, f"状态“{r.get('状态')}”不是 {' | '.join(TODO_STATES)}")
        rows.append({**r, "_line": ln})
    return rows


def load_decisions(root, problems):
    path = root / "trip" / "决定.md"
    if not path.exists():
        problems.add("trip/决定.md", 0, "没有这个文件")
        return {"done": [], "open": []}
    tables = parse_tables(path.read_text(encoding="utf-8"))
    out = {"done": [], "open": []}
    for t in tables:
        key = {"已定": "done", "待你定": "open"}.get(t["section"])
        if key:
            out[key] += [{**r, "_line": ln} for ln, r in t["rows"]]
    if not any(t["section"] == "待你定" for t in tables):
        problems.add("trip/决定.md", 0, "找不到“## 待你定”下的表")
    if len(out["open"]) > 5:
        problems.add("trip/决定.md", out["open"][5]["_line"], f"“待你定”有 {len(out['open'])} 条，最多同时挂 5 条")
    return out


def load_all(root):
    root = Path(root)
    problems = Problems()
    places = load_places(root, problems)
    data = {"root": root, "places": places, "images": load_images(root, problems, places),
            "days": load_days(root, problems), "todo": load_todo(root, problems),
            "decisions": load_decisions(root, problems)}
    for d in data["days"]:
        for ref, ln in d["refs"]:
            if ref not in places:
                problems.add(d["file"], ln, f"[[{ref}]] 不在地点库里")
    return data, problems


def weekday(d):
    return "周" + WEEK[d.weekday()]
