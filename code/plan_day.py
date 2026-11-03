"""排一天的顺序：给定起点、最晚回到哪、候选地点，按真实时刻表精确搜索，给推荐、备选和每个被舍候选的原因。

  python3 tools/plan_day.py legs  tools/examples/10-28.yaml          # 查交通，写 tools/examples/10-28.legs.json
  python3 tools/plan_day.py solve tools/examples/10-28.yaml [--json]   # 用上一步的矩阵求解

输入格式看 tools/examples/10-28.yaml。工具只算，不替你取舍：候选、停留、开放时间、优先级、节奏由你填，
结果好坏取决于这些输入和交通矩阵。查不到的腿记为 null，要在 overrides 里按官方时刻表补（渡轮、缆车、齿轨常缺）。
只按一组人一条路线算；分头行动时每组各写一份输入。退出码：0 有方案；1 无可行方案或有腿缺数据；2 输入错误。
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import transit  # noqa: E402

# 节奏预设。weights：各优先级的分值；buffer_min：每段交通前留的缓冲（赶车、找路）；
# travel_cost_per_min：路上每分钟扣几分（含等车）；stop_cost：每多去一处扣几分。
# 紧＝能多去就多去，缓冲小，通勤便宜；稍紧、中依次放慢；松＝少而精、缓冲大、通勤贵，可选项基本不值得多跑一趟。
# 输入里写同名键可以覆盖预设。
PACES = {
    "紧": {"weights": {3: 300, 2: 150, 1: 80}, "buffer_min": 3, "travel_cost_per_min": 0.5, "stop_cost": 0},
    "稍紧": {"weights": {3: 300, 2: 130, 1: 60}, "buffer_min": 5, "travel_cost_per_min": 0.75, "stop_cost": 10},
    "中": {"weights": {3: 300, 2: 120, 1: 40}, "buffer_min": 5, "travel_cost_per_min": 1.0, "stop_cost": 20},
    "松": {"weights": {3: 300, 2: 100, 1: 20}, "buffer_min": 10, "travel_cost_per_min": 1.5, "stop_cost": 40},
}
PRIORITY = {"must": "must", "必去": "must", 3: 3, "3": 3, "最建议": 3, 2: 2, "2": 2, "建议": 2, 1: 1, "1": 1, "可选": 1}
# 步行不超过这么久的腿只用步行，不再查时刻表（省请求）。
WALK_ONLY_MIN = 20


class InputError(Exception):
    pass


def mins(v):
    """'HH:MM'、'HH:MM(+1)' → 当天 0 点起的分钟。PyYAML 把不加引号的 07:30 读成六十进制整数 450，正好也是分钟。"""
    if isinstance(v, int):
        return v
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})(?:\(\+(\d)\))?\s*", str(v))
    if not m:
        raise InputError(f"时间要写成 HH:MM，收到 {v!r}")
    return int(m.group(1)) * 60 + int(m.group(2)) + 1440 * int(m.group(3) or 0)


def hm(m):
    return f"{m // 60 % 24:02d}:{m % 60:02d}" + (f"(+{m // 1440})" if m >= 1440 else "")


def load(path):
    d = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    for k in ("day", "start", "end", "candidates"):
        if k not in d:
            raise InputError(f"{path} 缺 {k}")
    d["day"] = str(d["day"])
    pace = d.get("pace", "中")
    if pace not in PACES:
        raise InputError(f"pace 只能是 {' | '.join(PACES)}，收到 {pace!r}")
    d["_cfg"] = {**PACES[pace], **{k: d[k] for k in PACES[pace] if k in d}}
    d["_cfg"]["weights"] = {int(k): v for k, v in d["_cfg"]["weights"].items()}
    ids = [d["start"]["id"]] + [c["id"] for c in d["candidates"]] + [d["end"]["id"]]
    if len(set(ids[1:-1])) != len(ids) - 2 or set(ids[1:-1]) & {ids[0], ids[-1]}:
        raise InputError("候选 id 有重复，或与起点、终点同名")
    places = transit.places_index()
    for c in d["candidates"]:
        if c.get("priority") not in PRIORITY:
            raise InputError(f"{c['id']} 的 priority 只能是 must|3|2|1（或 必去|最建议|建议|可选），收到 {c.get('priority')!r}")
        c["priority"] = PRIORITY[c["priority"]]
        c.setdefault("name", (places.get(c["id"]) or {}).get("name") or c["id"])
        if "stay" not in c:
            raise InputError(f"{c['id']} 缺 stay（停留分钟）")
    for n in (d["start"], d["end"]):
        n.setdefault("name", (places.get(n["id"]) or {}).get("name") or n["id"])
    return d


def legs_path(path):
    p = Path(path)
    return p.with_name(p.stem + ".legs.json")


def build_legs(d, fresh=False):
    """查每对点之间的交通：先一次性取步行分钟，步行超过 WALK_ONLY_MIN 的再取整天时刻表。返回矩阵、缺数据的腿、请求说明。"""
    nodes = [d["start"]] + d["candidates"] + [d["end"]]
    loc = {n["id"]: transit.resolve(str(n.get("at") or n["id"]), fresh) for n in nodes}
    origins = [d["start"]] + d["candidates"]
    M, missing, notes = {}, [], []
    for a in origins:
        dests = [b for b in d["candidates"] + [d["end"]] if b["id"] != a["id"]]
        walk = [None] * len(dests)
        with_xy = [i for i, b in enumerate(dests) if loc[b["id"]]["lat"] is not None]
        if loc[a["id"]]["lat"] is not None and with_xy:
            got, note = transit.walk(loc[a["id"]], [loc[dests[i]["id"]] for i in with_xy], fresh)
            for i, m in zip(with_xy, got):
                walk[i] = m
            notes.append(note)
        M[a["id"]] = {}
        for b, w in zip(dests, walk):
            deps = []
            if w is None or w > WALK_ONLY_MIN:
                res, note = transit.day(loc[a["id"]], loc[b["id"]], d["day"], fresh)
                notes.append(note)
                deps = [[it["dep"], it["arr"], it["lines"]] for it in res["itineraries"]]
            M[a["id"]][b["id"]] = {"walk": w, "deps": deps} if (w is not None or deps) else None
            if M[a["id"]][b["id"]] is None:
                missing.append([a["id"], b["id"]])
    return {"day": d["day"], "built_at": datetime.now(transit.TZ).isoformat(timespec="seconds"),
            "nodes": {k: {"label": v["label"], "how": v["how"]} for k, v in loc.items()},
            "matrix": M, "missing": missing}, notes


def apply_overrides(d, M):
    M = json.loads(json.dumps(M))
    for o in d.get("overrides") or []:
        a, b = o["from"], o["to"]
        if a not in M:
            raise InputError(f"override 的 from {a!r} 不是起点或候选")
        cur = M[a].get(b) or {"walk": None, "deps": []}
        if "minutes" in o:
            cur["walk"] = int(o["minutes"])
        if "deps" in o:
            cur["deps"] = cur["deps"] + [[str(x[0]), str(x[1]), str(x[2]) if len(x) > 2 else o.get("label", "手填")]
                                         for x in o["deps"]]
        M[a][b] = cur
    return M


class Solver:
    def __init__(self, d, M):
        self.d, self.M, self.cfg = d, M, d["_cfg"]
        self.C = {c["id"]: c for c in d["candidates"]}
        self.t0, self.end_id, self.end_by = mins(d["start"]["time"]), d["end"]["id"], mins(d["end"]["by"])

    def leg(self, a, b, t):
        """从 a 最早 t 出发到 b：(到达, 路上分钟含等车, 怎么走)；到不了返回 None。"""
        if a == b:
            return t, 0, "原地"
        x = self.M.get(a, {}).get(b)
        if not x:
            return None
        buf, best = self.cfg["buffer_min"], None
        if x.get("walk") is not None:
            best = (t + buf + x["walk"], f"步行 {x['walk']}′")
        for dep, arr, label in x.get("deps") or []:
            dm, am = mins(dep), mins(arr)
            if dm >= t + buf and am >= dm and (best is None or am < best[0]):
                best = (am, f"{dep}→{arr} {label}")
        return None if best is None else (best[0], best[0] - t, best[1])

    def window(self, c):
        op, cl = (mins(c["open"][0]), mins(c["open"][1])) if c.get("open") else (0, 2880)
        last = mins(c["last_entry"]) if c.get("last_entry") else cl - c["stay"]
        return op, min(last, cl - c["stay"])

    def solve(self, force=(), drop=(), topk=3):
        W, lam, sc = self.cfg["weights"], self.cfg["travel_cost_per_min"], self.cfg["stop_cost"]
        must = {i for i, c in self.C.items() if c["priority"] == "must" and i not in drop} | set(force)
        best, labels = [], {}

        def dominated(key, t, travel):
            L = labels.setdefault(key, [])
            if any(t2 <= t and tr2 <= travel for t2, tr2 in L):
                return True
            L[:] = [(t2, tr2) for t2, tr2 in L if not (t <= t2 and travel <= tr2)] + [(t, travel)]
            return False

        def visit(cur, t, seq, travel, value, tl):
            if dominated((frozenset(seq), cur), t, travel):
                return
            if must <= set(seq):
                g = self.leg(cur, self.end_id, t)
                if g and g[0] <= self.end_by:
                    tr = travel + g[1]
                    best.append((value - sc * len(seq) - lam * tr, tr, list(seq),
                                 tl + [{"id": self.end_id, "arrive": g[0], "via": g[2]}]))
            for cid, c in self.C.items():
                if cid in seq or cid in drop or (c.get("before") and c["before"] in seq):
                    continue
                g = self.leg(cur, cid, t)
                if not g:
                    continue
                op, last = self.window(c)
                st = max(g[0], op)
                if st > last:
                    continue
                lv = st + c["stay"]
                g2 = self.leg(cid, self.end_id, lv)
                if not g2 or g2[0] > self.end_by:
                    continue
                seq.append(cid)
                w = W[3] if c["priority"] == "must" else W[c["priority"]]
                visit(cid, lv, seq, travel + g[1], value + w,
                      tl + [{"id": cid, "arrive": g[0], "start": st, "leave": lv, "via": g[2]}])
                seq.pop()

        visit(self.d["start"]["id"], self.t0, [], 0, 0, [{"id": self.d["start"]["id"], "leave": self.t0}])
        best.sort(key=lambda x: (-x[0], x[1]))
        out, seen = [], set()
        for b in best:
            if frozenset(b[2]) not in seen:
                seen.add(frozenset(b[2]))
                out.append(b)
            if len(out) >= topk:
                break
        return out

    def why_alone_fails(self, cid):
        c, start = self.C[cid], self.d["start"]["id"]
        g = self.leg(start, cid, self.t0)
        if not g:
            return "从起点到这里没有交通数据（矩阵为 null，按官方时刻表补 overrides）"
        op, last = self.window(c)
        st = max(g[0], op)
        if st > last:
            return f"最早 {hm(g[0])} 到，晚于最晚开始 {hm(last)}"
        g2 = self.leg(cid, self.end_id, st + c["stay"])
        if not g2:
            return "从这里回终点没有交通数据（补 overrides）"
        if g2[0] > self.end_by:
            return f"停 {c['stay']}′ 后最早 {hm(g2[0])} 回到终点，晚于 {hm(self.end_by)}"
        return "单独去可行，但与必去项或先后要求冲突"

    def explain(self, plans):
        chosen, reasons = set(plans[0][2]), {}
        for cid in self.C:
            if cid in chosen:
                continue
            alt = self.solve(force=[cid], topk=1)
            if not alt:
                reasons[cid] = "放不进：" + self.why_alone_fails(cid)
                continue
            lost = sorted(chosen - set(alt[0][2]))
            reasons[cid] = (f"能去，但最好的含它方案得分 {alt[0][0]:.0f}，比推荐低 {plans[0][0] - alt[0][0]:.0f}"
                            + (f"，要舍掉 {'、'.join(self.C[i]['name'] for i in lost)}" if lost else "，多花通勤")
                            + f"（顺序：{' → '.join(self.C[i]['name'] for i in alt[0][2])}）")
        return reasons

    def diagnose(self):
        musts = [i for i, c in self.C.items() if c["priority"] == "must"]
        if not self.leg(self.d["start"]["id"], self.end_id, self.t0) and not musts:
            return ["起点到终点没有交通数据，也没有能去的候选"]
        out = [f"{self.C[i]['name']}：{self.why_alone_fails(i)}" for i in musts if not self.solve(force=[i], drop=set(musts) - {i}, topk=1)]
        if not out and musts:
            out.append("必去项单独都放得进，但放在一起超出时限；考虑缩短停留或改天")
        return out or ["时限内放不下任何方案"]


def render(d, plans, reasons, dt, missing):
    names = {**{c["id"]: c["name"] for c in d["candidates"]}, d["start"]["id"]: d["start"]["name"], d["end"]["id"]: d["end"]["name"]}
    res = {"day": d["day"], "pace": d.get("pace", "中"), "settings": d["_cfg"], "elapsed_ms": round(dt * 1000, 1),
           "missing_legs": missing, "plans": [], "dropped": reasons}
    for score, travel, seq, tl in plans:
        steps = []
        for x in tl:
            steps.append({"id": x["id"], "name": names[x["id"]], **{k: hm(x[k]) for k in ("arrive", "start", "leave") if k in x},
                          **({"wait_min": x["start"] - x["arrive"]} if "start" in x else {}), "via": x.get("via")})
        res["plans"].append({"score": round(score, 1), "travel_min": travel, "order": seq, "timeline": steps})
    return res


def print_text(res, d):
    cfg = res["settings"]
    print(f"{res['day']} 节奏 {res['pace']}（缓冲 {cfg['buffer_min']}′，通勤每分钟扣 {cfg['travel_cost_per_min']} 分，"
          f"每处扣 {cfg['stop_cost']} 分；用时 {res['elapsed_ms']} ms）")
    for k, p in enumerate(res["plans"]):
        print(f"\n{'推荐' if k == 0 else f'备选 {k}'}（得分 {p['score']:.0f}，路上 {p['travel_min']}′ 含等车）")
        for x in p["timeline"]:
            if x["id"] == d["start"]["id"] and "arrive" not in x:
                print(f"  {x['leave']}  从 {x['name']} 出发")
            elif "start" in x:
                wait = f"，等开门 {x['wait_min']}′" if x["wait_min"] else ""
                print(f"  {x['arrive']}  到 {x['name']}（{x['via']}）；{x['start']}–{x['leave']}{wait}")
            else:
                print(f"  {x['arrive']}  到 {x['name']}（{x['via']}；最晚 {d['end']['by']}）")
    if res["plans"] and not res["plans"][0]["order"] and d["candidates"]:
        print("\n注意：推荐是“哪儿也不去”。若候选其实去得了，多半是权重或节奏不合适，或腿缺数据。")
    if res["dropped"]:
        print("\n没进推荐的：")
        for cid, why in res["dropped"].items():
            print(f"- {next(c['name'] for c in d['candidates'] if c['id'] == cid)}：{why}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("\n", 1)[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("legs", help="查交通，写 <输入>.legs.json")
    p1.add_argument("input")
    p1.add_argument("--fresh", action="store_true", help="不用交通缓存")
    p2 = sub.add_parser("solve", help="求解")
    p2.add_argument("input")
    p2.add_argument("--json", action="store_true")
    p2.add_argument("--alternatives", type=int, default=2, help="备选几个（默认 2）")
    a = ap.parse_args(argv)
    try:
        d = load(a.input)
        if a.cmd == "legs":
            t = time.time()
            legs, notes = build_legs(d, a.fresh)
            legs_path(a.input).write_text(json.dumps(legs, ensure_ascii=False, indent=1), encoding="utf-8")
            fetched = sum(1 for n in notes if n.startswith("新查"))
            print(f"写好 {legs_path(a.input)}：{len(notes)} 次查询（新查 {fetched}，其余用缓存），{time.time() - t:.0f} s")
            for k, v in legs["nodes"].items():
                print(f"  {k}：{v['label']}（{v['how']}）")
            if legs["missing"]:
                print("这些腿查不到交通，按官方时刻表在输入的 overrides 里补（例：{from: a, to: b, deps: [[\"08:35\", \"08:42\", \"渡轮\"]]} 或 {from: a, to: b, minutes: 15}）：")
                for x, y in legs["missing"]:
                    print(f"  null  {x} → {y}")
                return 1
            return 0
        lp = legs_path(a.input)
        if not lp.exists():
            raise InputError(f"没有 {lp}；先跑 plan_day.py legs {a.input}")
        legs = json.loads(lp.read_text(encoding="utf-8"))
        need = {(x["id"], y["id"]) for x in [d["start"]] + d["candidates"] for y in d["candidates"] + [d["end"]] if x["id"] != y["id"]}
        have = {(x, y) for x in legs["matrix"] for y in legs["matrix"][x]}
        if need - have:
            raise InputError(f"{lp} 是旧的（候选变了）；重跑 plan_day.py legs {a.input}")
        M = apply_overrides(d, legs["matrix"])
        missing = [[x, y] for x, y in sorted(need) if not M[x].get(y)]
        s = Solver(d, M)
        t = time.time()
        plans = s.solve(topk=1 + a.alternatives)
        dt = time.time() - t
        if not plans:
            why = s.diagnose()
            if a.json:
                print(json.dumps({"day": d["day"], "error": "无可行方案", "why": why, "missing_legs": missing}, ensure_ascii=False, indent=1))
            else:
                print(f"{d['day']} 无可行方案：")
                for w in why:
                    print(f"- {w}")
                for x, y in missing:
                    print(f"- 缺数据的腿：{x} → {y}")
            return 1
        res = render(d, plans, s.explain(plans), dt, missing)
        if a.json:
            print(json.dumps(res, ensure_ascii=False, indent=1))
        else:
            print_text(res, d)
            if missing:
                print("\n缺数据的腿（当作走不通）：" + "；".join(f"{x}→{y}" for x, y in missing))
        return 0
    except (InputError, transit.TransitError, KeyError, yaml.YAMLError) as e:
        print(f"错误：{e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
