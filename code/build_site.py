"""生成网站：从 trip/ 与 data/ 生成手机优先的静态网站到 _site/，末尾做隐私扫描。

  python3 tools/build_site.py                    # 输出到 _site/
  python3 tools/build_site.py --root DIR --out DIR

页面：首页（今天与明天、要提前做的、待你定、每天入口）、每天一页、三个地区页、决定页、图片来源页、code/（工具代码与 MIT 许可）。
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
        self.cards = {}  # 天页 slug → 有卡片的地点 id

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

    def map_json(self, points):
        return Markup(json.dumps({"points": points}, ensure_ascii=False).replace("</", "<\\/"))

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
            d["map_json"] = self.map_json(d["map"]["points"])
            self.page("day.html", f"{d['slug']}.html", title=d["meta"].get("title") or d["short"], page_id=d["slug"],
                      day=d, maps=bool(d["map"]["points"]), prev=days[i - 1] if i else None,
                      next=days[i + 1] if i + 1 < len(days) else None)
        todo_open = [t for t in self.data["todo"] if t.get("状态") in ("待办", "进行中")]
        self.page("index.html", "index.html", title="首页", page_id="index", days=days, todo_open=todo_open,
                  decisions=self.data["decisions"])
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
        self.page("decisions.html", "decisions.html", title="决定", page_id="decisions", decisions=self.data["decisions"])
        credits = [{"name": self.places.get(pid, {}).get("name", pid), **img} for pid, img in sorted(self.images.items())
                   if isinstance(img, dict)]
        self.page("credits.html", "credits.html", title="图片来源", page_id="credits", credits=credits)
        self.copy_static()
        self.old_links()
        (self.out / ".nojekyll").write_text("")

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
            if isinstance(img, dict) and img.get("file") and (self.root / img["file"]).exists():
                dst = self.out / img["file"]
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self.root / img["file"], dst)
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
