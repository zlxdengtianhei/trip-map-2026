"""网站隐私：名单词替换或拦下、邮箱打码、扫描网站产物。名单在 config/privacy.txt（只在私有仓，不发布）。

  python3 tools/privacy.py _site        # 扫描一个目录；有命中就逐条打印并以 1 退出

build_site.py 写页面时调 redact()，写完调 scan_dir()；check.py 用 scan_text() 提前查喂给网站的源文件。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POLICY_FILE = ROOT / "config" / "privacy.txt"
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
EMAIL_MASK = "（邮箱已隐藏，见 repo）"
# 订单号样式：旧系统 config/system.json 的 forbid_patterns 与 drop_line_labels。
ORDER = [
    ("64 位哈希（多是订单或付款截图的文件名）", re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")),
    ("Airbnb 订单号样式", re.compile(r"\bHM[A-Z0-9]{8,}\b")),
    ("链接以外的 9 位以上连续数字", re.compile(r"(?<![\d.,])\d{9,}(?![\d.,])")),
    ("订单、确认号或护照号", re.compile(r"(订单号|预订号|确认号|确认码|预订码|订座号|护照号|Booking\s*(?:number|no\.?)|PNR)"
                                  r"\s*[:：]?\s*[A-Za-z0-9][A-Za-z0-9-]{4,}", re.I)),
]
URL = re.compile(r"https?://[^\s\"'<>)）]+")
TEXT_SUFFIXES = {".html", ".json", ".js", ".css", ".txt", ".md", ".py", ".xml", ".svg", ""}


def _word(w):
    # 用 ASCII 边界而不是 \b：中文字符在 Python 里也算“词字符”，\bAnna\b 匹配不到“Anna的”。
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(w)}(?![A-Za-z0-9])", re.I)


class Policy:
    """replace：[(正则, 显示为)]；forbid：[(说明, 正则)]。名单里每个词都在 forbid 里，带“=>”的另外先替换。"""

    def __init__(self, lines):
        self.replace, self.forbid = [], []
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            word, _, shown = (x.strip() for x in line.partition("=>"))
            if shown:
                # 中文里名字两边常留空格（“Anna 走峡谷”），替换成中文词后去掉这些空格。
                cjk = r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]"
                self.replace.append((re.compile(rf"(?:(?<={cjk})[ \t])?{_word(word).pattern}(?:[ \t](?={cjk}))?", re.I), shown))
            self.forbid.append((f"名单词“{word[:2]}…”", _word(word)))
        self.forbid += [("邮箱", EMAIL)] + ORDER


def load(path=POLICY_FILE):
    if not Path(path).exists():
        raise FileNotFoundError(f"没有隐私名单 {path}；网站不能在没有名单时构建")
    return Policy(Path(path).read_text(encoding="utf-8").splitlines())


def redact(text, policy):
    for rx, shown in policy.replace:
        text = rx.sub(shown, text)
    return EMAIL.sub(EMAIL_MASK, text)


def scan_text(text, policy):
    """返回 [(行号, 说明, 命中片段打码)]。命中片段只露头两个字符，免得日志本身泄露。"""
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        for what, rx in policy.forbid:
            # 网址里的长数字多是页面编号（地点库的官网链接实测有 9 位编号），只对正文查这一条。
            for m in rx.finditer(URL.sub(" ", line) if what.startswith("链接以外") else line):
                hits.append((i, what, m.group(0)[:2] + "…"))
    return hits


def scan_dir(folder, policy):
    """扫描目录下全部文本文件与文件名，返回 [(相对路径, 行号, 说明, 片段)]。"""
    folder = Path(folder)
    hits = []
    for p in sorted(folder.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(folder))
        for what, rx in ORDER[:1]:
            if rx.search(p.name):
                hits.append((rel, 0, "文件名含 " + what, p.name[:2] + "…"))
        if p.suffix.lower() in TEXT_SUFFIXES:
            hits += [(rel, *h) for h in scan_text(p.read_text(encoding="utf-8", errors="ignore"), policy)]
    return hits


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__)
        return 2
    try:
        policy = load()
    except FileNotFoundError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 2
    hits = scan_dir(argv[0], policy)
    for rel, line, what, frag in hits:
        print(f"{argv[0]}/{rel}:{line}：{what}（{frag}）")
    print(f"隐私扫描 {argv[0]}：{'没有命中' if not hits else f'{len(hits)} 处命中，不能发布'}")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
