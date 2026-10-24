"""读行程数据：地点库、天页、要提前做的、决定、图片来源。格式由 docs/格式.md 定，解析只在这一个文件里。

check.py 与 build_site.py 都从这里读；读到不合格式的地方记进 problems（文件、行号、说明），不抛异常，
让检查能一次报全、网站也尽量照常生成。

也是查询工具（网站与 agent 共用同一组函数）：
  python3 tools/tripdata.py near <地点id 或 纬度,经度> [--km 5] [--kind 美食]   # 附近的地点（≤2.5 km 步行可达）
  python3 tools/tripdata.py find <词> [<词> …]                                  # 搜 name、name_local、reason、notes
  python3 tools/tripdata.py day <MM-DD>                                         # 按时间轴打印一天（没有时间轴就打印推荐的一套）
"""
import argparse
import math
import re
import sys
import unicodedata
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
# 地点的可选小节 trail（徒步路线的难度、长度、评分等，见 docs/格式.md 第 1 节）
TRAIL_DIFFICULTY = ["简单", "中等", "困难"]
TRAIL_TYPES = ["环线", "往返", "单程"]
TRAIL_REQUIRED = ["difficulty", "length_km", "source"]
TRAIL_TEXT = ["time", "scenery", "autumn", "source"]  # 一句文字，不换行
TRAIL_KEYS = ["difficulty", "length_km", "ascent_m", "time", "type", "rating", "reviews", "url", "scenery", "autumn", "source"]
TAKEN = re.compile(r"^\d{4}(?:-\d{2}-\d{2})?$")
# 第三轮新字段（docs/格式.md 第 1、2 节）：票价、评分、优惠、一天的时间轴
PRICE_KEYS = ["adult", "student", "youth", "combo", "note", "source", "checked"]
PRICE_REQUIRED = ["adult", "source", "checked"]
RATING_KEYS = ["score", "reviews", "source", "checked"]
OFFER_SCOPES = ["全程", "布达佩斯", "维也纳", "湖区", "城际交通"]
OFFER_KINDS = ["证件", "交通票", "城市卡", "联票", "折扣", "买票技巧"]
OFFER_FOR_BASE = ["4 人", "队友"]   # 再加地点库里 who 出现过的人名（人名不写进代码：代码会原样发布到公开站）
OFFER_VERDICTS = ["推荐", "看情况", "不划算", "已有"]
OFFER_REQUIRED = ["id", "name", "scope", "kind", "what", "saves", "for", "verdict", "why", "price", "how", "source", "checked"]
# 买票与预约（data/bookings.yaml，docs/格式.md 第 9 节）
BOOKING_KINDS = ["车票", "门票", "寄存", "住处", "订位", "联系"]
BOOKING_STATUS = ["待买", "待订", "待联系", "已买", "已订", "已联系", "现场买", "不用了"]
BOOKING_OPEN = ["待买", "待订", "待联系"]        # 还没办的
BOOKING_DONE = ["已买", "已订", "已联系", "不用了"]  # 办完或不用办的
BOOKING_WHY = ["会涨价", "会卖完", "只凭预约", "要提前约", "不急，现场买"]
BOOKING_URGENT = ["会涨价", "会卖完", "只凭预约", "要提前约"]
BOOKING_REQUIRED = ["id", "day", "kind", "what", "who", "status"]
BOOKING_TEXT = ["what", "who", "time", "deadline", "price", "if_missed", "notes", "source", "checked"]
BOOKING_CONTACT_KEYS = ["name", "tel", "email", "web", "lang"]
MMDD = re.compile(r"^(\d{2})-(\d{2})$")
PLAN_TYPES = ["玩", "路上", "吃", "机动", "硬节点", "预留"]
PLAN_SURE = ["定", "估", "不定"]
PLAN_WHO_BASE = ["全组", "队友"]    # 同上，再加地点库里 who 出现过的人名
PLAN_MAX = 3
WALK_KM = 2.5          # ≤ 这个距离算步行可达
NEAR_KM = 5.0          # 地图上标出的“附近”半径
EAT_KM = 1.5           # 时间轴“吃”块的“附近还有”半径
SKIP_NEAR = {"住宿", "交通"}   # 附近查询默认不含
DAY_REQUIRED = ["date", "title", "area", "pace"]
REF = re.compile(r"\[\[([^\]\n]+)\]\]")
ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
HHMM = re.compile(r"^\d{1,2}:\d{2}$")
WEEK = "一二三四五六日"


class _NoDupLoader(yaml.SafeLoader):
    """safe_load，但同一层有重复的键就报错（否则 YAML 只留后一个，前一个静默丢掉）。"""


def _map_no_dup(loader, node, deep=False):
    seen = set()
    for k, _ in node.value:
        key = loader.construct_object(k, deep=deep)
        if key in seen:
            raise yaml.constructor.ConstructorError(None, None, f"重复的键“{key}”（前一个会被静默丢掉）", k.start_mark)
        seen.add(key)
    return loader.construct_mapping(node, deep)


_NoDupLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _map_no_dup)


def _load(text):
    return yaml.load(text, Loader=_NoDupLoader)


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
        data = _load(text) or []
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
        _check_pick_fields(p, pid, ln, problems)
        _check_trail(p, pid, ln, problems)
        _check_price(p, pid, ln, problems)
        _check_rating(p, pid, ln, problems)
        _check_offer_list(p, pid, ln, problems)
        if pid:
            places[pid] = p
    return places


def _check_pick_fields(p, pid, ln, problems):
    """可选的 heat、sources、transit（“选地方”页与天页候选用）；写错的报出来并当作没写，网站照常生成。"""
    h = p.get("heat")
    if h is not None and (isinstance(h, bool) or not isinstance(h, int) or h < 0):
        problems.add("data/places.yaml", ln, f"{pid} 的 heat 要写成 0 或正整数（资料里提到它的笔记篇数），现在是 “{h}”")
        p["heat"] = None
    s = p.get("sources")
    if s is not None:
        if not isinstance(s, list) or not all(isinstance(x, str) and x.strip() for x in s):
            problems.add("data/places.yaml", ln, f"{pid} 的 sources 要写成列表，每条一个相对仓库根的资料路径")
            p["sources"] = None
        else:
            for x in s:
                if x.startswith("/") or ".." in Path(x).parts:
                    problems.add("data/places.yaml", ln, f"{pid} 的 sources 里 “{x}” 要写相对仓库根的路径（不以 / 开头、不含 ..）")
    t = p.get("transit")
    if t is not None:
        if not isinstance(t, str) or not t.strip():
            problems.add("data/places.yaml", ln, f"{pid} 的 transit 要写成一句文字（交通与单程时间）")
            p["transit"] = None
        elif "\n" in t.strip():
            problems.add("data/places.yaml", ln, f"{pid} 的 transit 只写一句，不换行")
            p["transit"] = " ".join(t.split())


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _check_trail(p, pid, ln, problems):
    """可选的 trail 小节：写错的字段报出来并当作没写（整节不是键值对就整节当没写），网站照常生成。"""
    t = p.get("trail")
    if t is None:
        return
    where = f"{pid} 的 trail"
    if not isinstance(t, dict):
        problems.add("data/places.yaml", ln, f"{where} 要写成键值对小节（difficulty、length_km 等，见格式页第 1 节）")
        p["trail"] = None
        return
    for k in list(t):
        if k not in TRAIL_KEYS:
            problems.add("data/places.yaml", ln, f"{where} 里的 {k} 不是格式页的字段（{'、'.join(TRAIL_KEYS)}）")
            t.pop(k)
    bad = []
    v = t.get("difficulty")
    if v is not None and v not in TRAIL_DIFFICULTY:
        bad.append(("difficulty", f"要写 {' | '.join(TRAIL_DIFFICULTY)}"))
    v = t.get("type")
    if v is not None and v not in TRAIL_TYPES:
        bad.append(("type", f"要写 {' | '.join(TRAIL_TYPES)}"))
    v = t.get("length_km")
    if v is not None and not (_num(v) and v > 0):
        bad.append(("length_km", "要写成大于 0 的数（公里）"))
    v = t.get("ascent_m")
    if v is not None and not (_num(v) and v >= 0):
        bad.append(("ascent_m", "要写成 0 或正数（米）"))
    v = t.get("rating")
    if v is not None and not (_num(v) and 0 <= v <= 5):
        bad.append(("rating", "要写成 0—5 的数"))
    v = t.get("reviews")
    if v is not None and not (isinstance(v, int) and not isinstance(v, bool) and v >= 0):
        bad.append(("reviews", "要写成 0 或正整数（评价条数）"))
    v = t.get("url")
    if v is not None and not (isinstance(v, str) and re.match(r"^https?://\S+$", v.strip())):
        bad.append(("url", "要写成 http(s):// 开头的一个网址"))
    for k in TRAIL_TEXT:
        v = t.get(k)
        if v is None:
            continue
        if not isinstance(v, str) or not v.strip():
            bad.append((k, "要写成一句文字"))
        elif "\n" in v.strip():
            problems.add("data/places.yaml", ln, f"{where} 的 {k} 只写一句，不换行")
            t[k] = " ".join(v.split())
    for k, why in bad:
        problems.add("data/places.yaml", ln, f"{where} 的 {k} {why}，现在是 “{t[k]}”")
        t.pop(k)
    for k in TRAIL_REQUIRED:
        if t.get(k) in (None, "") and k not in dict(bad):
            problems.add("data/places.yaml", ln, f"{where} 缺必填字段 {k}")
    if isinstance(t.get("url"), str):
        t["url"] = t["url"].strip()


def _check_taken(img, pid, ln, problems):
    """图片的可选字段 taken（拍摄年份或日期）：统一成 "YYYY" 或 "YYYY-MM-DD" 文字；写错的报出来并当作没写。"""
    v = img.get("taken")
    if v is None:
        return
    if isinstance(v, date):  # YAML 会把不带引号的 2019-05-03 读成日期
        s = v.isoformat()[:10]
    elif isinstance(v, int) and not isinstance(v, bool):
        s = str(v)
    else:
        s = str(v).strip()
    ok = bool(TAKEN.match(s))
    if ok and len(s) == 10:
        try:
            date.fromisoformat(s)
        except ValueError:
            ok = False
    if ok and not 1826 <= int(s[:4]) <= date.today().year:
        ok = False
    if not ok:
        problems.add("data/images.yaml", ln, f"{pid} 的 taken 要写成拍摄年份 YYYY 或日期 YYYY-MM-DD（不晚于今年），现在是 “{v}”")
        img.pop("taken")
        return
    img["taken"] = s


def _text_field(t, k, where, ln, problems, path="data/places.yaml"):
    """t[k] 要是一句文字（不换行）；写错的报出来并去掉，换行的压成一行。返回字段现在是否有效。"""
    v = t.get(k)
    if v is None:
        return False
    if isinstance(v, date):
        t[k] = v.isoformat()
        return True
    if not isinstance(v, str) or not v.strip():
        problems.add(path, ln, f"{where} 的 {k} 要写成一句文字，现在是 “{v}”")
        t.pop(k)
        return False
    if "\n" in v.strip():
        problems.add(path, ln, f"{where} 的 {k} 只写一句，不换行")
    t[k] = " ".join(v.split())
    return True


def _check_price(p, pid, ln, problems):
    """可选的 price 小节（门票或票价）：写错的字段报出来并去掉；整节不是键值对就整节当作没写。"""
    t = p.get("price")
    if t is None:
        return
    where = f"{pid} 的 price"
    if not isinstance(t, dict):
        problems.add("data/places.yaml", ln, f"{where} 要写成键值对小节（adult、student 等，见格式页第 1 节）")
        p["price"] = None
        return
    for k in list(t):
        if k not in PRICE_KEYS:
            problems.add("data/places.yaml", ln, f"{where} 里的 {k} 不是格式页的字段（{'、'.join(PRICE_KEYS)}）")
            t.pop(k)
    for k in PRICE_KEYS:
        _text_field(t, k, where, ln, problems)
    for k in PRICE_REQUIRED:
        if t.get(k) in (None, ""):
            problems.add("data/places.yaml", ln, f"{where} 缺必填字段 {k}")


def _check_rating(p, pid, ln, problems):
    """可选的 rating 小节（第三方评分）：写错的字段报出来并去掉；缺 score 或 source 就整节当作没写。"""
    t = p.get("rating")
    if t is None:
        return
    where = f"{pid} 的 rating"
    if not isinstance(t, dict):
        problems.add("data/places.yaml", ln, f"{where} 要写成键值对小节（score、reviews、source、checked）")
        p["rating"] = None
        return
    for k in list(t):
        if k not in RATING_KEYS:
            problems.add("data/places.yaml", ln, f"{where} 里的 {k} 不是格式页的字段（{'、'.join(RATING_KEYS)}）")
            t.pop(k)
    v = t.get("score")
    if v is not None and not (_num(v) and 0 <= v <= 5):
        problems.add("data/places.yaml", ln, f"{where} 的 score 要写成 0—5 的数，现在是 “{v}”")
        t.pop("score")
    v = t.get("reviews")
    if v is not None and not (isinstance(v, int) and not isinstance(v, bool) and v >= 0):
        problems.add("data/places.yaml", ln, f"{where} 的 reviews 要写成 0 或正整数（评价条数），现在是 “{v}”")
        t.pop("reviews")
    for k in ("source", "checked"):
        _text_field(t, k, where, ln, problems)
    for k in ("score", "source"):
        if t.get(k) in (None, ""):
            problems.add("data/places.yaml", ln, f"{where} 缺必填字段 {k}；整个 rating 当作没写")
            p["rating"] = None
            return


def _check_offer_list(p, pid, ln, problems):
    """地点的 offers：id 列表（写法在这里查；id 在 offers.yaml 里是否存在由 load_all 查）。"""
    v = p.get("offers")
    if v is None:
        return
    if not isinstance(v, list) or not all(isinstance(x, str) and ID.match(x) for x in v):
        problems.add("data/places.yaml", ln, f"{pid} 的 offers 要写成 id 列表（小写字母、数字、连字符），引用 data/offers.yaml")
        p["offers"] = None


def _check_more(img, pid, ln, root, problems):
    """图片的可选 more：[{file, author, license, source_page, …}]（每项同主图字段）；写错的项报出来并去掉。"""
    v = img.get("more")
    if v is None:
        return
    if not isinstance(v, list):
        problems.add("data/images.yaml", ln, f"{pid} 的 more 要写成列表，每项同主图字段")
        img.pop("more")
        return
    keep = []
    for i, m in enumerate(v, 1):
        where = f"{pid} 的 more 第 {i} 项"
        if not isinstance(m, dict) or not m.get("file"):
            problems.add("data/images.yaml", ln, f"{where} 要有 file（键值对）")
            continue
        miss = [k for k in ("author", "license", "source_page") if not m.get(k)]
        if miss:
            problems.add("data/images.yaml", ln, f"{where} 缺 {'、'.join(miss)}（署名与许可要写全）")
            continue
        if not (root / m["file"]).exists():
            problems.add("data/images.yaml", ln, f"{where} 的图片文件 {m['file']} 不存在")
            continue
        _check_taken(m, f"{pid} more[{i}]", ln, problems)
        keep.append(m)
    img["more"] = keep


def load_images(root, problems, places):
    path = root / "data" / "images.yaml"
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    try:
        data = _load(text) or {}
    except yaml.YAMLError as e:
        problems.add("data/images.yaml", 0, f"YAML 读不了：{e}")
        return {}
    lines = {m.group(1): text[:m.start()].count("\n") + 1 for m in re.finditer(r"^([^\s#][^:]*):", text, re.M)}
    for pid, img in data.items():
        if pid not in places:
            problems.add("data/images.yaml", lines.get(pid, 0), f"{pid} 不在地点库里")
        if isinstance(img, dict) and img.get("file") and not (root / img["file"]).exists():
            problems.add("data/images.yaml", lines.get(pid, 0), f"{pid} 的图片文件 {img['file']} 不存在")
        if isinstance(img, dict):
            _check_taken(img, pid, lines.get(pid, 0), problems)
            _check_more(img, pid, lines.get(pid, 0), root, problems)
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
           "slots": [], "refs": [], "recommended": [], "plans": []}
    if fm is None:
        problems.add(rel, 1, "缺 YAML 头（--- … ---）")
    else:
        try:
            day["meta"] = _load(fm) or {}
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
        return {"done": [], "open": [], "ask": []}
    tables = parse_tables(path.read_text(encoding="utf-8"))
    out = {"done": [], "open": [], "ask": []}
    for t in tables:
        key = {"已定": "done", "待你定": "open", "只要告诉我": "ask"}.get(t["section"])
        if key:
            out[key] += [{**r, "_line": ln} for ln, r in t["rows"]]
    if not any(t["section"] == "待你定" for t in tables):
        problems.add("trip/决定.md", 0, "找不到“## 待你定”下的表")
    if len(out["open"]) > 3:
        problems.add("trip/决定.md", out["open"][3]["_line"], f"“待你定”有 {len(out['open'])} 条，最多同时挂 3 条（只要用户告诉信息的放“## 只要告诉我”）")
    return out


def people(places):
    """地点库 who 字段里出现过的人名（“都行”不算）；时间轴的轨道、优惠的适用人群可以用这些名字。"""
    return sorted({p["who"] for p in places.values() if isinstance(p.get("who"), str) and p["who"] != "都行"} - set(PLAN_WHO_BASE))


def load_offers(root, problems, places=None):
    """data/offers.yaml（优惠与车票）→ 列表。文件不存在是正常的（还没整理），返回空列表、不报错。"""
    path = root / "data" / "offers.yaml"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    try:
        data = _load(text) or []
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        problems.add("data/offers.yaml", mark.line + 1 if mark else 0, f"YAML 读不了：{getattr(e, 'problem', e)}")
        return []
    if not isinstance(data, list):
        problems.add("data/offers.yaml", 1, "顶层要是列表（每条优惠一条“- id: …”）")
        return []
    starts = [text[:m.start()].count("\n") + 1 for m in re.finditer(r"^- id:", text, re.M)]
    out, seen = [], {}
    enums = {"scope": OFFER_SCOPES, "kind": OFFER_KINDS, "for": OFFER_FOR_BASE + people(places or {}), "verdict": OFFER_VERDICTS}
    for i, o in enumerate(data):
        if not isinstance(o, dict):
            problems.add("data/offers.yaml", 0, f"第 {i + 1} 条不是键值对")
            continue
        oid = str(o.get("id", ""))
        ln = starts[i] if len(starts) == len(data) else 0
        o["_line"] = ln
        where = f"优惠 {oid or '（无 id）'}"
        if not oid or not ID.match(oid):
            problems.add("data/offers.yaml", ln, f"{where} 的 id 只能用小写字母、数字、连字符")
            if not oid:
                continue
        if oid in seen:
            problems.add("data/offers.yaml", ln, f"id “{oid}” 重复（第一次在第 {seen[oid]} 行）")
            continue
        seen[oid] = ln
        for k in ("name", "what", "saves", "why", "price", "how", "source", "checked", "buy_when"):
            _text_field(o, k, where, ln, problems, "data/offers.yaml")
        dv = o.get("days")
        if dv is not None and not (isinstance(dv, list) and all(isinstance(x, str) and MMDD.match(x) for x in dv)):
            problems.add("data/offers.yaml", ln, f"{where} 的 days 要写成带引号的 MM-DD 列表，如 [\"10-27\", \"10-30\"]")
            o.pop("days")
        for k, allowed in enums.items():
            v = o.get(k)
            if v is not None and v not in allowed:
                problems.add("data/offers.yaml", ln, f"{where} 的 {k} “{v}” 不是 {' | '.join(allowed)}")
                o.pop(k)
        for k in OFFER_REQUIRED:
            if o.get(k) in (None, ""):
                problems.add("data/offers.yaml", ln, f"{where} 缺必填字段 {k}")
        for k in ("places", "links"):
            v = o.get(k)
            if v is not None and not (isinstance(v, list) and all(isinstance(x, str) and x.strip() for x in v)):
                problems.add("data/offers.yaml", ln, f"{where} 的 {k} 要写成列表（每项一个{'地点 id' if k == 'places' else '网址'}）")
                o.pop(k)
        if isinstance(o.get("links"), list):
            good = [x.strip() for x in o["links"] if re.match(r"^https?://\S+$", x.strip())]
            if len(good) != len(o["links"]):
                problems.add("data/offers.yaml", ln, f"{where} 的 links 里有不是 http(s):// 开头的网址")
            o["links"] = good
        out.append(o)
    return out


def booking_status_base(status):
    """“待买（10-24 在买）”→“待买”：状态可以带括号备注，判断时只看括号前。"""
    return re.split(r"[（(]", str(status or ""), maxsplit=1)[0].strip()


def load_bookings(root, problems, places=None):
    """data/bookings.yaml（要买、要订、要联系的事）→ 列表。文件不存在是正常的，返回空列表、不报错。"""
    path = root / "data" / "bookings.yaml"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    F = "data/bookings.yaml"
    try:
        data = _load(text) or []
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        problems.add(F, mark.line + 1 if mark else 0, f"YAML 读不了：{getattr(e, 'problem', e)}")
        return []
    if not isinstance(data, list):
        problems.add(F, 1, "顶层要是列表（每件事一条“- id: …”）")
        return []
    starts = [text[:m.start()].count("\n") + 1 for m in re.finditer(r"^- id:", text, re.M)]
    out, seen = [], {}
    for i, b in enumerate(data):
        if not isinstance(b, dict):
            problems.add(F, 0, f"第 {i + 1} 条不是键值对")
            continue
        bid = str(b.get("id", ""))
        ln = starts[i] if len(starts) == len(data) else 0
        b["_line"] = ln
        where = f"买票预约 {bid or '（无 id）'}"
        if not bid or not ID.match(bid):
            problems.add(F, ln, f"{where} 的 id 只能用小写字母、数字、连字符")
            if not bid:
                continue
        if bid in seen:
            problems.add(F, ln, f"id “{bid}” 重复（第一次在第 {seen[bid]} 行）")
            continue
        seen[bid] = ln
        for k in BOOKING_REQUIRED:
            if b.get(k) in (None, ""):
                problems.add(F, ln, f"{where} 缺必填字段 {k}")
        for k in BOOKING_TEXT:
            _text_field(b, k, where, ln, problems, F)
        for k in ("day", "due"):
            v = b.get(k)
            if v is not None and not (isinstance(v, str) and MMDD.match(v)):
                problems.add(F, ln, f"{where} 的 {k} “{v}” 要写成带引号的 MM-DD，如 \"10-27\"")
                b.pop(k)
        if b.get("kind") is not None and b["kind"] not in BOOKING_KINDS:
            problems.add(F, ln, f"{where} 的 kind “{b['kind']}” 不是 {' | '.join(BOOKING_KINDS)}")
            b.pop("kind")
        if b.get("status") is not None:
            base = booking_status_base(b["status"])
            if base not in BOOKING_STATUS:
                problems.add(F, ln, f"{where} 的 status “{b['status']}” 括号前要是 {' | '.join(BOOKING_STATUS)}")
                b.pop("status")
            else:
                b["status_base"] = base
        if b.get("why_now") is not None and b["why_now"] not in BOOKING_WHY:
            problems.add(F, ln, f"{where} 的 why_now “{b['why_now']}” 不是 {' | '.join(BOOKING_WHY)}")
            b.pop("why_now")
        if b.get("status_base") in BOOKING_OPEN and not b.get("why_now"):
            problems.add(F, ln, f"{where} 还没办，要写 why_now（为什么现在要办）")
        w = b.get("where")
        if w is not None:
            ok = isinstance(w, list) and w and all(
                (isinstance(x, str) and x.strip()) or
                (isinstance(x, dict) and isinstance(x.get("t"), str) and re.match(r"^https?://\S+$", str(x.get("u", ""))))
                for x in w)
            if not ok:
                problems.add(F, ln, f"{where} 的 where 要写成列表：每项一句文字，或 {{t: 文字, u: https://…}}")
                b.pop("where")
        c = b.get("contact")
        if c is not None:
            if not (isinstance(c, dict) and isinstance(c.get("name"), str) and c["name"].strip()):
                problems.add(F, ln, f"{where} 的 contact 要是键值对，且有 name")
                b.pop("contact")
            else:
                for k in c:
                    if k not in BOOKING_CONTACT_KEYS:
                        problems.add(F, ln, f"{where} 的 contact 里 “{k}” 不认识（可用 {' | '.join(BOOKING_CONTACT_KEYS)}）")
                for k in ("tel", "email", "web", "lang"):
                    if c.get(k) is not None and not (isinstance(c[k], str) and c[k].strip()):
                        problems.add(F, ln, f"{where} 的 contact.{k} 要写成一句文字")
                        c.pop(k)
                if isinstance(c.get("web"), str) and not re.match(r"^https?://\S+$", c["web"]):
                    problems.add(F, ln, f"{where} 的 contact.web 要 http(s):// 开头")
                    c.pop("web")
        if b.get("kind") in ("订位", "联系") and not b.get("contact"):
            problems.add(F, ln, f"{where} 是 {b['kind']}，要写 contact（至少 name；查不到电话就写 tel: 没查到）")
        d = b.get("draft")
        if d is not None:
            if isinstance(d, str) and d.strip():
                b["draft"] = [{"lang": "", "text": d.strip()}]
            elif isinstance(d, list) and d and all(isinstance(x, dict) and isinstance(x.get("text"), str) and x["text"].strip() for x in d):
                b["draft"] = [{"lang": str(x.get("lang") or ""), "text": x["text"].strip("\n")} for x in d]
            else:
                problems.add(F, ln, f"{where} 的 draft 要写成文字，或列表（每项 lang、text）")
                b.pop("draft")
        if b.get("place") is not None and places is not None and b["place"] not in places:
            problems.add(F, ln, f"{where} 的 place “{b['place']}” 不在地点库里")
            b.pop("place")
        out.append(b)
    return out


def _mins(s):
    """"07:35" → 455；不合格式返回 None（小时 0—29，允许跨过午夜写 24:30）。"""
    m = re.match(r"^(\d{1,2}):(\d{2})$", str(s).strip())
    if not m or int(m.group(1)) > 29 or int(m.group(2)) > 59:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def hhmm(n):
    return f"{n // 60:02d}:{n % 60:02d}"


def _plan_block(b, where, rel, places, problems):
    """一个时间块 → 规范的 dict；写错的报出来：缺时刻、取值错的整块去掉，地点 id 不存在的只去掉那个 id。"""
    if not isinstance(b, dict):
        problems.add(rel, 1, f"{where} 要写成键值对（from、to、type 等）")
        return None
    f = _mins(b.get("from", ""))
    if f is None:
        problems.add(rel, 1, f"{where} 的 from 要写成 HH:MM（0—29 点），现在是 “{b.get('from')}”")
        return None
    t = None
    if b.get("to") is not None:
        t = _mins(b["to"])
        if t is None:
            problems.add(rel, 1, f"{where} 的 to 要写成 HH:MM（0—29 点），现在是 “{b['to']}”")
            return None
        if t <= f:
            problems.add(rel, 1, f"{where} 的 to {b['to']} 不晚于 from {b['from']}")
            return None
    typ = b.get("type")
    if typ not in PLAN_TYPES:
        problems.add(rel, 1, f"{where} 的 type “{typ}” 不是 {' | '.join(PLAN_TYPES)}")
        return None
    if t is None and typ != "硬节点":
        problems.add(rel, 1, f"{where} 缺 to（只有“硬节点”可以只写 from）")
        return None
    sure = b.get("sure")
    if sure is not None and sure not in PLAN_SURE:
        problems.add(rel, 1, f"{where} 的 sure “{sure}” 不是 {' | '.join(PLAN_SURE)}")
        sure = None
    elif sure is None:
        problems.add(rel, 1, f"{where} 缺 sure（{' | '.join(PLAN_SURE)}）；网站先显示成“未标”")
    out = {"from": hhmm(f), "to": hhmm(t) if t is not None else None, "f": f, "t": t, "type": typ, "sure": sure,
           "place": None, "options": [], "text": "", "mode": "", "src": "", "note": ""}
    for k in ("text", "mode", "src", "note"):
        v = b.get(k)
        if v is None:
            continue
        if isinstance(v, str) and v.strip():
            out[k] = " ".join(v.split())
        else:
            problems.add(rel, 1, f"{where} 的 {k} 要写成一句文字，现在是 “{v}”")
    pid = b.get("place")
    if pid is not None:
        if not isinstance(pid, str) or pid not in places:
            problems.add(rel, 1, f"{where} 的 place “{pid}” 不在地点库里")
        else:
            out["place"] = pid
    opts = b.get("options")
    if opts is not None:
        if not isinstance(opts, list):
            problems.add(rel, 1, f"{where} 的 options 要写成 id 列表")
        else:
            for o in opts:
                if isinstance(o, str) and o in places:
                    if o not in out["options"]:
                        out["options"].append(o)
                else:
                    problems.add(rel, 1, f"{where} 的 options 里 “{o}” 不在地点库里")
    if not (out["place"] or out["options"] or out["text"]):
        problems.add(rel, 1, f"{where} 要有 place、options 或 text 之一（写这个块是什么）")
        return None
    return out


def _plans(day, places, problems):
    """天页头的 plans → 规范的列表；写错的报出来并去掉那一块（整个 plans 不是列表就全当没写，页面回到“推荐的一套”）。"""
    rel, raw = day["file"], day["meta"].get("plans")
    if raw is None:
        return []
    who_ok = PLAN_WHO_BASE[:1] + people(places) + PLAN_WHO_BASE[1:]
    if not isinstance(raw, list) or not raw or not all(isinstance(x, dict) for x in raw):
        problems.add(rel, 1, "plans 要写成方案列表（每套有 name、tracks）；这天先按没有时间轴显示")
        return []
    plans = []
    for i, pl in enumerate(raw, 1):
        if len(plans) >= PLAN_MAX:
            problems.add(rel, 1, f"plans 最多 {PLAN_MAX} 套，第 {i} 套起不显示")
            break
        name = pl.get("name")
        if not isinstance(name, str) or not name.strip():
            problems.add(rel, 1, f"plans 第 {i} 套缺 name")
            continue
        note = pl.get("note")
        tracks = []
        if not isinstance(pl.get("tracks"), list) or not pl["tracks"]:
            problems.add(rel, 1, f"方案“{name}”缺 tracks（一条轨道一个人群）")
            continue
        for j, tr in enumerate(pl["tracks"], 1):
            if not isinstance(tr, dict) or tr.get("who") not in who_ok or not isinstance(tr.get("blocks"), list):
                problems.add(rel, 1, f"方案“{name}”第 {j} 条轨道要有 who（{' | '.join(who_ok)}）和 blocks 列表")
                continue
            blocks = []
            for k, b in enumerate(tr["blocks"], 1):
                nb = _plan_block(b, f"方案“{name}”{tr['who']}轨第 {k} 块", rel, places, problems)
                if nb:
                    blocks.append(nb)
            blocks.sort(key=lambda b: (b["f"], b["t"] if b["t"] is not None else b["f"]))
            end, last = -1, None
            for b in blocks:
                if b["f"] < end:
                    problems.add(rel, 1, f"方案“{name}”{tr['who']}轨：{b['from']} 的块和前一块（{last['from']}–{last['to'] or '点'}）重叠，同一轨道的块不能重叠")
                e = b["t"] if b["t"] is not None else b["f"]
                if e >= end:
                    end, last = e, b
            if blocks:
                tracks.append({"who": tr["who"], "blocks": blocks})
        if tracks:
            plans.append({"name": name.strip(), "note": " ".join(note.split()) if isinstance(note, str) else "", "tracks": tracks})
    return plans


def plan_places(plans, first_only=False):
    """时间轴里出现的地点 id（place 与 options），按出现顺序；first_only 只看第一套的 place。"""
    out = []
    for pl in plans[:1] if first_only else plans:
        for tr in pl["tracks"]:
            for b in tr["blocks"]:
                for pid in ([b["place"]] if b["place"] else []) + ([] if first_only else b["options"]):
                    if pid not in out:
                        out.append(pid)
    return out


def load_all(root):
    root = Path(root)
    problems = Problems()
    places = load_places(root, problems)
    offers = load_offers(root, problems, places)
    bookings = load_bookings(root, problems, places)
    data = {"root": root, "places": places, "images": load_images(root, problems, places), "offers": offers, "bookings": bookings,
            "days": load_days(root, problems), "todo": load_todo(root, problems),
            "decisions": load_decisions(root, problems)}
    offer_ids = {o["id"] for o in offers}
    for pid, p in places.items():
        if p.get("offers") and offers is not None:
            bad = [x for x in p["offers"] if x not in offer_ids]
            for x in bad:
                problems.add("data/places.yaml", p.get("_line", 0), f"{pid} 的 offers 里 “{x}” 不在 data/offers.yaml 里")
            p["offers"] = [x for x in p["offers"] if x in offer_ids]
    for o in offers:
        if o.get("places"):
            for x in o["places"]:
                if x not in places:
                    problems.add("data/offers.yaml", o["_line"], f"优惠 {o['id']} 的 places 里 “{x}” 不在地点库里")
            o["places"] = [x for x in o["places"] if x in places]
    for d in data["days"]:
        for ref, ln in d["refs"]:
            if ref not in places:
                problems.add(d["file"], ln, f"[[{ref}]] 不在地点库里")
        d["plans"] = _plans(d, places, problems)
        if d["plans"]:
            # 时间轴里的地点也算这天提到的：地图上标出、地区页的“排进哪一天”会带上；第一套的 place 算“推荐”
            for pid in plan_places(d["plans"]):
                d["refs"].append((pid, 0))
            for pid in plan_places(d["plans"], first_only=True):
                if pid not in d["recommended"]:
                    d["recommended"].append(pid)
    return data, problems


def weekday(d):
    return "周" + WEEK[d.weekday()]


# ===================== 查询：附近、搜索、按时间轴看一天 =====================
# 网站生成（build_site.py）与命令行共用这几个函数；以后做 MCP，把它们原样包成工具。

_DEFAULT = {}


def default_root():
    return Path(__file__).resolve().parent.parent


def _all(root=None):
    """默认读本仓的数据（读一次，缓存）；有问题照常返回，不抛异常。"""
    key = str(root or default_root())
    if key not in _DEFAULT:
        _DEFAULT[key] = load_all(Path(key))
    return _DEFAULT[key][0]


def dist_km(lat1, lon1, lat2, lon2):
    """两点的球面距离（公里）。"""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371.0088 * 2 * math.asin(math.sqrt(a))


def near(lat, lon, km=NEAR_KM, places=None, kinds=None, exclude=()):
    """库里坐标在 km 公里内的地点，按距离从近到远。

    返回 [{"id","name","kind","km","walk","place"}]；walk 表示 ≤2.5 km（步行可达）。
    默认不含住宿、交通；kinds（集合）给了就只要这些类别（这时住宿、交通也可以点名要）。
    places 不给就读本仓的地点库。
    """
    places = places if places is not None else _all()["places"]
    out = []
    for pid, p in places.items():
        c = p.get("coords")
        if not c or pid in exclude:
            continue
        k = p.get("kind")
        if kinds:
            if k not in kinds:
                continue
        elif k in SKIP_NEAR:
            continue
        d = dist_km(lat, lon, c[0], c[1])
        if d <= km:
            out.append({"id": pid, "name": p.get("name", pid), "kind": k, "km": round(d, 2), "walk": d <= WALK_KM, "place": p})
    out.sort(key=lambda x: (x["km"], x["id"]))
    return out


def _fold(s):
    """搜索用：小写、去重音（Schönbrunn ≈ schonbrunn）。"""
    return "".join(c for c in unicodedata.normalize("NFKD", str(s).casefold()) if not unicodedata.combining(c))


def find(term, places=None, fields=("name", "name_local", "reason", "notes")):
    """搜地点库：词用空格分开，每个词都要在 name、name_local、reason、notes（或 id）里出现。

    返回 [{"id","name","kind","where":[命中的字段],"place"}]，名字命中的排前面。
    """
    places = places if places is not None else _all()["places"]
    words = [_fold(w) for w in str(term).split() if w.strip()]
    if not words:
        return []
    out = []
    for pid, p in places.items():
        texts = {f: _fold(p.get(f) or "") for f in (*fields, "id")}
        texts["id"] = _fold(pid)
        if all(any(w in t for t in texts.values()) for w in words):
            hit = [f for f in (*fields, "id") if any(w in texts[f] for w in words)]
            out.append({"id": pid, "name": p.get("name", pid), "kind": p.get("kind"), "where": hit, "place": p})
    out.sort(key=lambda x: (not ("name" in x["where"] or "name_local" in x["where"]), x["id"]))
    return out


def source_label(p):
    """地点的“来源标签”（餐馆尤其用）：有资料的写“N 篇攻略提到”；按 Google 地图评分推荐的写评分并标明资料里没有。"""
    r = p.get("rating") if isinstance(p.get("rating"), dict) else None
    notes = p.get("heat") or len(p.get("sources") or [])
    rate = ""
    if r:
        rate = f"{r.get('source') or '评分'} {r['score']:g}"
        if r.get("reviews") is not None:
            rate += f"（{r['reviews']:,} 条）"
    if not p.get("sources") and r and "google" in (r.get("source") or "").lower():
        return f"{rate} · 推荐的就餐点（资料里没有）"
    if notes:
        return f"{notes} 篇攻略提到" + (f" · {rate}" if rate else "")
    if rate:
        return rate
    return "资料里没有评分"


def nearby_food(places, track_blocks, idx, km=EAT_KM, limit=3):
    """时间轴“吃”块（track_blocks[idx]）的“附近还有”：库里 kind 美食、离前后“玩”块地点 km 内、不在 options 里的，
    按距离从近到远，最多 limit 个。返回 [{"id","name","km","from_id","from_name","place"}]。"""
    b = track_blocks[idx]
    anchors = []
    for rng in (range(idx - 1, -1, -1), range(idx + 1, len(track_blocks))):
        for j in rng:
            o = track_blocks[j]
            if o["type"] == "玩" and o["place"] and places.get(o["place"], {}).get("coords"):
                anchors.append(o["place"])
                break
    skip = set(b["options"]) | set(anchors)
    best = {}
    for aid in anchors:
        c = places[aid]["coords"]
        for n in near(c[0], c[1], km, places, kinds={"美食"}, exclude=skip):
            if n["id"] not in best or n["km"] < best[n["id"]]["km"]:
                best[n["id"]] = {**n, "from_id": aid, "from_name": places[aid].get("name", aid)}
    return sorted(best.values(), key=lambda x: (x["km"], x["id"]))[:limit]


def day_by_date(data, mmdd):
    """“10-26” → 天页（找不到返回 None）。"""
    for d in data["days"]:
        if Path(d["file"]).stem == mmdd:
            return d
    return None


def _fmt_dur(m):
    return f"{m // 60} h {m % 60:02d}" if m >= 60 and m % 60 else (f"{m // 60} h" if m >= 60 else f"{m} 分")


def block_title(b, places):
    """时间块的一句话：地点名、吃饭的备选名或 text。"""
    if b["type"] == "吃" and b["options"]:
        names = "、".join(places[o]["name"] for o in b["options"])
        return (b["text"] + "：" if b["text"] else "吃：") + names
    if b["place"]:
        return places[b["place"]]["name"] + (f"（{b['text']}）" if b["text"] else "")
    return b["text"]


def day_text(data, d):
    """一天按时间轴打印成文字（命令行 day 用）；没有时间轴就打印推荐的一套。"""
    places = data["places"]
    m = d["meta"]
    lines = [f"{m['date']:%m-%d} {weekday(m['date'])}｜{m.get('title', '')}｜{m.get('area', '')}｜节奏 {m.get('pace', '')}" + (f"｜住 {m['base']}" if m.get("base") else "")]
    lead = d["sections"].get("一句话", {}).get("text")
    if lead:
        lines.append(lead)
    for a in m.get("anchors") or []:
        lines.append(f"  硬节点 {a['time']} {a['text']}")
    if not d["plans"]:
        lines.append("（这天没有时间轴 plans，下面是“推荐的一套”）")
        lines.append(d["sections"].get("推荐的一套", {}).get("text") or "（也没有推荐的一套）")
        return "\n".join(lines)
    for i, pl in enumerate(d["plans"], 1):
        lines.append("")
        lines.append(f"== 第 {i} 套：{pl['name']}" + (f" — {pl['note']}" if pl["note"] else ""))
        for tr in pl["tracks"]:
            tot = {}
            lines.append(f"  [{tr['who']}]")
            for k, b in enumerate(tr["blocks"]):
                span = f"{b['from']}–{b['to']}" if b["to"] else f"{b['from']}  "
                dur = f"（{_fmt_dur(b['t'] - b['f'])}）" if b["t"] is not None else ""
                head = f"    {span} {b['type']}·{b['sure'] or '未标'} {block_title(b, places)}"
                if b["mode"]:
                    head += f"｜{b['mode']}"
                lines.append(head + dur + (f"｜{b['note']}" if b["note"] else "") + (f"｜出处：{b['src']}" if b["src"] else ""))
                if b["t"] is not None:
                    tot[b["type"]] = tot.get(b["type"], 0) + b["t"] - b["f"]
                if b["type"] == "吃":
                    for o in b["options"]:
                        lines.append(f"        备选 {places[o]['name']} [{o}] — {source_label(places[o])}")
                    for n in nearby_food(places, tr["blocks"], k):
                        lines.append(f"        附近还有 {n['name']} [{n['id']}] 距{n['from_name']} {n['km']:.2f} km — {source_label(n['place'])}")
            if tot:
                lines.append("    占用：" + "，".join(f"{t} {_fmt_dur(tot[t])}" for t in PLAN_TYPES if t in tot))
    return "\n".join(lines)


def _center(arg, places):
    """地点 id 或 “纬度,经度” → (lat, lon, 排除的 id)；解析不了返回 None。"""
    if arg in places and places[arg].get("coords"):
        c = places[arg]["coords"]
        return c[0], c[1], {arg}
    m = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*[,，]\s*(-?\d+(?:\.\d+)?)\s*$", arg)
    if m:
        return float(m.group(1)), float(m.group(2)), set()
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="查行程数据：附近的地点、搜地点、按时间轴看一天（读本仓的 data/ 与 trip/）")
    ap.add_argument("--root", default=str(default_root()), help="行程仓根目录（默认本仓）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pn = sub.add_parser("near", help="某个地点或坐标附近的地点（默认 5 km 内，不含住宿、交通）")
    pn.add_argument("where", help="地点 id，或 纬度,经度")
    pn.add_argument("--km", type=float, default=NEAR_KM)
    pn.add_argument("--kind", action="append", help="只要这个类别（可重复），如 美食")
    pf = sub.add_parser("find", help="搜 name、name_local、reason、notes；多个词都要出现")
    pf.add_argument("words", nargs="+")
    pd = sub.add_parser("day", help="按时间轴打印一天；没有时间轴就打印推荐的一套")
    pd.add_argument("date", help="MM-DD，如 10-26")
    a = ap.parse_args(argv)
    data, problems = load_all(Path(a.root).resolve())
    places = data["places"]
    if a.cmd == "near":
        c = _center(a.where, places)
        if not c:
            print(f"错误：“{a.where}” 不是有坐标的地点 id，也不是 纬度,经度", file=sys.stderr)
            return 2
        res = near(c[0], c[1], a.km, places, set(a.kind) if a.kind else None, c[2])
        for r in res:
            walk = "步行可达" if r["walk"] else "要坐车"
            print(f"{r['km']:5.2f} km  {walk}  {r['kind'] or '':<4} {r['name']} [{r['id']}]  {source_label(r['place'])}")
        print(f"（{a.where} 周围 {a.km:g} km 内共 {len(res)} 个" + (f"，类别：{'、'.join(a.kind)}" if a.kind else "，不含住宿、交通") + "）")
        return 0
    if a.cmd == "find":
        res = find(" ".join(a.words), places)
        for r in res:
            print(f"{r['kind'] or '':<4} {r['name']} [{r['id']}]  命中：{'、'.join(r['where'])}")
        print(f"（共 {len(res)} 个）")
        return 0
    d = day_by_date(data, a.date)
    if not d:
        print(f"错误：没有 {a.date} 的天页（有：{'、'.join(Path(x['file']).stem for x in data['days'])}）", file=sys.stderr)
        return 2
    print(day_text(data, d))
    return 0


if __name__ == "__main__":
    sys.exit(main())
