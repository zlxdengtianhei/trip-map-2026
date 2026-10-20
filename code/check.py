"""一致性检查：只报不改。改完行程文件、推送之前跑一次。

  python3 tools/check.py            # 检查本仓
  python3 tools/check.py --root tools/examples/sample

查：天页 YAML 头齐全、pace 合法、[[id]] 都在地点库；地点库必填字段齐全、id 不重复；要提前做的状态合法；
“待你定”不超过 3 条；U- 原话 id 在 log/原话/ 里找得到；喂给网站的文件（以及已有的 _site/）里没有隐私词。
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
SITE_SOURCES = ["trip/days/*.md", "trip/要提前做的.md", "trip/决定.md", "data/places.yaml", "data/images.yaml"]


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
    check_privacy(root, problems)
    for f, ln, msg in problems:
        print(f"{f}:{ln}：{msg}")
    n_days, n_places = len(data["days"]), len(data["places"])
    print(f"检查完：{n_days} 个天页，{n_places} 个地点，{'没有问题' if not problems else f'{len(problems)} 个问题'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
