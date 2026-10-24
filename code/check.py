"""一致性检查：只报不改。改完行程文件、推送之前跑一次。

  python3 tools/check.py            # 检查本仓
  python3 tools/check.py --root tools/examples/sample

查：天页 YAML 头齐全、pace 合法、[[id]] 都在地点库；地点库必填字段齐全、id 不重复，可选的 heat、sources、transit
与 trail 小节（徒步路线：难度、长度、评分等）写法对、sources 里的资料文件都在；价格 price 与评分 rating 小节写法对、
offers 引用的优惠 id 都在 data/offers.yaml 里；优惠文件 data/offers.yaml（没有这个文件不算问题）每条字段齐全、取值合法；
天页头 plans（时间轴）的时刻是 HH:MM、type 与 sure 取值合法、同一轨道的块不重叠、place 与 options 的 id 都在地点库里；
图片的 taken（拍摄年份或日期）与 more（更多图）写法对；
买票与预约文件 data/bookings.yaml（没有这个文件不算问题）每条字段齐全、取值合法、place 在地点库里，订位与联系要有 contact；
优惠的 days（哪天用得上）写法对；
要提前做的状态合法；“待你定”不超过 3 条；U- 原话 id 在 log/原话/ 里找得到；喂给网站的文件（以及已有的 _site/）里没有隐私词。
字段写法在 tripdata.py 里查（写错的报出来，网站当作没写）；这里另查要读文件系统的（资料文件在不在）。
查询命令也在 tripdata.py：near（附近的地点）、find（搜地点）、day（按时间轴看一天），见该文件开头。
退出码：0 没问题；1 有问题（逐条打印 文件:行：说明）；2 检查本身跑不了。
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import privacy  # noqa: E402
import tripdata  # noqa: E402

# 新系统原话 id 是 U-MMDD-HHMMSS-<会话前 4 位>（10-20 04:1x 之前的没有后缀）；旧系统的 U-<8 位十六进制>-<6 位> 不在本仓，不查。
UID = re.compile(r"\bU-\d{4}-\d{6}(?:-[0-9a-z]{4})?\b")
SITE_SOURCES = ["trip/days/*.md", "trip/要提前做的.md", "trip/决定.md", "data/places.yaml", "data/images.yaml", "data/offers.yaml", "data/bookings.yaml", "trip/车票须知.md"]


def check_quote_ids(root, problems):
    known = set()
    for p in (root / "log" / "原话").glob("*.md"):
        known |= set(re.findall(r"^##\s+(U-\d{4}-\d{6}(?:-[0-9a-z]{4})?)", p.read_text(encoding="utf-8"), re.M))
    files = [*root.glob("trip/**/*.md"), *root.glob("data/*.yaml"), root / "log" / "流水.md"]
    for p in files:
        if not p.exists():
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").split("\n"), 1):
            for uid in UID.findall(line):
                if uid not in known:
                    problems.add(p.relative_to(root), i, f"原话 {uid} 在 log/原话/ 里找不到")


def check_sources(root, data, problems):
    # sources 的写法在 tripdata 里查；这里只查文件在不在（地点从资料里来，资料没了就找不回出处）
    for pid, p in data["places"].items():
        for x in p.get("sources") or []:
            if not x.startswith("/") and ".." not in Path(x).parts and not (root / x).exists():
                problems.add("data/places.yaml", p.get("_line", 0), f"{pid} 的 sources 里 {x} 不存在")


def check_privacy(root, problems):
    try:
        policy = privacy.load(root / "config" / "privacy.txt")
    except FileNotFoundError as e:
        problems.add("config/privacy.txt", 0, str(e))
        return
    for pattern in SITE_SOURCES:
        for p in sorted(root.glob(pattern)):
            text = privacy.redact(p.read_text(encoding="utf-8"), policy)
            for ln, what, frag in privacy.scan_text(text, policy):
                problems.add(p.relative_to(root), ln, f"上网站后仍有隐私词：{what}（{frag}）；改写或在名单里加替换")
    site = root / "_site"
    if site.exists():
        for rel, ln, what, frag in privacy.scan_dir(site, policy):
            problems.add(f"_site/{rel}", ln, f"网站产物里有隐私词：{what}（{frag}）")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("\n", 1)[1])
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent), help="行程仓根目录（默认本仓）")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    try:
        data, problems = tripdata.load_all(root)
    except Exception as e:  # 解析器自身出错也要给出能定位的报错，而不是把检查当成通过
        print(f"检查本身出错：{type(e).__name__}: {e}", file=sys.stderr)
        return 2
    check_quote_ids(root, problems)
    check_sources(root, data, problems)
    check_privacy(root, problems)
    for f, ln, msg in problems:
        print(f"{f}:{ln}：{msg}")
    n_days, n_places = len(data["days"]), len(data["places"])
    print(f"检查完：{n_days} 个天页，{n_places} 个地点，{'没有问题' if not problems else f'{len(problems)} 个问题'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
