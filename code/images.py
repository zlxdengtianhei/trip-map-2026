"""给地点找图：列候选、看图后选定、记许可证。图来自维基百科条目配图、Wikimedia Commons 与 Openverse。

  python3 tools/images.py find katrin                 # 列候选，缩略图下载到 data/cache/images/katrin/，先看图
  python3 tools/images.py pick katrin "File:BadIschlKatrinSiriuskoglTraun.JPG"   # 960px 存 images/katrin.jpg，写 data/images.yaml
  python3 tools/images.py add-url katrin https://…    # 记用户给的外链（许可证未知，网站只放“查看图片”链接）
  python3 tools/images.py find nussensee --since 2018 --cat Nussensee -n 20   # 只列 2018 年及以后拍的；加查 Commons 分类
  python3 tools/images.py fill-taken                  # 给 data/images.yaml 里还没有 taken 的条目补拍摄日期
  python3 tools/images.py pick dohany-synagogue openverse:4dff3a28-…   # 选 find 列出的 Openverse 结果

find 只列候选，不替你判断：坐标搜索常混进无关的照片（选型实测：缆车站旁的胡蜂照片），pick 之前必须看图。
拍摄日期（10-22 加）：find 每行开头显示 [拍摄年份]，pick 把它写进该条的 taken。来源是 Commons 文件页的
DateTimeOriginal（EXIF 或描述里的 date），写成 YYYY-MM-DD，缺日子（只到月或年）写 YYYY；
文件页没有拍摄日期（或只写了“original upload date”）时，先看分类“… photographs taken on YYYY-MM-DD”，
再没有才用上传日期，并加 taken_from: upload。
--since 年份：只留该年及以后拍的候选（按上传日期代替的也按它算）。
--cat 分类名：再列 Commons 该分类里最新加入的 n 个文件（地点的分类往往比搜索准，新图也多）。
  按上传时间排的全文搜索试过：混进大量别处的同名纪念碑、人像，不用。
Openverse（10-22 加，api.openverse.org，不用 key）：汇集 Flickr 等处的 CC 图。find 用同样的搜索词（前两个）再查
Openverse，排除 Wikimedia 来源（上面已查过），结果写成 openverse:<id> 和 Commons 候选列在一起。只要 CC0、CC BY、
CC BY-SA、公有领域标记（网站公开、代码 MIT，NC、ND 一律不要）。Openverse 不给拍摄日期：Flickr 的图读照片页里的
dateTaken（没有就用 datePosted，记 taken_from: upload），别的来源记不了 taken。pick 时再用 Flickr oEmbed 核一次
作者现在的许可证，不在允许范围就拒绝；Flickr 图存 1024 宽（Flickr 的标准尺寸）。匿名额度 20 次/分钟、200 次/天（搜索与 pick 各算一次），用完时 find 只列 Commons。
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
            ra = e.headers.get("Retry-After")
            ra = int(ra) if ra and ra.isdigit() else 5 * (attempt + 1)
            # Openverse 的日额度用完时 Retry-After 是几小时，不干等
            if e.code == 429 and attempt < 3 and ra <= 120:
                time.sleep(ra)
                continue
            if e.code in (500, 502, 503) and attempt < 3:  # Openverse 常有偶发 500，隔几秒重试就好（实测）
                time.sleep(3 * (attempt + 1))
                continue
            raise ImageError(f"HTTP {e.code}：{url}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            _last[0] = time.time()
            host = urllib.parse.urlsplit(url).hostname or ""
            if attempt < 1 and "timed out" in str(getattr(e, "reason", e)):  # Openverse 偶尔一次超时（实测），重试一次
                continue
            if host.endswith(("openverse.org", "flickr.com", "staticflickr.com")):
                raise ImageError(f"连不上 {host}（{getattr(e, 'reason', e)}）")
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


MONTHS = "january february march april may june july august september october november december".split()
_MON = r"(" + "|".join(MONTHS) + r")"
_Y = r"((?:19|20)\d\d)"


def parse_date(s):
    """一段日期文字 → YYYY-MM-DD，缺日子的给 YYYY（data/images.yaml 只认这两种）；认不出给 None。
    实见写法：2016-08-14 15:59:05、2012-07、
    Taken on 21 April 2018, 11:36、July 3, 2019、30.10.2006、30.02006（坏写法，只取年）。"""
    s = (s or "").lower()
    if m := re.search(rf"(?<!\d){_Y}-(\d\d)(?:-(\d\d))?(?!\d)", s):
        return f"{m[1]}-{m[2]}-{m[3]}" if m[3] else m[1]
    if m := re.search(rf"(?<!\d)(\d{{1,2}})\.?\s+{_MON},?\s+{_Y}", s):
        return f"{m[3]}-{MONTHS.index(m[2]) + 1:02d}-{int(m[1]):02d}"
    if m := re.search(rf"{_MON}\s+(\d{{1,2}}),?\s+{_Y}", s):
        return f"{m[3]}-{MONTHS.index(m[1]) + 1:02d}-{int(m[2]):02d}"
    if m := re.search(rf"{_MON},?\s+{_Y}", s):
        return m[2]
    if m := re.search(rf"(?<!\d)(\d{{1,2}})\.(\d{{1,2}})\.{_Y}", s):
        return f"{m[3]}-{int(m[2]):02d}-{int(m[1]):02d}"
    if m := re.search(rf"{_Y}(?!\d)", s):
        return m[1]
    return None


def taken_date(raw, uploaded, cats=""):
    """(taken, 来源)：先用 DateTimeOriginal，去掉其中“(… (original upload date))”那段；再看分类
    “photographs taken on YYYY-MM-DD”；都没有就用上传日期，来源记 upload。"""
    s = _strip(raw)
    own = re.sub(r"\(?[^()]*\(original upload date\)\)?", " ", s, flags=re.I)
    if d := parse_date(own):
        return d, None
    if m := re.search(r"taken on ((?:19|20)\d\d-\d\d-\d\d)", cats or "", re.I):
        return m[1], None
    return (parse_date(s) or (uploaded or "")[:10] or None), "upload"


def file_info(titles, width):
    """Commons 文件信息：{title: {thumb, page, license, license_url, author, mime, w, h, taken, taken_from}}；
    不在 Commons 的文件不返回。"""
    out = {}
    for i in range(0, len(titles), 40):
        js = _api("commons.wikimedia.org", prop="imageinfo", iiprop="url|extmetadata|mime|size|timestamp",
                  iiurlwidth=width, titles="|".join(titles[i:i + 40]))
        for pg in js.get("query", {}).get("pages", {}).values():
            ii = (pg.get("imageinfo") or [None])[0]
            if not ii:
                continue
            em = ii.get("extmetadata", {})
            val = lambda k: _strip((em.get(k) or {}).get("value"))
            taken, taken_from = taken_date((em.get("DateTimeOriginal") or {}).get("value"), ii.get("timestamp"),
                                           val("Categories"))
            out[pg["title"]] = {"thumb": ii.get("thumburl") or ii.get("url"), "page": ii.get("descriptionurl"),
                                "license": val("LicenseShortName") or "未知", "license_url": val("LicenseUrl"),
                                "author": val("Artist") or val("Credit") or "未知", "mime": ii.get("mime"),
                                "w": ii.get("width"), "h": ii.get("height"), "taken": taken, "taken_from": taken_from}
    return out


OV_API = "https://api.openverse.org/v1/images/"
OV_PREFIX = "openverse:"
OV_LICENSES = {"by": "CC BY", "by-sa": "CC BY-SA", "cc0": "CC0", "pdm": "Public Domain Mark"}
# Flickr oEmbed 的 license_id 里允许的几种（其余是 NC、ND、保留所有权利或来历不清）。
FLICKR_OK = {"4": ("CC BY 2.0", "https://creativecommons.org/licenses/by/2.0/"),
             "5": ("CC BY-SA 2.0", "https://creativecommons.org/licenses/by-sa/2.0/"),
             "9": ("CC0", "https://creativecommons.org/publicdomain/zero/1.0/"),
             "10": ("Public Domain Mark 1.0", "https://creativecommons.org/publicdomain/mark/1.0/"),
             "11": ("CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/"),
             "12": ("CC BY-SA 4.0", "https://creativecommons.org/licenses/by-sa/4.0/")}


def _flickr_size(url, size):
    """Flickr 图片地址换尺寸后缀：z = 640 宽（看图），b = 1024 宽（选定）。"""
    return re.sub(r"(_[a-z])?\.jpg$", f"_{size}.jpg", url) if "staticflickr.com" in url else url


def flickr_taken(page_url):
    """Flickr 照片页里的 dateTaken；没有就用 datePosted（来源记 upload）。Flickr 不用 key 拿不到拍摄日期，只能读页面。"""
    h = _get(page_url, raw=True).decode("utf-8", "replace")
    if m := re.search(r'"dateTaken":"((?:19|20)\d\d-\d\d-\d\d)', h):
        return m[1], None
    if m := re.search(r'"datePosted":"(\d{9,})"', h):
        return date.fromtimestamp(int(m[1])).isoformat(), "upload"
    return None, None


def ov_meta(r):
    """Openverse 一条结果 → 和 file_info 同样的字段；不是允许的许可证给 None。"""
    lic = r.get("license")
    if lic not in OV_LICENSES:
        return None
    name = OV_LICENSES[lic] + (f" {r['license_version']}" if lic not in ("cc0",) and r.get("license_version") else "")
    url = r.get("url") or ""
    flickr = r.get("source") == "flickr"
    taken, taken_from = flickr_taken(r["foreign_landing_url"]) if flickr else (None, None)
    jpeg = (r.get("filetype") or "").lower() in ("jpg", "jpeg") or url.lower().endswith((".jpg", ".jpeg"))
    return {"thumb": _flickr_size(url, "z") if flickr else r.get("thumbnail"), "full": url,
            "page": r.get("foreign_landing_url"), "license": name, "license_url": r.get("license_url"),
            "author": r.get("creator") or "未知", "mime": "image/jpeg" if jpeg else r.get("filetype"),
            "w": r.get("width"), "h": r.get("height"), "taken": taken, "taken_from": taken_from,
            "source": r.get("source")}


def ov_search(q, n):
    """Openverse 搜图，只要允许的许可证，排除 Wikimedia（Commons 上面已经查过）。"""
    js = _get(OV_API + "?" + urllib.parse.urlencode({"q": q, "license": ",".join(OV_LICENSES), "page_size": n,
                                                     "excluded_source": "wikimedia"}))
    return js.get("results", [])


def ov_pick_meta(ov_id):
    r = _get(OV_API + urllib.parse.quote(ov_id) + "/")
    meta = ov_meta(r)
    if not meta:
        raise ImageError(f"Openverse 上 {ov_id} 的许可证是 {r.get('license')} {r.get('license_version')}，不能用")
    if meta["source"] == "flickr":  # 以 Flickr 现在的许可证为准：作者可能改过，Openverse 的索引会旧
        o = _get("https://www.flickr.com/services/oembed/?" + urllib.parse.urlencode(
            {"format": "json", "url": meta["page"]}))
        if str(o.get("license_id")) not in FLICKR_OK:
            raise ImageError(f"Flickr 上这张现在的许可证是 {o.get('license') or o.get('license_id')}，不能用")
        meta["license"], meta["license_url"] = FLICKR_OK[str(o["license_id"])]
        meta["thumb"] = _flickr_size(meta["full"], "b")
    else:
        meta["thumb"] = meta["full"]
    return meta


def _image_bytes(meta):
    """先下 thumb；Flickr 换尺寸失败（原图另有密钥时会这样）就下 Openverse 给的原地址。"""
    try:
        return _get(meta["thumb"], raw=True)
    except ImageError:
        if meta.get("full") and meta["full"] != meta["thumb"]:
            return _get(meta["full"], raw=True)
        raise


def taken_label(meta):
    """find 每行开头的 [年份]；上传日期代替的写 [上传 年份]。"""
    y = (meta.get("taken") or "????")[:4]
    return f"[上传 {y}]" if meta.get("taken_from") == "upload" else f"[{y}]"


def candidates(p, radius_m=500, n=6, queries=None, cats=()):
    """按条目配图 → Commons 坐标搜索 → Commons 全文搜索 → 指定分类（最新加入的在前）收集文件名，
    返回 [(File:…, 来源说明)]，已去掉非照片。"""
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
    for c in cats:
        c = c if c.startswith("Category:") else "Category:" + c
        js = _api("commons.wikimedia.org", list="categorymembers", cmtitle=c, cmtype="file", cmlimit=n,
                  cmsort="timestamp", cmdir="desc")
        for h in js.get("query", {}).get("categorymembers", []):
            add(h["title"], f"Commons 分类“{c[9:]}”")
    return list(found.items())


def ov_candidates(p, n=6, queries=None):
    """Openverse 候选：[(openverse:<id>, 来源说明, meta)]。额度用完或连不上只给警告，不影响 Commons 候选。"""
    names = queries or [x for x in dict.fromkeys([p.get("name_local"), p.get("name")]) if x]
    out, seen = [], set()
    for q in names[:2]:
        try:
            results = ov_search(q, n)
        except ImageError as e:
            print(f"警告：Openverse 查不了（{e}），这次只列 Commons 候选", file=sys.stderr)
            break
        for r in results:
            if r["id"] in seen or NOT_PHOTO.search(r.get("title") or ""):
                continue
            seen.add(r["id"])
            try:
                meta = ov_meta(r)
            except ImageError as e:
                print(f"警告：{r.get('foreign_landing_url')} 读不了（{e}），跳过", file=sys.stderr)
                continue
            if meta:
                out.append((OV_PREFIX + r["id"], f"Openverse·{r.get('source')} 搜索“{q}”：{(r.get('title') or '')[:50]}"
                            f"｜{meta['page']}", meta))
    return out


def find(pid, radius_m=500, n=6, queries=None, since=None, cats=()):
    p = place(pid)
    cands = candidates(p, radius_m, n, queries, cats=cats)
    info = file_info([t for t, _ in cands], PREVIEW_W)
    for t, why, meta in ov_candidates(p, n, queries):
        cands.append((t, why))
        info[t] = meta
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
        if since and (meta["taken"] or "0")[:4] < str(since):
            continue
        k = len(rows) + 1
        path = folder / f"{k:02d}.jpg"
        try:
            path.write_bytes(_image_bytes(meta))
        except ImageError as e:
            print(f"警告：{t} 的缩略图下不了（{e}），跳过", file=sys.stderr)
            continue
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
    ov_id = title[len(OV_PREFIX):] if title.startswith(OV_PREFIX) else None
    if ov_id:
        meta = ov_pick_meta(ov_id)
    else:
        title = title.replace("_", " ")
        if not title.startswith("File:"):
            title = "File:" + title
        meta = file_info([title], PICK_W).get(title)
        if not meta:
            raise ImageError(f"Commons 上没有 {title}（只支持 Commons 文件与 find 列出的 openverse:…；别处的图用 add-url）")
    ext = {"image/png": ".png", "image/webp": ".webp"}.get(meta["mime"], ".jpg")
    OUT.mkdir(exist_ok=True)
    for old in OUT.glob(f"{pid}.*"):
        old.unlink()
    path = OUT / f"{pid}{ext}"
    path.write_bytes(_image_bytes(meta))
    data = _load_yaml()
    data[pid] = {"file": str(path.relative_to(ROOT)), "author": meta["author"], "license": meta["license"],
                 "license_url": meta["license_url"] or None, "source_page": meta["page"],
                 "taken": meta["taken"], "taken_from": meta["taken_from"],
                 "fetched": date.today().isoformat(), "checked_by_agent": True}
    data[pid] = {k: v for k, v in data[pid].items() if v is not None}
    _save_yaml(data)
    return path, data[pid]


def fill_taken(redo=False):
    """给 data/images.yaml 里来自 Commons、还没有 taken 的条目补拍摄日期（redo 时全部重取）；返回 [(id, taken, taken_from)]。"""
    data = _load_yaml()
    todo = {}
    for pid, img in data.items():
        sp = (img.get("source_page") or "") if isinstance(img, dict) else ""
        if "commons.wikimedia.org/wiki/" in sp and (redo or not img.get("taken")):
            todo["File:" + urllib.parse.unquote(sp.split("/wiki/File:", 1)[1]).replace("_", " ")] = pid
    info = file_info(list(todo), PREVIEW_W)
    done = []
    for title, pid in todo.items():
        meta = info.get(title)
        if not meta:
            continue
        img = {k: v for k, v in data[pid].items() if k not in ("taken", "taken_from")}
        head = {k: img.pop(k) for k in ("file", "url", "author", "license", "license_url", "source_page") if k in img}
        new = {"taken": meta["taken"], "taken_from": meta["taken_from"]}
        data[pid] = {**head, **{k: v for k, v in new.items() if v is not None}, **img}
        done.append((pid, meta["taken"], meta["taken_from"]))
    _save_yaml(data)
    return done, [pid for t, pid in todo.items() if t not in info]


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
    f.add_argument("--since", type=int, help="只列该年及以后拍的（例如 2018）")
    f.add_argument("--cat", action="append", default=[], help="再列 Commons 这个分类里最新加入的 n 个文件（可写多次）")
    k = sub.add_parser("pick", help="选定一张：下载 960px，写许可证")
    k.add_argument("id")
    k.add_argument("title", help="find 列出的 File:… 文件名，或 openverse:<id>")
    t = sub.add_parser("fill-taken", help="给还没有 taken 的条目补拍摄日期")
    t.add_argument("--redo", action="store_true", help="已有 taken 的也重取")
    u = sub.add_parser("add-url", help="记用户给的外链")
    u.add_argument("id")
    u.add_argument("url")
    u.add_argument("--note", help="一句说明，例如“用户 10-21 给的小红书图”")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "find":
            p, rows = find(a.id, a.radius, a.n, a.q, a.since, a.cat)
            print(f"{a.id}：{p.get('name')}（{p.get('name_local') or ''}）  坐标 {p.get('coords') or '无'}")
            if not rows:
                print("没有候选。可以加大 --radius，或请用户给链接（add-url）。")
                return 1
            for r in rows:
                print(f"{r['n']:02d}  {taken_label(r)}  {r['title']}\n    {r['why']}｜{r['license']}｜{r['author'][:50]}｜"
                      f"{r['w']}×{r['h']}｜{'上传' if r['taken_from'] else '拍摄'} {r['taken'] or '未知'}\n    看图：{r['preview']}")
            print(f"\n看过图再选：python3 tools/images.py pick {a.id} \"File:…\"")
        elif a.cmd == "pick":
            path, meta = pick(a.id, a.title)
            print(f"存好 {path.relative_to(ROOT)}；data/images.yaml 记：{meta['author']} · {meta['license']} · "
                  f"{'上传' if meta.get('taken_from') else '拍摄'} {meta.get('taken') or '未知'} · {meta['source_page']}")
        elif a.cmd == "fill-taken":
            done, missing = fill_taken(a.redo)
            for pid, taken, src in done:
                print(f"{pid}  {taken or '未知'}{'（上传日期）' if src else ''}")
            print(f"补好 {len(done)} 条" + (f"；Commons 上找不到：{', '.join(missing)}" if missing else ""))
        else:
            meta = add_url(a.id, a.url, a.note)
            print(f"记好 {a.id} 的外链（许可证未知，网站只放链接，不嵌入图）：{meta['url']}")
        return 0
    except ImageError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
