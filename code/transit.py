"""查公共交通：查站、查几班车、整天时刻表。数据来自 Transitous（MOTIS 2 公开 API），只用标准库。

  python3 tools/transit.py stops "Bad Ischl Bahnhof"
  python3 tools/transit.py plan "Bad Ischl Bahnhof" 47.5618,13.6494 2026-10-28T09:00 [--arrive] [-n 5]
  python3 tools/transit.py day  "Budapest-Keleti" "Wien Hbf" 2026-10-27
  python3 tools/transit.py walk "Bad Ischl Bahnhof" 47.6987,13.6071 47.7150,13.6210

起终点可写：`纬度,经度`、地点库里的 id（有 coords 的）、Transitous 站 id、或站名（取查站的第一条）。
时间按维也纳当地时间（布达佩斯同一时区）。结果缓存在 data/cache/transit/，同一请求 6 小时内直接用缓存；
`--fresh` 强制重查；联网失败时退回旧缓存并注明多久以前查的。首末段步行上限默认 20′，查到 0 条时自动放宽到 60′。
退出码：0 有结果；1 查到 0 条；2 输入或网络错误。数据条款：https://transitous.org/api/
"""
import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date as Date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache" / "transit"
TZ = ZoneInfo("Europe/Vienna")
API = "https://api.transitous.org/api"
# Transitous 条款要求 User-Agent 带联系方式（https://transitous.org/api/）。
UA = "trip-2026-tools/1.0 (+https://github.com/zlxdengtianhei/trip-map-2026)"
# 不含 AIRPLANE：坐标起点时 Transitous 会混进航班（选型实测 t4：布达佩斯机场 → Deák 返回了经 Cagliari 的 Ryanair）。
MODES = ("TRAM,SUBWAY,FERRY,BUS,COACH,RAIL,HIGHSPEED_RAIL,LONG_DISTANCE,NIGHT_RAIL,REGIONAL_FAST_RAIL,"
         "REGIONAL_RAIL,SUBURBAN,FUNICULAR,AERIAL_LIFT,CABLE_CAR,OTHER")
# 首末段步行上限：服务端默认 900 秒时，景点离车站稍远就 0 条（选型实测 t5）；一律放到 60′ 又会混进“下车走 54′”的班次。
# 所以先按 20′ 查，0 条再自动放宽到 60′，并在输出里写明。
WALK_DEFAULT_MIN, WALK_WIDE_MIN, LONG_WALK_MIN = 20, 60, 20
TTL = {"plan": timedelta(hours=6), "day": timedelta(hours=6), "geocode": timedelta(days=7), "walk": timedelta(days=30)}
COORD = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")
PLACE_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_last_request = [0.0]
_offline = []


class TransitError(Exception):
    def __init__(self, msg, network=False):
        super().__init__(msg)
        self.network = network


def _get(path, params):
    url = f"{API}/{path}?" + urllib.parse.urlencode(params)
    if _offline:
        raise TransitError(_offline[0], network=True)
    wait = 0.3 - (time.time() - _last_request[0])
    if wait > 0:
        time.sleep(wait)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=90) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        raise TransitError(f"Transitous 返回 HTTP {e.code}：{e.read()[:300].decode('utf-8', 'replace')}（{url}）")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        # 云端会话的默认网络对外部域名一律 403（主会话 10-20 实测）；一次连不上，本进程后面的请求都不再试。
        _offline.append(f"连不上 api.transitous.org（{getattr(e, 'reason', e)}）。在云端会话里，多半是环境网络没放行这个域名："
                        "到 claude.ai/code 的环境设置把网络改成 Full，或 Custom 加上 api.transitous.org。"
                        "没联网时只能用缓存；起终点写地点库 id 或 纬度,经度 不用联网")
        raise TransitError(_offline[0], network=True)
    finally:
        _last_request[0] = time.time()
    return url, json.loads(body)


def _cached(kind, params, fetch, fresh=False):
    """返回 (结果, 说明)。说明写明是新查的、缓存的，还是联网失败退回的旧缓存。"""
    key = hashlib.sha1(json.dumps([kind, params], sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
    path = CACHE / f"{kind}-{key}.json"
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    now = datetime.now(TZ)
    if old and not fresh:
        age = now - datetime.fromisoformat(old["fetched_at"])
        if age < TTL[kind]:
            return old["result"], f"缓存（{_ago(age)}前查）"
    try:
        url, result = fetch()
    except TransitError as e:
        if old and e.network:
            age = now - datetime.fromisoformat(old["fetched_at"])
            if not _offline[1:]:
                print(f"注意：{e}。下面用缓存。", file=sys.stderr)
                _offline.append("warned")
            return old["result"], f"旧缓存（{_ago(age)}前查，联网失败）"
        raise
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"kind": kind, "request": params, "url": url, "fetched_at": now.isoformat(timespec="seconds"),
                               "result": result}, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return result, f"新查（{now:%m-%d %H:%M}）"


def _ago(td):
    m = int(td.total_seconds() // 60)
    return f"{m} 分钟" if m < 120 else f"{m // 60} 小时" if m < 2880 else f"{m // 1440} 天"


def stops(text, n=8, fresh=False):
    """按名字查站。返回 [{name, id, lat, lon, area, modes}]，按 Transitous 的相关度排。"""
    params = {"text": text, "type": "STOP", "numResults": max(n, 5)}

    def fetch():
        url, js = _get("v1/geocode", params)
        out = [{"name": m.get("name"), "id": m.get("id"), "lat": m.get("lat"), "lon": m.get("lon"),
                "area": ", ".join(a["name"] for a in m.get("areas", []) if a.get("matched") or a.get("default"))[:60],
                "modes": m.get("modes") or []}
               for m in js if m.get("type") == "STOP" and m.get("lat", -1) != -1]
        return url, out

    res, note = _cached("geocode", params, fetch, fresh)
    return res[:n], note


def places_index():
    """地点库里有 coords 的条目，按 id 索引；没有 PyYAML 或没有地点库时返回空。"""
    path = ROOT / "data" / "places.yaml"
    if not path.exists():
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    return {p["id"]: p for p in (yaml.safe_load(path.read_text(encoding="utf-8")) or [])
            if isinstance(p, dict) and p.get("id") and p.get("coords")}


def resolve(text, fresh=False):
    """把用户写的起终点变成 Transitous 认的位置。返回 {place（发给 API 的串）, label, lat, lon, how}。"""
    m = COORD.match(text)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        return {"place": f"{lat},{lon}", "label": f"{lat:.4f},{lon:.4f}", "lat": lat, "lon": lon, "how": "坐标"}
    if PLACE_ID.match(text):
        p = places_index().get(text)
        if p:
            lat, lon = p["coords"][:2]
            return {"place": f"{lat},{lon}", "label": p.get("name") or text, "lat": lat, "lon": lon, "how": f"地点库 {text} 的坐标"}
    if re.search(r"[:_]", text) and " " not in text:
        return {"place": text, "label": text, "lat": None, "lon": None, "how": "站 id"}
    hits, _ = stops(text, n=3, fresh=fresh)
    if not hits:
        raise TransitError(f"查不到叫“{text}”的站；换个写法，或直接写 纬度,经度")
    h = hits[0]
    others = "；".join(x["name"] for x in hits[1:])
    return {"place": h["id"], "label": h["name"], "lat": h["lat"], "lon": h["lon"],
            "how": f"站名“{text}”取第一条" + (f"（其余：{others}）" if others else "")}


def walk(origin, dests, fresh=False, max_min=60):
    """步行分钟数。origin、dests 是带 lat/lon 的 resolve() 结果；返回 ([与 dests 同序的分钟，走不到为 None], 说明)。"""
    for p in [origin, *dests]:
        if p["lat"] is None:
            raise TransitError(f"{p['label']} 没有坐标，算不了步行（写 纬度,经度 或站名）")
    params = {"one": f"{origin['lat']};{origin['lon']}", "many": ",".join(f"{p['lat']};{p['lon']}" for p in dests),
              "mode": "WALK", "max": max_min * 60, "maxMatchingDistance": 250, "arriveBy": "false"}

    def fetch():
        url, js = _get("v1/one-to-many", params)
        return url, [round(x["duration"] / 60) if "duration" in x else None for x in js]

    return _cached("walk", params, fetch, fresh)


def _local(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(TZ)


def _hm(t, base):
    s = t.strftime("%H:%M")
    return s if t.date() == base else f"{s}(+{(t.date() - base).days})"


def _normalize(js):
    out = []
    for it in js.get("itineraries", []):
        start, end = _local(it["startTime"]), _local(it["endTime"])
        base = start.date()
        legs, notes = [], []
        for lg in it["legs"]:
            if lg["mode"] == "WALK":
                legs.append({"mode": "WALK", "minutes": round(lg["duration"] / 60),
                             "to": {"END": "终点", "START": "起点"}.get(lg["to"]["name"], lg["to"]["name"])})
                continue
            line = lg.get("displayName") or lg.get("routeShortName") or lg.get("tripShortName") or lg["mode"]
            legs.append({"mode": lg["mode"], "line": line, "headsign": lg.get("headsign") or "",
                         "agency": lg.get("agencyName") or "", "from": lg["from"]["name"], "to": lg["to"]["name"],
                         "dep": _hm(_local(lg["startTime"]), base), "arr": _hm(_local(lg["endTime"]), base),
                         "realtime": bool(lg.get("realTime"))})
            if lg.get("cancelled"):
                notes.append(f"{line} 已取消")
            if lg.get("reservation") not in (None, "NONE"):
                notes.append(f"{line} 订座：{lg['reservation']}")
            for a in lg.get("alerts") or []:
                if a.get("headerText"):
                    notes.append(f"{line}：{a['headerText'][:120]}")
        transit = [l for l in legs if l["mode"] != "WALK"]
        out.append({"date": base.isoformat(), "dep": start.strftime("%H:%M"), "arr": _hm(end, base),
                    "start": start.isoformat(timespec="minutes"), "end": end.isoformat(timespec="minutes"),
                    "minutes": round(it["duration"] / 60), "transfers": it.get("transfers", max(len(transit) - 1, 0)),
                    "lines": "+".join(l["line"] for l in transit) or "步行",
                    "first_walk": legs[0]["minutes"] if legs and legs[0]["mode"] == "WALK" else 0,
                    "last_walk": legs[-1]["minutes"] if legs and legs[-1]["mode"] == "WALK" else 0,
                    "legs": legs, "notes": notes})
    direct = [round(d["duration"] / 60) for d in js.get("direct", []) if all(l["mode"] == "WALK" for l in d["legs"])]
    return {"itineraries": out, "walk_direct_min": min(direct) if direct else None}


def _params(frm, to, when, arrive, w, extra):
    return {"fromPlace": frm["place"], "toPlace": to["place"], "time": when.isoformat(timespec="seconds"),
            "arriveBy": "true" if arrive else "false", "transitModes": MODES, "maxPreTransitTime": w * 60,
            "maxPostTransitTime": w * 60, "detailedLegs": "false", "detailedTransfers": "false", **extra}


def _day_extra(d):
    start = datetime(d.year, d.month, d.day, tzinfo=TZ)
    nxt = d + timedelta(days=1)
    window = int((datetime(nxt.year, nxt.month, nxt.day, tzinfo=TZ) - start).total_seconds())
    # timetableView 下 numItineraries 是下限，小值让服务端只返回窗口内的最优班次，不越过当天（实测 5 → 当天 17 班）。
    return start, {"timetableView": "true", "searchWindow": window, "numItineraries": 5}


def _from_day_cache(frm, to, when, walk_min):
    """没联网又没有这条 plan 的缓存时，从同一对起终点当天的整天时刻表缓存里取。"""
    start, extra = _day_extra(when.date())
    for w in ([walk_min] if walk_min else [WALK_DEFAULT_MIN, WALK_WIDE_MIN]):
        params = _params(frm, to, start, False, w, extra)
        key = hashlib.sha1(json.dumps(["day", params], sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
        path = CACHE / f"day-{key}.json"
        if path.exists():
            old = json.loads(path.read_text(encoding="utf-8"))
            return old["result"], f"没联网，用 {_ago(datetime.now(TZ) - datetime.fromisoformat(old['fetched_at']))}前查的整天时刻表"
    return None, None


def _search(kind, frm, to, when, arrive, extra, fresh, walk_min):
    tries = [walk_min] if walk_min else [WALK_DEFAULT_MIN, WALK_WIDE_MIN]
    for w in tries:
        params = _params(frm, to, when, arrive, w, extra)
        res, note = _cached(kind, params, lambda: (lambda u, js: (u, _normalize(js)))(*_get("v6/plan", params)), fresh)
        if res["itineraries"]:
            break
    if w != tries[0]:
        note += f"；步行上限 {tries[0]}′ 时 0 条，已放宽到 {w}′"
    return res, note


def plan(frm, to, when, arrive=False, n=5, fresh=False, walk_min=None):
    """几班车。frm/to 是 resolve() 的结果；when 是带时区的 datetime。返回 ({itineraries, walk_direct_min}, 说明)。"""
    try:
        res, note = _search("plan", frm, to, when, arrive, {"numItineraries": n}, fresh, walk_min)
    except TransitError as e:
        res, note = _from_day_cache(frm, to, when, walk_min) if e.network else (None, None)
        if res is None:
            raise
        t = when.isoformat(timespec="minutes")
        res = {**res, "itineraries": [x for x in res["itineraries"] if (x["end"] <= t if arrive else x["start"] >= t)]}
    its = sorted(res["itineraries"], key=lambda x: x["end"] if arrive else x["start"], reverse=arrive)[:n]
    return {**res, "itineraries": sorted(its, key=lambda x: x["start"])}, note


def day(frm, to, day_str, fresh=False, walk_min=None):
    """整天时刻表：当天出发的全部班次，按（出发, 到达, 线路）去重。返回 ({itineraries, walk_direct_min}, 说明)。"""
    start, extra = _day_extra(Date.fromisoformat(day_str))
    res, note = _search("day", frm, to, start, False, extra, fresh, walk_min)
    seen, out = set(), []
    for it in sorted(res["itineraries"], key=lambda x: (x["start"], x["end"])):
        k = (it["dep"], it["arr"], it["lines"])
        if it["date"] != day_str or k in seen:
            continue
        seen.add(k)
        out.append(it)
    return {**res, "itineraries": out}, note


def variants(its):
    """标出同一趟主车的换乘变体：比另一班多换乘、出发只晚 ≤10′、到达最多早 5′。返回（主要班次, 变体数）。"""
    t = [(datetime.fromisoformat(x["start"]), datetime.fromisoformat(x["end"]), x["transfers"]) for x in its]
    keep = [x for (xs, xe, xt), x in zip(t, its)
            if not any(yt < xt and timedelta(0) <= xs - ys <= timedelta(minutes=10) and ye - xe <= timedelta(minutes=5)
                       for ys, ye, yt in t)]
    return keep, len(its) - len(keep)


def _print_itinerary(i, it):
    tail = f"，末段步行 {it['last_walk']}′" if it["last_walk"] > LONG_WALK_MIN else ""
    print(f"{i}) {it['dep']} → {it['arr']}  {it['minutes']}′  换乘 {it['transfers']}{tail}")
    for lg in it["legs"]:
        if lg["mode"] == "WALK":
            if lg["minutes"]:
                print(f"     步行 {lg['minutes']}′ → {lg['to']}")
        else:
            rt = "（实时）" if lg["realtime"] else ""
            print(f"     {lg['dep']} {lg['line']} [{lg['mode']}] {lg['from']} → {lg['to']} {lg['arr']}{rt}"
                  + (f"  往 {lg['headsign']}" if re.search(r"[^\W\d_]", lg["headsign"]) else ""))
    for n in it["notes"]:
        print(f"     ! {n}")


def _header(a, b, what, note):
    print(f"{a['label']} → {b['label']}  {what}  ［Transitous，{note}］")
    for p, w in ((a, "起点"), (b, "终点")):
        if p["how"] not in ("站 id",):
            print(f"  {w}：{p['how']}")


def _empty_hint(res):
    print(f"没有查到车次（首末段步行已放到 {WALK_WIDE_MIN}′）。可能原因：起终点离车站更远（换成附近车站或缆车站的坐标）、"
          "当天没有班次、或数据源缺这段（渡轮、缆车、齿轨常缺，查官方时刻表）。")
    if res.get("walk_direct_min") is not None:
        print(f"直接步行约 {res['walk_direct_min']}′。")


def _parse_when(s):
    try:
        return datetime.fromisoformat(s).replace(tzinfo=TZ)
    except ValueError:
        raise TransitError(f"时间要写成 YYYY-MM-DDTHH:MM（当地时间），收到 “{s}”")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("\n", 1)[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("stops", help="按名字查站")
    s.add_argument("text")
    s.add_argument("-n", type=int, default=8)
    w = sub.add_parser("walk", help="从一处步行到几处各要几分钟")
    w.add_argument("frm", metavar="起点")
    w.add_argument("to", metavar="终点", nargs="+")
    for name, hlp in (("plan", "某个时刻前后的几班"), ("day", "整天时刻表")):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("frm", metavar="起点")
        p.add_argument("to", metavar="终点")
        p.add_argument("when", metavar="YYYY-MM-DDTHH:MM" if name == "plan" else "YYYY-MM-DD")
        if name == "plan":
            p.add_argument("--arrive", action="store_true", help="时间是最晚到达")
            p.add_argument("-n", type=int, default=5, help="列几班（默认 5）")
        else:
            p.add_argument("--all", action="store_true", help="连换乘变体一起列")
        p.add_argument("--walk", type=int, metavar="分钟", help="首末段步行上限（默认 20，0 条时自动放宽到 60）")
    for p in sub.choices.values():
        p.add_argument("--fresh", action="store_true", help="不用缓存，重查")
        p.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "stops":
            hits, note = stops(a.text, a.n, a.fresh)
            if a.json:
                print(json.dumps(hits, ensure_ascii=False, indent=1))
            else:
                print(f"“{a.text}”的站  ［Transitous，{note}］")
                for h in hits:
                    print(f"- {h['name']}｜{h['area']}｜{h['lat']:.5f},{h['lon']:.5f}｜{h['id']}｜{','.join(h['modes'][:5])}")
            return 0 if hits else 1
        if a.cmd == "walk":
            frm, dests = resolve(a.frm, a.fresh), [resolve(t, a.fresh) for t in a.to]
            mins, note = walk(frm, dests, a.fresh)
            if a.json:
                print(json.dumps([{"to": d["label"], "minutes": m} for d, m in zip(dests, mins)], ensure_ascii=False))
            else:
                print(f"从 {frm['label']} 步行  ［Transitous，{note}］")
                for d, m in zip(dests, mins):
                    print(f"- {d['label']}：" + (f"{m}′" if m is not None else "60′ 内走不到"))
            return 0
        frm, to = resolve(a.frm, a.fresh), resolve(a.to, a.fresh)
        if a.cmd == "plan":
            when = _parse_when(a.when)
            res, note = plan(frm, to, when, a.arrive, a.n, a.fresh, a.walk)
            what = f"{when:%m-%d %H:%M} " + ("之前到" if a.arrive else "之后出发")
        else:
            Date.fromisoformat(a.when)
            res, note = day(frm, to, a.when, a.fresh, a.walk)
            what = f"{a.when} 全天 {len(res['itineraries'])} 班（已去重）"
    except (TransitError, ValueError) as e:
        print(f"错误：{e}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps({"from": frm, "to": to, "source": note, **res}, ensure_ascii=False, indent=1))
        return 0 if res["itineraries"] else 1
    _header(frm, to, what, note)
    if not res["itineraries"]:
        _empty_hint(res)
        return 1
    if a.cmd == "plan":
        for i, it in enumerate(res["itineraries"], 1):
            _print_itinerary(i, it)
        if res.get("walk_direct_min") is not None:
            print(f"另：直接步行约 {res['walk_direct_min']}′。")
    else:
        its, hidden = (res["itineraries"], 0) if a.all else variants(res["itineraries"])
        for it in its:
            tail = f" 末段步行{it['last_walk']}′" if it["last_walk"] > LONG_WALK_MIN else ""
            print(f"{it['dep']}→{it['arr']}  {it['minutes']:>3}′ {it['transfers']}换  {it['lines']}{tail}")
        if hidden:
            print(f"（另有 {hidden} 班是上面某班的换乘变体：多换乘、出发晚不到 10′、到达早不到 5′；--all 全列）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
