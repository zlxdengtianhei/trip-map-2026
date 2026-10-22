"""生成网站：从 trip/ 与 data/ 生成手机优先的静态网站到 _site/，末尾做隐私扫描。

  python3 tools/build_site.py                    # 输出到 _site/
  python3 tools/build_site.py --root DIR --out DIR

页面：首页（今天与明天、要提前做的、待你定、每天入口）、每天一页（头里有 plans: 就画时间轴；天页地图可标出 5 km 内
库里其他地点）、三个地区页、“选地方”页（pick.html：每个地方标想去、不去或不标，想去的选想哪天，复制成一段文字给 agent）、
“票价与优惠”页（offers.html，读 data/offers.yaml）、决定页、图片来源页、code/（工具代码与 MIT 许可）。
名单里带“=>”的词先替换（如名字显示成“本人”），邮箱打码；扫描仍发现名单词、邮箱、订单号样式就以 1 退出，不能发布。
数据不合格式时照常生成能生成的部分；格式问题用 check.py 查。依赖：jinja2、pyyaml、markdown（见 requirements.txt）。
"""
import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import markdown
from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup, escape

sys.path.insert(0, str(Path(__file__).resolve().parent))
import privacy  # noqa: E402
import tripdata  # noqa: E402

TOOLS = Path(__file__).resolve().parent
TZ = ZoneInfo("Europe/Vienna")
AREA_SLUG = {"布达佩斯": "area-budapest", "维也纳": "area-vienna", "湖区": "area-lakes"}
COLOR_REC, COLOR_OTHER = "#1d1d1f", "#2f6fdb"
PRI_ORDER = {p: i for i, p in enumerate(tripdata.PRIORITIES)}
# “选地方”页：不列交通、住宿；类别筛选按这个顺序（只显示有地点的类别）
PICK_SKIP = {"交通", "住宿"}
PICK_KINDS = ["徒步", "景点", "小镇", "美食", "抱石", "观景", "购物", "其他"]
# “选地方”页每个地区可选的日期。来源：主会话 10-21 的任务包（agents/pick_page_20261021-2158/PACKET.md）。
# 对照天页 YAML 头：10-25、10-26 在布达佩斯；10-27 白天维也纳、晚上住 Bad Ischl；10-28、10-29 湖区；
# 10-30 白天 Bad Ischl、晚上回维也纳；10-31 天页是维也纳（湖区 10-31 留着：决定 W-04 若选 A，本人 10-31 上午还在 Bad Ischl）；11-01 只有回程，不列。
PICK_DATES = {"布达佩斯": ["2026-10-25", "2026-10-26"],
              "维也纳": ["2026-10-27", "2026-10-30", "2026-10-31"],
              "湖区": ["2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30", "2026-10-31"]}


# 时间轴：每分钟占多少像素；块的类型 → 样式名；把握 → 样式名与说明
TL_PX = 1.1
TL_COL = 160       # 每列最窄（手机上多列时靠横向滑动）
TL_COL_MAX = 340   # 每列最宽（桌面上列少时不拉成一大片）
TL_TYPE = {"玩": "play", "路上": "move", "吃": "eat", "机动": "flex", "硬节点": "hard", "预留": "hold"}
TL_SURE = {"定": ("fix", "定：时刻表上有的，或已订的"), "估": ("est", "估：估的停留或步行时间"),
           "不定": ("unk", "不定：还不知道，先留着位置")}
OFFER_VERDICT_CLS = {"推荐": "v-rec", "看情况": "v-maybe", "不划算": "v-no", "已有": "v-have"}
OFFER_VERDICT_ORDER = {v: i for i, v in enumerate(["推荐", "看情况", "已有", "不划算"])}


def pickable(p):
    """“选地方”页列不列它：交通、住宿不列；`pick: false` 的（如会场这类硬节点）也不列。"""
    return p.get("kind") not in PICK_SKIP and p.get("pick") is not False


def _n(x):
    """9.0 → "9"，9.8 → "9.8"，2300 → "2,300"。"""
    return f"{x:,.0f}" if float(x).is_integer() else f"{x:,}"


def trail_view(t):
    """地点 trail 小节 → 卡片要显示的几行（模板里用 trail(p.trail)）。字段已在 tripdata 里校验，写错的已去掉。

    line：要点一行“中等 · 9.8 km · 爬升 420 m · 3–3.5 h · 环线”；rest：难度以外的部分（难度单独上色）；
    rating：“AllTrails 4.8（2,300 条评价）”，来源名取 source 括号前的部分，括号里的（如“10-22 查”）放 when；
    link_label：去哪个网站看图与评价（url 是 alltrails.com 就写 AllTrails，否则写来源名）。
    """
    if not isinstance(t, dict) or not t:
        return None
    parts = [f"{_n(t['length_km'])} km" if t.get("length_km") is not None else None]
    parts.append(f"爬升 {_n(t['ascent_m'])} m" if t.get("ascent_m") is not None else None)
    parts += [t.get("time"), t.get("type")]
    source = (t.get("source") or "").strip()
    m = re.match(r"^(.*?)\s*[（(](.*)[）)]\s*$", source)
    name, when = (m.group(1).strip(), m.group(2).strip()) if m and m.group(1).strip() else (source, "")
    url = t.get("url") or ""
    site = "AllTrails" if "alltrails.com" in url.lower() else (name or "来源网站")
    rating = None
    if t.get("rating") is not None:
        rating = f"{name or '评分'} {_n(t['rating'])}"
        if t.get("reviews") is not None:
            rating += f"（{_n(t['reviews'])} 条评价）"
    rest = " · ".join(str(x) for x in parts if x not in (None, ""))
    return {"line": " · ".join(x for x in [t.get("difficulty"), rest] if x), "difficulty": t.get("difficulty"), "rest": rest,
            "rating": rating, "when": when, "source": source, "scenery": t.get("scenery"), "autumn": t.get("autumn"),
            "url": url, "link_label": f"去 {site} 看图与评价"}


def price_view(p):
    """地点的 price 小节 → 卡片要显示的内容。line：“成人 €22 ｜ 学生 €17”（没有学生价就只写成人）；rows：收进“详情”的几行。"""
    t = p.get("price") if isinstance(p, dict) else None
    if not isinstance(t, dict) or not t.get("adult"):
        return None
    line = f"成人 {t['adult']}"
    stu = (t.get("student") or "").strip()
    if stu and "无学生价" not in stu:
        line += f" ｜ 学生 {stu}"
    rows = []
    if t.get("youth"):
        rows.append(("青年票", t["youth"]))
    if t.get("combo"):
        rows.append(("联票", t["combo"]))
    if stu and "无学生价" in stu:
        rows.append(("学生票", stu))
    if t.get("note"):
        rows.append(("说明", t["note"]))
    src = "；".join(x for x in [t.get("source"), f"核对 {t['checked']}" if t.get("checked") else None] if x)
    if src:
        rows.append(("票价出处", src))
    return {"line": line, "rows": rows}


def rating_view(p):
    """地点的 rating 小节 → “Google 地图 4.6（2,300 条）”；rec 表示这是没有资料、按评分推荐的就餐点。"""
    r = p.get("rating") if isinstance(p, dict) else None
    if not isinstance(r, dict) or r.get("score") is None:
        return None
    text = f"{r.get('source') or '评分'} {_n(r['score'])}"
    if r.get("reviews") is not None:
        text += f"（{_n(r['reviews'])} 条）"
    google = "google" in (r.get("source") or "").lower()
    return {"text": text, "checked": r.get("checked") or "", "rec": bool(google and not p.get("sources"))}


def _dur(m):
    return f"{m // 60} h {m % 60:02d}" if m >= 60 and m % 60 else (f"{m // 60} h" if m >= 60 else f"{m} 分")


class Site:
    def __init__(self, root, out):
        self.root, self.out = Path(root), Path(out)
        self.data, self.problems = tripdata.load_all(self.root)
        self.places, self.images = self.data["places"], self.data["images"]
        # 住处是私人位置：公开站不放坐标和当地名（地址在私有仓里，agent 照样能用）
        for p in self.places.values():
            if p.get("kind") == "住宿":
                p.pop("coords", None)
                p.pop("name_local", None)
        self.policy = privacy.load(self.root / "config" / "privacy.txt")
        self.env = Environment(loader=FileSystemLoader(str(self.root / "site" / "templates")), autoescape=True,
                               trim_blocks=False)
        self.env.globals.update(trail=trail_view, price=price_view, rating=rating_view, gallery=self.gallery,
                                source_label=tripdata.source_label)
        self.offers = self.data["offers"]
        self.offers_by_id = {o["id"]: o for o in self.offers}
        self.cards = {}  # 天页 slug → 有卡片的地点 id

    def gallery(self, pid, name):
        """点图放大用：这个地点的图（主图加 more 里的图）→ JSON 文字 [{"src","cap"}]；没有存好的图返回空列表。"""
        img = self.images.get(pid)
        if not isinstance(img, dict) or not img.get("file"):
            return "[]"
        out = []
        for m in [img] + [x for x in img.get("more") or [] if isinstance(x, dict)]:
            if m.get("file"):
                cap = " · ".join(x for x in [name, f"图：{m['author']}" if m.get("author") else "", m.get("license") or ""] if x)
                out.append({"src": m["file"], "cap": cap})
        return json.dumps(out, ensure_ascii=False)

    # ---- 文字 ----
    def link(self, pid, here=None):
        """[[id]] 的去处：天页 here 上有这张卡片就跳那张卡片（首页引用同一段也能用），否则跳地区页。"""
        p = self.places.get(pid)
        if not p:
            return None
        for slug in ([here] if here else []):
            if pid in self.cards.get(slug, ()):
                return f"{slug}.html#p-{pid}"
        return f"{AREA_SLUG.get(p.get('area'), 'index')}.html#p-{pid}"

    def md(self, text, here=None, inline=False):
        def sub(m):
            pid = m.group(1).strip()
            p, href = self.places.get(pid), self.link(pid, here)
            if not p:
                return f'<span class="muted">{escape(pid)}</span>'
            return f'<a href="{escape(href)}">{escape(p["name"])}</a>'

        html = markdown.markdown(tripdata.REF.sub(sub, text or ""), extensions=["tables", "sane_lists"])
        if inline:
            html = re.sub(r"^<p>(.*)</p>$", r"\1", html.strip(), flags=re.S)
        return Markup(html)

    # ---- 数据整理 ----
    def prepare_days(self):
        days = [d for d in self.data["days"] if d["meta"].get("date")]
        for d in days:
            dt = d["meta"]["date"]
            d.update(date_iso=dt.isoformat(), short=f"{dt.month}/{dt.day}",
                     date_label=f"{dt.month} 月 {dt.day} 日 {tripdata.weekday(dt)}")
            for a in d["meta"]["anchors"]:
                h, mi = map(int, a["time"].split(":"))
                a["iso"] = datetime(dt.year, dt.month, dt.day, h % 24, mi, tzinfo=TZ).isoformat()
            self.cards[d["slug"]] = {c["ref"] for s in d["slots"] for c in s["items"] if c["ref"] in self.places}
        return days

    def render_day_text(self, d, here):
        html = {name: self.md(sec["text"], here) for name, sec in d["sections"].items() if name != "一句话"}
        lead = self.md(d["sections"].get("一句话", {}).get("text", ""), here, inline=True)
        return html, lead

    def map_json(self, points, near=None):
        return Markup(json.dumps({"points": points, "near": near or []}, ensure_ascii=False).replace("</", "<\\/"))

    def day_near(self, d, pts):
        """天页地图：库里离当天任一地点 5 km 内的其他地点（不含住宿、交通），灰点用。距离在这里算好。"""
        mine = [pid for pid in dict.fromkeys(r for r, _ in d["refs"]) if self.places.get(pid, {}).get("coords")]
        best = {}
        for pid in mine:
            c = self.places[pid]["coords"]
            for n in tripdata.near(c[0], c[1], tripdata.NEAR_KM, self.places, exclude=set(mine)):
                if n["id"] not in best or n["km"] < best[n["id"]]["km"]:
                    best[n["id"]] = {**n, "from": self.places[pid]["name"]}
        out = []
        for n in sorted(best.values(), key=lambda x: (x["km"], x["id"])):
            p = n["place"]
            out.append({"lat": round(p["coords"][0], 6), "lon": round(p["coords"][1], 6), "name": p["name"], "kind": p.get("kind") or "",
                        "km": round(n["km"], 1), "walk": n["walk"], "from": n["from"], "href": self.link(n["id"])})
        return out

    def day_map(self, d):
        pts, seen = [], set()
        for pid, _ in d["refs"]:
            p = self.places.get(pid)
            if not p or not p.get("coords") or pid in seen:
                continue
            seen.add(pid)
            rec = pid in d["recommended"]
            pts.append({"lat": round(p["coords"][0], 6), "lon": round(p["coords"][1], 6), "name": p["name"],
                        "note": "推荐的一套" if rec else "", "color": COLOR_REC if rec else COLOR_OTHER,
                        "href": f"#p-{pid}" if pid in self.cards[d["slug"]] else self.link(pid)})
        return pts


    def timeline(self, d):
        """天页的 plans → 画时间轴要用的数据（位置按分钟算好）。没有 plans 返回 None，天页回到“推荐的一套”。

        每套方案每条轨道一列；块的 top/height 是像素。“吃”块带备选 options 与自动的“附近还有”（库里美食、离前后“玩”块 1.5 km 内，最多 3 个）。
        """
        plans = d.get("plans")
        if not plans:
            return None
        places = self.places
        allb = [b for pl in plans for tr in pl["tracks"] for b in tr["blocks"]]
        lo = min(b["f"] for b in allb) // 60 * 60
        hi = -(-max((b["t"] if b["t"] is not None else b["f"]) for b in allb) // 60) * 60
        if hi - lo < 180:
            hi = lo + 180
        height = round((hi - lo) * TL_PX)
        ruler = [{"label": f"{m // 60 % 24:02d}:00", "top": round((m - lo) * TL_PX)} for m in range(lo, hi + 1, 60)]
        slug, ncols = d["slug"], 0
        out = []
        for i, pl in enumerate(plans):
            tracks = []
            for j, tr in enumerate(pl["tracks"]):
                blocks, tot = [], {}
                for k, b in enumerate(tr["blocks"]):
                    point = b["t"] is None
                    top = round((b["f"] - lo) * TL_PX)
                    h = 24 if point else max(round((b["t"] - b["f"]) * TL_PX), 20)
                    if point:
                        top = max(top - h, 0)  # 只有时刻的节点：旗子画在这个时刻的上方，线在时刻处
                    if not point:
                        tot[b["type"]] = tot.get(b["type"], 0) + b["t"] - b["f"]
                    sure_cls, sure_txt = TL_SURE.get(b["sure"], ("none", "未标把握"))
                    v = {"id": f"tl-{i}-{j}-{k}", "top": top, "h": h, "point": point, "type": b["type"], "tcls": TL_TYPE[b["type"]],
                         "scls": sure_cls, "sure": b["sure"] or "未标", "sure_txt": sure_txt, "from": b["from"], "to": b["to"],
                         "time": f"{b['from']}–{b['to']}" if b["to"] else f"{b['from']} 起",
                         "dur": _dur(b["t"] - b["f"]) if not point else "", "title": tripdata.block_title(b, places),
                         "mode": b["mode"], "src": b["src"], "note": b["note"], "who": tr["who"], "plan": pl["name"],
                         "place": None, "options": [], "nearby": []}
                    if b["place"]:
                        v["place"] = {"name": places[b["place"]]["name"], "href": self.link(b["place"], slug)}
                    for o in b["options"]:
                        v["options"].append({"name": places[o]["name"], "href": self.link(o, slug), "label": tripdata.source_label(places[o])})
                    if b["type"] == "吃":
                        for n in tripdata.nearby_food(places, tr["blocks"], k):
                            v["nearby"].append({"name": n["name"], "href": self.link(n["id"], slug), "km": f"{n['km']:.1f}",
                                                "from": n["from_name"], "label": tripdata.source_label(n["place"])})
                    blocks.append(v)
                summary = [{"type": t, "cls": TL_TYPE[t], "dur": _dur(tot[t])} for t in tripdata.PLAN_TYPES if t in tot]
                tracks.append({"who": tr["who"], "blocks": blocks, "first": j == 0, "summary": summary})
                ncols += 1
            out.append({"name": pl["name"], "note": pl["note"], "tracks": tracks})
        return {"plans": out, "ruler": ruler, "height": height, "ncols": ncols, "min_width": 46 + ncols * TL_COL, "col": TL_COL, "col_max": TL_COL_MAX, "hpx": round(60 * TL_PX, 2),
                "types": list(TL_TYPE.items()), "sure_legend": [(k, TL_SURE[k][0], TL_SURE[k][1]) for k in tripdata.PLAN_SURE]}

    def plan_brief(self, d):
        """首页用：第一套方案每条轨道的“时刻 地点”清单（玩、吃、硬节点；路上和预留不列）。"""
        if not d.get("plans"):
            return []
        out = []
        for tr in d["plans"][0]["tracks"]:
            items = [f"{b['from']} {tripdata.block_title(b, self.places)}" for b in tr["blocks"] if b["type"] in ("玩", "吃", "硬节点")]
            out.append({"who": tr["who"], "items": items})
        return out

    def offers_groups(self):
        """优惠页：按 scope 分组（全程、各地区、城际交通，其余的排后面），组内推荐的在前。"""
        groups = {}
        for o in self.offers:
            groups.setdefault(o.get("scope") or "其他", []).append(o)
        order = tripdata.OFFER_SCOPES + [k for k in groups if k not in tripdata.OFFER_SCOPES]
        out = []
        for sc in order:
            if sc in groups:
                items = sorted(groups[sc], key=lambda o: (OFFER_VERDICT_ORDER.get(o.get("verdict"), 9), o["name"] if isinstance(o.get("name"), str) else ""))
                for o in items:
                    o["vcls"] = OFFER_VERDICT_CLS.get(o.get("verdict"), "v-have")
                    o["place_links"] = [{"name": self.places[x]["name"], "href": self.link(x)} for x in o.get("places") or [] if x in self.places]
                out.append({"scope": sc, "slug": "offers-" + str(order.index(sc)), "items": items})
        return out

    def build_offers(self):
        n_rec = sum(1 for o in self.offers if o.get("verdict") == "推荐")
        self.page("offers.html", "offers.html", title="票价与优惠", page_id="offers", groups=self.offers_groups(), n_rec=n_rec)

    def usage(self, days):
        used = {}
        for d in days:
            for pid in dict.fromkeys(r for r, _ in d["refs"]):
                used.setdefault(pid, []).append({"slug": d["slug"], "short": d["short"], "rec": pid in d["recommended"],
                                                 "card": pid in self.cards[d["slug"]]})
        return used

    # ---- 输出 ----
    def write(self, rel, html):
        path = self.out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(privacy.redact(html, self.policy), encoding="utf-8")

    def page(self, tpl, rel, **ctx):
        base = {"generated": datetime.now(TZ).strftime("%m-%d %H:%M"), "nav_days": self.days,
                "nav_areas": self.nav_areas, "places": self.places, "images": self.images, "maps": False,
                "offers": self.offers, "offers_by_id": self.offers_by_id,
                "up": "../" * rel.count("/")}
        self.write(rel, self.env.get_template(tpl).render(**{**base, **ctx}))

    def build(self):
        if self.out.exists():
            shutil.rmtree(self.out)
        self.out.mkdir(parents=True)
        self.days = days = self.prepare_days()
        self.nav_areas = [{"name": n, "slug": s} for n, s in AREA_SLUG.items()]
        for i, d in enumerate(days):
            d["html"], d["lead"] = self.render_day_text(d, d["slug"])
            d["extra"] = [n for n in d["order"] if n not in tripdata.SECTIONS]
            seen = set()
            for s in d["slots"]:
                for c in s["items"]:
                    c["reason_html"] = self.md(c["reason"], d["slug"], inline=True)
                    c["anchor"] = c["ref"] in self.places and c["ref"] not in seen
                    seen.add(c["ref"])
            d["map"] = {"points": self.day_map(d)}
            d["near"] = self.day_near(d, d["map"]["points"]) if d["map"]["points"] else []
            d["map_json"] = self.map_json(d["map"]["points"], d["near"])
            d["tl"] = self.timeline(d)
            d["brief"] = self.plan_brief(d)
            self.page("day.html", f"{d['slug']}.html", title=d["meta"].get("title") or d["short"], page_id=d["slug"],
                      day=d, maps=bool(d["map"]["points"]), prev=days[i - 1] if i else None,
                      next=days[i + 1] if i + 1 < len(days) else None)
        todo_open = [t for t in self.data["todo"] if t.get("状态") in ("待办", "进行中")]
        pick_n = sum(1 for p in self.places.values() if p.get("area") in AREA_SLUG and pickable(p))
        self.page("index.html", "index.html", title="首页", page_id="index", days=days, todo_open=todo_open,
                  decisions=self.data["decisions"], pick_n=pick_n,
                  n_rec=sum(1 for o in self.offers if o.get("verdict") == "推荐"))
        used = self.usage(days)
        for name, slug in AREA_SLUG.items():
            items = [{**p, "used": used.get(pid, [])} for pid, p in self.places.items() if p.get("area") == name]
            items.sort(key=lambda p: (not any(u["rec"] for u in p["used"]), PRI_ORDER.get(p.get("priority"), 9), p["name"]))
            groups = {}
            for p in items:
                groups.setdefault(p.get("kind") or "其他", []).append(p)
            pts = [{"lat": round(p["coords"][0], 6), "lon": round(p["coords"][1], 6), "name": p["name"],
                    "note": p.get("priority", ""), "href": f"#p-{p['id']}",
                    "color": COLOR_REC if any(u["rec"] for u in p["used"]) else COLOR_OTHER}
                   for p in items if p.get("coords")]
            area = {"name": name, "slug": slug, "places": items, "map": {"points": pts}, "map_json": self.map_json(pts),
                    "groups": sorted(groups.items(), key=lambda kv: -len(kv[1]))}
            self.page("area.html", f"{slug}.html", title=name, page_id=slug, area=area, maps=bool(pts))
        self.build_pick()
        self.build_offers()
        self.page("decisions.html", "decisions.html", title="决定", page_id="decisions", decisions=self.data["decisions"])
        credits = []
        for pid, img in sorted(self.images.items()):
            if not isinstance(img, dict):
                continue
            name = self.places.get(pid, {}).get("name", pid)
            credits.append({"name": name, **img})
            for i, m in enumerate(img.get("more") or [], 2):
                credits.append({"name": f"{name}（第 {i} 张）", **m})
        self.page("credits.html", "credits.html", title="图片来源", page_id="credits", credits=credits)
        self.copy_static()
        self.old_links()
        (self.out / ".nojekyll").write_text("")

    def build_pick(self):
        """“选地方”页：可去的地点按地区分组，组内按 heat 从高到低、再按 priority；勾选在浏览器里做（site.js）。"""
        titles = {d["date_iso"]: d["meta"].get("title", "") for d in self.days}
        groups, kinds = [], {}
        for name, slug in AREA_SLUG.items():
            items = [p for p in self.places.values() if p.get("area") == name and pickable(p)]
            items.sort(key=lambda p: (-(p.get("heat") or 0), PRI_ORDER.get(p.get("priority"), 9), p["name"]))
            if not items:
                continue
            for p in items:
                kinds[p.get("kind") or "其他"] = kinds.get(p.get("kind") or "其他", 0) + 1
            dates = []
            for iso in PICK_DATES.get(name, []):
                dt = datetime.fromisoformat(iso).date()
                dates.append({"mmdd": f"{dt:%m-%d}", "label": f"{dt:%m-%d} {tripdata.weekday(dt)}", "title": titles.get(iso, "")})
            groups.append({"name": name, "slug": slug.replace("area-", "pick-"), "places": items, "dates": dates})
        kind_list = [(k, kinds[k]) for k in PICK_KINDS if k in kinds] + [(k, n) for k, n in kinds.items() if k not in PICK_KINDS]
        self.page("pick.html", "pick.html", title="选地方", page_id="pick", groups=groups, kinds=kind_list,
                  total=sum(len(g["places"]) for g in groups))

    def old_links(self):
        # 旧站（10-19 前）的页面名改成跳转页，存过的旧链接仍能打开
        old = {f"day-{d}.html": f"day-10-{d}.html" for d in range(25, 32)}
        old.update({"today.html": "index.html", "decide.html": "decisions.html", "map.html": "index.html",
                    "places.html": "index.html", "budget.html": "index.html", "notes.html": "index.html",
                    "updates.html": "index.html"})
        for src, dst in old.items():
            if not (self.out / src).exists():
                (self.out / src).write_text(
                    f'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0; url={dst}">'
                    f'<link rel="canonical" href="{dst}"><a href="{dst}">{dst}</a>\n')

    def copy_static(self):
        shutil.copytree(self.root / "site" / "assets", self.out / "assets")
        for img in self.images.values():
            if not isinstance(img, dict):
                continue
            for m in [img] + [x for x in img.get("more") or [] if isinstance(x, dict)]:
                if m.get("file") and (self.root / m["file"]).exists():
                    dst = self.out / m["file"]
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(self.root / m["file"], dst)
        code = self.out / "code"
        code.mkdir()
        files = sorted(p.name for p in TOOLS.glob("*.py"))
        for f in files:
            shutil.copy2(TOOLS / f, code / f)
        shutil.copy2(TOOLS / "LICENSE", code / "LICENSE")
        self.page("code.html", "code/index.html", title="工具代码", page_id="code", files=files + ["LICENSE"])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("\n", 1)[1])
    ap.add_argument("--root", default=str(TOOLS.parent), help="行程仓根目录（默认本仓）")
    ap.add_argument("--out", help="输出目录（默认 <root>/_site）")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    out = Path(a.out).resolve() if a.out else root / "_site"
    try:
        site = Site(root, out)
    except FileNotFoundError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 2
    site.build()
    n = sum(1 for _ in out.rglob("*.html"))
    print(f"生成 {out}：{n} 个页面，{len(site.days)} 天，{len(site.places)} 个地点，{sum(1 for i in site.images.values() if isinstance(i, dict) and i.get('file'))} 张图")
    if site.problems:
        print(f"数据有 {len(site.problems)} 处不合格式（照常生成；跑 tools/check.py 看详情）")
    # 整份文件读不进来（YAML 错、重复键）时，生成的网站会缺一整块内容，不能拿去替换线上旧站
    unreadable = [(f, ln, msg) for f, ln, msg in site.problems if "读不了" in msg]
    if unreadable or not site.places:
        for f, ln, msg in unreadable:
            print(f"错误：{f}:{ln}：{msg}", file=sys.stderr)
        print("错误：有文件整份读不进来（或地点库为空），网站缺内容，不能发布；修好后重跑", file=sys.stderr)
        return 1
    hits = privacy.scan_dir(out, site.policy)
    for rel, ln, what, frag in hits:
        print(f"隐私：{out.name}/{rel}:{ln}：{what}（{frag}）")
    if hits:
        print(f"隐私扫描：{len(hits)} 处命中，不能发布")
        return 1
    print("隐私扫描：没有命中")
    return 0


if __name__ == "__main__":
    sys.exit(main())
