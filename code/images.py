"""给地点找图：列候选、看图后选定、记许可证。图来自维基百科条目配图与 Wikimedia Commons。

  python3 tools/images.py find katrin                 # 列候选，缩略图下载到 data/cache/images/katrin/，先看图
  python3 tools/images.py pick katrin "File:BadIschlKatrinSiriuskoglTraun.JPG"   # 960px 存 images/katrin.jpg，写 data/images.yaml
  python3 tools/images.py add-url katrin https://…    # 记用户给的外链（许可证未知，网站只放“查看图片”链接）

find 只列候选，不替你判断：坐标搜索常混进无关的照片（选型实测：缆车站旁的胡蜂照片），pick 之前必须看图。
退出码：0 成功；1 没有候选；2 输入或网络错误。
"""
import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PLACES, IMAGES_YAML = ROOT / "data" / "places.yaml", ROOT / "data" / "images.yaml"
CACHE, OUT = ROOT / "data" / "cache" / "images", ROOT / "images"
# Wikimedia 要求 User-Agent 带联系方式，否则很快 429（选型实测第三个请求就被拒）；请求间隔 ≥0.5 s。
UA = "trip-2026-tools/1.0 (+https://github.com/zlxdengtianhei/trip-map-2026)"
GAP_S = 0.5
# Wikimedia 只缓存标准缩略图宽度（…500、960、1280…），别的宽度会被限流。
PREVIEW_W, PICK_W = 500, 960
NOT_PHOTO = re.compile(r"(?i)(\.(svg|pdf|tiff?|gif|ogv|webm|djvu)$|karte|\bmap\b|_map|map_|locator|lageplan|wappen|"
                       r"coat.of.arms|flag|logo|\bplan\b|badge|diagram|schild|sign\b)")
ARTICLE_MAX_KM = 10
_last = [0.0]


class ImageError(Exception):
    pass


def _get(url, raw=False):
    for attempt in range(4):
        wait = GAP_S - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as r:
                body = r.read()
            _last[0] = time.time()
            return body if raw else json.loads(body)
        except urllib.error.HTTPError as e:
            _last[0] = time.time()
            if e.code == 429 and attempt < 3:
                ra = e.headers.get("Retry-After")
                time.sleep(int(ra) if ra and ra.isdigit() else 5 * (attempt + 1))
                continue
            raise ImageError(f"HTTP {e.code}：{url}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            host = urllib.parse.urlsplit(url).hostname
            raise ImageError(f"连不上 {host}（{getattr(e, 'reason', e)}）。在云端会话里，多半是环境网络没放行 Wikimedia："
                             "到 claude.ai/code 的环境设置把网络改成 Full，或 Custom 加上 *.wikipedia.org、commons.wikimedia.org、thumb.wikimedia.org、"
                             "upload.wikimedia.org。没联网时可以先用 add-url 记用户给的链接")


def _api(host, **q):
    return _get(f"https://{host}/w/api.php?" + urllib.parse.urlencode({"action": "query", "format": "json", **q}))


def _km(a, b):
    la1, lo1, la2, lo2 = map(radians, (a[0], a[1], b[0], b[1]))
    h = sin((la2 - la1) / 2) ** 2 + cos(la1) * cos(la2) * sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * asin(sqrt(h))


def place(pid):
    if not PLACES.exists():
        raise ImageError(f"没有 {PLACES.relative_to(ROOT)}")
    for p in yaml.safe_load(PLACES.read_text(encoding="utf-8")) or []:
        if isinstance(p, dict) and p.get("id") == pid:
            return p
    raise ImageError(f"地点库里没有 id = {pid}")


def _strip(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()


def file_info(titles, width):
    """Commons 文件信息：{title: {thumb, page, license, license_url, author, mime, w, h}}；不在 Commons 的文件不返回。"""
    out = {}
    for i in range(0, len(titles), 40):
        js = _api("commons.wikimedia.org", prop="imageinfo", iiprop="url|extmetadata|mime|size", iiurlwidth=width,
                  titles="|".join(titles[i:i + 40]))
        for pg in js.get("query", {}).get("pages", {}).values():
            ii = (pg.get("imageinfo") or [None])[0]
            if not ii:
                continue
            em = ii.get("extmetadata", {})
            val = lambda k: _strip((em.get(k) or {}).get("value"))
            out[pg["title"]] = {"thumb": ii.get("thumburl") or ii.get("url"), "page": ii.get("descriptionurl"),
                                "license": val("LicenseShortName") or "未知", "license_url": val("LicenseUrl"),
                                "author": val("Artist") or val("Credit") or "未知", "mime": ii.get("mime"),
                                "w": ii.get("width"), "h": ii.get("height"), "date": val("DateTimeOriginal")[:10]}
    return out


def candidates(p, radius_m=500, n=6, queries=None):
    """按条目配图 → Commons 坐标搜索 → Commons 全文搜索收集文件名，返回 [(File:…, 来源说明)]，已去掉非照片。"""
    names = queries or [x for x in dict.fromkeys([p.get("name_local"), p.get("name")]) if x]
    xy = tuple(p["coords"][:2]) if p.get("coords") else None
    found = {}

    def add(title, why):
        title = title.replace("_", " ")  # pageimages 给下划线写法，Commons 返回空格写法，不统一就对不上
        if not NOT_PHOTO.search(title):
            found.setdefault(title, why)

    for lang in ("de", "en"):
        for q in names[:1]:
            js = _api(f"{lang}.wikipedia.org", generator="search", gsrsearch=q, gsrlimit=3,
                      prop="pageimages|coordinates", piprop="name")
            pages = sorted((js.get("query") or {}).get("pages", {}).values(), key=lambda x: x.get("index", 99))
            for pg in pages:
                if not pg.get("pageimage"):
                    continue
                co = (pg.get("coordinates") or [None])[0]
                # 没坐标的条目多是人名、文化、泛称，配图对不上地点（旧系统取图的经验）。
                if xy and (not co or _km(xy, (co["lat"], co["lon"])) > ARTICLE_MAX_KM):
                    continue
                dist = f"，条目坐标离地点 {_km(xy, (co['lat'], co['lon'])):.1f} km" if xy else ""
                add("File:" + pg["pageimage"], f"{lang} 维基条目“{pg['title']}”配图{dist}")
    if xy:
        js = _api("commons.wikimedia.org", list="geosearch", gscoord=f"{xy[0]}|{xy[1]}", gsradius=radius_m,
                  gsnamespace=6, gslimit=n)
        for h in js.get("query", {}).get("geosearch", []):
            add(h["title"], f"Commons 坐标搜索，离地点 {round(h['dist'])} m")
    for q in names[:2]:
        js = _api("commons.wikimedia.org", list="search", srsearch=q, srnamespace=6, srlimit=n)
        for h in js.get("query", {}).get("search", []):
            add(h["title"], f"Commons 全文搜索“{q}”")
    return list(found.items())


def find(pid, radius_m=500, n=6, queries=None):
    p = place(pid)
    cands = candidates(p, radius_m, n, queries)
    info = file_info([t for t, _ in cands], PREVIEW_W)
    folder = CACHE / pid
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*"):
        old.unlink()
    rows = []
    for t, why in cands:
        meta = info.get(t)
        # 实景照片基本是 JPEG；PNG 多是地图、示意图（旧系统取图的经验）。
        if not meta or meta["mime"] != "image/jpeg":
            continue
        k = len(rows) + 1
        path = folder / f"{k:02d}.jpg"
        path.write_bytes(_get(meta["thumb"], raw=True))
        rows.append({"n": k, "title": t, "why": why, "preview": str(path.relative_to(ROOT)), **meta})
    (folder / "candidates.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return p, rows


def _save_yaml(data):
    tmp = IMAGES_YAML.with_suffix(".yaml.tmp")
    tmp.write_text("# 由 tools/images.py 写；键是地点 id。格式见 docs/格式.md 第 1 节。\n"
                   + yaml.safe_dump(dict(sorted(data.items())), allow_unicode=True, sort_keys=False, width=200),
                   encoding="utf-8")
    tmp.replace(IMAGES_YAML)


def _load_yaml():
    return (yaml.safe_load(IMAGES_YAML.read_text(encoding="utf-8")) or {}) if IMAGES_YAML.exists() else {}


def pick(pid, title):
    place(pid)
    title = title.replace("_", " ")
    if not title.startswith("File:"):
        title = "File:" + title
    meta = file_info([title], PICK_W).get(title)
    if not meta:
        raise ImageError(f"Commons 上没有 {title}（只支持 Commons 上的文件；别处的图用 add-url）")
    ext = {"image/png": ".png", "image/webp": ".webp"}.get(meta["mime"], ".jpg")
    OUT.mkdir(exist_ok=True)
    for old in OUT.glob(f"{pid}.*"):
        old.unlink()
    path = OUT / f"{pid}{ext}"
    path.write_bytes(_get(meta["thumb"], raw=True))
    data = _load_yaml()
    data[pid] = {"file": str(path.relative_to(ROOT)), "author": meta["author"], "license": meta["license"],
                 "license_url": meta["license_url"] or None, "source_page": meta["page"],
                 "fetched": date.today().isoformat(), "checked_by_agent": True}
    data[pid] = {k: v for k, v in data[pid].items() if v is not None}
    _save_yaml(data)
    return path, data[pid]


def add_url(pid, url, note=None):
    place(pid)
    if not re.match(r"https?://", url):
        raise ImageError("链接要以 http:// 或 https:// 开头")
    data = _load_yaml()
    data[pid] = {"url": url, "author": "未知", "license": "未知", "source_page": url,
                 "fetched": date.today().isoformat(), "checked_by_agent": False, **({"note": note} if note else {})}
    _save_yaml(data)
    return data[pid]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("\n", 1)[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find", help="列候选并下载缩略图")
    f.add_argument("id")
    f.add_argument("--radius", type=int, default=500, help="Commons 坐标搜索半径（米，默认 500）")
    f.add_argument("-n", type=int, default=6, help="每种搜索最多取几条（默认 6）")
    f.add_argument("--q", action="append", help="换一个搜索词（可写多次）；默认用 name_local 与 name")
    k = sub.add_parser("pick", help="选定一张：下载 960px，写许可证")
    k.add_argument("id")
    k.add_argument("title", help="File:… 文件名（find 列出的）")
    u = sub.add_parser("add-url", help="记用户给的外链")
    u.add_argument("id")
    u.add_argument("url")
    u.add_argument("--note", help="一句说明，例如“用户 10-21 给的小红书图”")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "find":
            p, rows = find(a.id, a.radius, a.n, a.q)
            print(f"{a.id}：{p.get('name')}（{p.get('name_local') or ''}）  坐标 {p.get('coords') or '无'}")
            if not rows:
                print("没有候选。可以加大 --radius，或请用户给链接（add-url）。")
                return 1
            for r in rows:
                print(f"{r['n']:02d}  {r['title']}\n    {r['why']}｜{r['license']}｜{r['author'][:50]}｜{r['w']}×{r['h']}"
                      f"{'｜' + r['date'] if r['date'] else ''}\n    看图：{r['preview']}")
            print(f"\n看过图再选：python3 tools/images.py pick {a.id} \"File:…\"")
        elif a.cmd == "pick":
            path, meta = pick(a.id, a.title)
            print(f"存好 {path.relative_to(ROOT)}；data/images.yaml 记：{meta['author']} · {meta['license']} · {meta['source_page']}")
        else:
            meta = add_url(a.id, a.url, a.note)
            print(f"记好 {a.id} 的外链（许可证未知，网站只放链接，不嵌入图）：{meta['url']}")
        return 0
    except ImageError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
