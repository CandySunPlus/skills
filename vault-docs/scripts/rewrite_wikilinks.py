#!/usr/bin/env python3
"""把镜像里的 Obsidian wikilink 改写成相对链接。

vault 里跨文引用写作 [[某文档]]，只有 Obsidian 认这套语法。同步进仓库之后
GitHub 与各类编辑器都把它当普通文字，跨文引用全部点不动——而镜像存在的意义
恰恰是让代码和编码代理在仓库内直接读这些文档。

[[某文档#某小节]] 里的锚点也一并译过去：读目标文件的标题，按 GitHub 给标题
生成锚点的规则算出 slug，拼进链接。译不出来的（块引用 #^块号、对不上任何标题的）
留成文件级链接并在报告里列出，不静默指错。

由 sync-docs.sh 在注入只读横幅之后、写基线之前调用。位置在写基线之前，改写
结果才能成为基线的一部分，校验那套机制不必知道它存在。

用法：rewrite_wikilinks.py <镜像根目录>

vault 侧不受影响，转换只发生在镜像。
"""

from __future__ import annotations

import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})([^\n]*)")
BACKTICK_RUN_RE = re.compile(r"`+")
BLANK_LINE_RE = re.compile(r"\n[ \t]*\n")

# ATX 标题。要求 # 之后有空白，否则 #标签 会被当成标题。
ATX_RE = re.compile(r"^ {0,3}#{1,6}(?:[ \t]+(.*?))?[ \t]*$")
# 闭合式标题末尾那串 #（`## 标题 ##`）。前面必须是空白，不然 `# C#` 会被削成 `C`。
CLOSING_HASHES_RE = re.compile(r"(?:\A|[ \t])#+\Z")
# YAML frontmatter。里面以 # 开头的行是 YAML 注释，不是标题。
FRONTMATTER_RE = re.compile(r"\A---[ \t]*\n.*?\n---[ \t]*(?:\n|\Z)", re.DOTALL)
# 标题里的行内链接，取其显示文字——GitHub 的锚点按渲染后的文字算。
INLINE_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")

# 目标不含 [ ] | # 与反斜杠，锚点与别名各自可选。![[...]] 的叹号单独捕获。
# 别名分隔符允许前置反斜杠——表格单元格里的 wikilink 必须写 \| ，裸竖线会把单元格切开。
WIKILINK_RE = re.compile(r"(!?)\[\[([^\[\]|#\\]+)(#[^\[\]|\\]*)?(?:\\?\|([^\[\]]*))?\]\]")

# 只转义会破坏 markdown 链接语法的字符，中文原样保留，链接在源码里肉眼可读。
_ESCAPE = {" ": "%20", "(": "%28", ")": "%29", "<": "%3C", ">": "%3E"}
_UNESCAPE = {code: char for char, code in _ESCAPE.items()}

# 宽松匹配，只用来兜住 WIKILINK_RE 解析不了的 [[...]] 形态，不参与改写。
# 内容允许出现单个 ]（如 [[目标|别名]带杂字]]），只在遇到连续 ]] 时才收口，
# 否则形如 [^\]]* 的写法会被一个孤立的 ] 卡住、连起点都匹配不到。
LOOSE_WIKILINK_RE = re.compile(r"!?\[\[(?:[^\]]|\](?!\]))*\]\]")


def _mask(text: str, spans: list[tuple[int, int]]) -> bytearray:
    """把区间铺成逐字符标记，便于 O(1) 判断某个位置在不在区间里。"""
    mask = bytearray(len(text))
    for start, end in spans:
        mask[start:end] = b"\x01" * (end - start)
    return mask


def _fence_spans(text: str) -> list[tuple[int, int]]:
    """围栏代码块的字符区间。

    闭合围栏要同种字符、不短于开启标记、且不带信息串——否则 ```python 这样的行
    会把外层围栏提前关掉。文件结束时围栏未闭合，就算到文件末尾。
    """
    spans: list[tuple[int, int]] = []
    pos = 0
    open_at: int | None = None
    open_marker = ""
    for line in text.splitlines(keepends=True):
        end = pos + len(line)
        match = FENCE_RE.match(line)
        if open_at is None:
            if match:
                open_at, open_marker = pos, match.group(1)
        elif (
            match
            and match.group(1)[0] == open_marker[0]
            and len(match.group(1)) >= len(open_marker)
            and not match.group(2).strip()
        ):
            spans.append((open_at, end))
            open_at = None
        pos = end
    if open_at is not None:
        spans.append((open_at, len(text)))
    return spans


def _inline_spans(text: str, fences: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """行内代码的字符区间。

    按 CommonMark 配对：一段 N 个反引号由下一段恰好 N 个反引号闭合。额外加一条
    「跨空行不配对」——孤立反引号在文档里迟早会出现，这条把它的影响限制在本段，
    不至于让半篇文档被误判成代码。
    """
    blocked = _mask(text, fences)
    runs = [
        (match.start(), match.end())
        for match in BACKTICK_RUN_RE.finditer(text)
        if not blocked[match.start()]
    ]
    spans: list[tuple[int, int]] = []
    i = 0
    while i < len(runs):
        start, end = runs[i]
        width = end - start
        i += 1
        for j in range(i, len(runs)):
            other_start, other_end = runs[j]
            if BLANK_LINE_RE.search(text, end, other_start):
                break
            if other_end - other_start == width:
                spans.append((start, other_end))
                i = j + 1
                break
    return spans


def code_spans(text: str) -> list[tuple[int, int]]:
    """text 中属于代码的字符区间，围栏代码块与行内代码都算。"""
    fences = _fence_spans(text)
    return sorted(fences + _inline_spans(text, fences))


# slug 里保留的两个标点。GitHub 删掉其余标点与符号，只留下这两个。
_SLUG_KEEP = frozenset("-_")


def github_slug(title: str) -> str:
    """标题文本 -> GitHub 给它生成的锚点。

    规则三步：转小写、删掉标点与符号（`-` 与 `_` 留着）、空格换成连字符。
    中文不受影响，所以「4. WebSocket 控制协议」出来是 4-websocket-控制协议。
    """
    chars = []
    for char in title.lower():
        if char == " ":
            chars.append("-")
        elif char in _SLUG_KEEP or unicodedata.category(char)[0] not in "PSCZ":
            chars.append(char)
    return "".join(chars)


def _heading_text(raw: str) -> str:
    """标题行里 # 之后那部分的渲染文字。

    行内链接取显示文字：GitHub 按渲染结果算锚点，而 [[目标|别名]] 在镜像里会被
    改写成 [别名](路径)，两种形态渲染出来是同一串字，所以这儿算一次就够。
    """
    title = CLOSING_HASHES_RE.sub("", raw).strip()
    title = WIKILINK_RE.sub(
        lambda m: m.group(4) or (m.group(2) + (m.group(3) or "")), title
    )
    return INLINE_LINK_RE.sub(r"\1", title).strip()


def heading_anchors(text: str) -> dict[str, str]:
    """一个文档的「标题文本 -> 锚点」。键是转小写后的标题，Obsidian 认锚点不分大小写。

    同名标题只登记第一处，Obsidian 跳的也是第一处；但重名计数要照所有标题走一遍，
    因为 GitHub 给第二个同名标题的锚点是 slug-1、第三个是 slug-2，漏数一个后面全错。
    """
    fences = _mask(text, _fence_spans(text))
    frontmatter = FRONTMATTER_RE.match(text)
    body_at = frontmatter.end() if frontmatter else 0
    anchors: dict[str, str] = {}
    seen: dict[str, int] = {}
    pos = 0
    for line in text.splitlines(keepends=True):
        start, pos = pos, pos + len(line)
        if start < body_at or fences[start]:
            continue
        match = ATX_RE.match(line.rstrip("\n"))
        if match is None:
            continue
        title = _heading_text(match.group(1) or "")
        base = github_slug(title)
        if not base:
            continue
        count = seen.get(base, 0)
        seen[base] = count + 1
        anchors.setdefault(title.lower(), base if count == 0 else f"{base}-{count}")
    return anchors


def resolve_anchor(anchors: dict[str, str], anchor: str) -> str | None:
    """Obsidian 的 #小节 -> 镜像里的锚点。译不出来返回 None。

    嵌套写法 #上级#下级 取最后一段，那才是真正指的标题。
    块引用 #^块号 在 GitHub 上没有对应的锚点，只能落回文件级链接。
    """
    title = anchor.lstrip("#").split("#")[-1].strip()
    if not title or title.startswith("^"):
        return None
    return anchors.get(title.lower())


def build_index(root: Path) -> dict[str, Path]:
    """wikilink 目标 -> 镜像内的实际文件。

    每个文档登记两个键：相对镜像根去扩展名的路径（`03-节点规格/00-通则`），以及
    纯文件名去扩展名（`00-通则`，对应 Obsidian 的最短唯一路径写法）。

    两遍走：先铺全路径键，再补纯文件名键。分两遍是因为纯文件名可能与某个根目录
    文档的全路径键撞上，一遍走会把后者挤掉。纯文件名在多个目录下重名时直接不登记，
    只认全路径。
    """
    docs = sorted(root.rglob("*.md"))
    index = {path.relative_to(root).with_suffix("").as_posix(): path for path in docs}
    by_stem: dict[str, list[Path]] = {}
    for path in docs:
        by_stem.setdefault(path.stem, []).append(path)
    for stem, paths in by_stem.items():
        if len(paths) == 1 and stem not in index:
            index[stem] = paths[0]
    return index


@dataclass
class Report:
    """一次改写的账目。每一处 wikilink 都要落进其中一栏，不许有下落不明的。"""

    rewritten: int = 0
    in_code: list[str] = field(default_factory=list)
    external: dict[str, int] = field(default_factory=dict)
    anchored: int = 0
    anchor_missed: list[str] = field(default_factory=list)
    embeds: list[str] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)
    generated: list[tuple[Path, str]] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"wikilink 改写 {self.rewritten} 处"]
        if self.external:
            total = sum(self.external.values())
            detail = "、".join(
                f"{target} ×{count}" for target, count in sorted(self.external.items())
            )
            lines.append(
                f"  库外保留 {total} 处（{len(self.external)} 个目标）：{detail}"
            )
        if self.in_code:
            lines.append(f"  代码内跳过 {len(self.in_code)} 处：")
            lines += [f"    {item}" for item in self.in_code]
        if self.anchored:
            lines.append(f"  其中带锚点 {self.anchored} 处，锚点已译成 GitHub 的写法")
        if self.anchor_missed:
            lines.append(
                f"  锚点译不出来 {len(self.anchor_missed)} 处，链接落在文件顶部："
            )
            lines += [f"    {item}" for item in self.anchor_missed]
        if self.embeds:
            lines.append(f"  嵌入语法 {len(self.embeds)} 处，原样保留：")
            lines += [f"    {item}" for item in self.embeds]
        if self.unparsed:
            lines.append(f"  无法解析 {len(self.unparsed)} 处，原样保留：")
            lines += [f"    {item}" for item in self.unparsed]
        return "\n".join(lines)


def encode_path(path: str) -> str:
    return "".join(_ESCAPE.get(char, char) for char in path)


def decode_path(href: str) -> str:
    for code, char in _UNESCAPE.items():
        href = href.replace(code, char)
    return href


def rewrite_text(
    text: str,
    source: Path,
    root: Path,
    index: dict[str, Path],
    report: Report,
    anchors: dict[Path, dict[str, str]] | None = None,
) -> str:
    """改写单个文件的正文。source 是该文件的绝对路径，root 是镜像根。

    命中索引的改成相对链接；未命中的原样保留 [[...]]——仓库里没有可指的文件，
    保留原形态读者一眼看得出这是 vault 笔记，比造个死链或悄悄抹成纯文本诚实。

    anchors 是「目标文件 -> 它的标题锚点表」的缓存，按需填；跨文件复用它，同一个
    被大量引用的文档才不会被反复读、反复扫标题。
    """
    if anchors is None:
        anchors = {}
    in_code = _mask(text, code_spans(text))
    pieces: list[str] = []
    last = 0
    for match in WIKILINK_RE.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        where = f"{source.relative_to(root).as_posix()}:{line}"
        if in_code[match.start()]:
            report.in_code.append(f"{where} {match.group(0)}")
            continue
        bang = match.group(1)
        target = match.group(2).strip()
        anchor = match.group(3) or ""
        alias = match.group(4) or ""
        if bang:
            report.embeds.append(f"{where} {match.group(0)}")
            continue
        dest = index.get(target)
        if dest is None:
            report.external[target] = report.external.get(target, 0) + 1
            continue
        href = encode_path(dest.relative_to(source.parent, walk_up=True).as_posix())
        if anchor:
            if dest not in anchors:
                # 目标文件此刻可能还没被本轮改写。标题不受改写影响：[[目标|别名]]
                # 会变成 [别名](路径)，渲染出来是同一串字，锚点算的就是那串字。
                anchors[dest] = heading_anchors(dest.read_text(encoding="utf-8"))
            slug = resolve_anchor(anchors[dest], anchor)
            if slug is None:
                report.anchor_missed.append(f"{where} {match.group(0)}")
            else:
                href += f"#{slug}"
                report.anchored += 1
        display = alias if alias else target + anchor
        pieces.append(text[last : match.start()])
        pieces.append(f"[{display}]({href})")
        last = match.end()
        report.rewritten += 1
        report.generated.append((source, href))
    pieces.append(text[last:])

    # 兜底：宽松模式扫到、但 WIKILINK_RE 没能在同一起点匹配上的 [[...]]，记进
    # unparsed 而不是无声消失——Report 的 docstring 承诺每一处都要落进某一栏。
    matched_starts = {m.start() for m in WIKILINK_RE.finditer(text)}
    for loose in LOOSE_WIKILINK_RE.finditer(text):
        if loose.start() not in matched_starts:
            line = text.count("\n", 0, loose.start()) + 1
            report.unparsed.append(
                f"{source.relative_to(root).as_posix()}:{line} {loose.group(0)}"
            )
    return "".join(pieces)


def verify_generated(pairs: list[tuple[Path, str]]) -> list[str]:
    """检查每条生成的链接都能解析到真实文件。

    兜的是本脚本的路径计算错误，不是用户输入问题——所以只查自己刚生成的链接，
    不去管文档里原有的其他相对链接。
    """
    broken: list[str] = []
    for source, href in pairs:
        # 锚点由目标文件自己的标题算出来，不必再验；这儿只看路径那一段。
        target = source.parent / decode_path(href.split("#", 1)[0])
        if not target.exists():
            broken.append(f"{source}: {href} → {target}")
    return broken


def rewrite_tree(root: Path) -> Report:
    """原地改写整个镜像。返回汇总账目。"""
    index = build_index(root)
    report = Report()
    anchors: dict[Path, dict[str, str]] = {}
    for path in sorted(root.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        rewritten = rewrite_text(text, path, root, index, report, anchors)
        if rewritten != text:
            path.write_text(rewritten, encoding="utf-8")
    return report


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("用法：rewrite_wikilinks.py <镜像根目录>", file=sys.stderr)
        return 2
    root = Path(argv[1]).resolve()
    if not root.is_dir():
        print(f"镜像根目录不存在：{root}", file=sys.stderr)
        return 2
    report = rewrite_tree(root)
    print(report.summary())
    broken = verify_generated(report.generated)
    if broken:
        print("以下改写后的链接指向不存在的文件，是脚本的路径计算出了错：", file=sys.stderr)
        for item in broken:
            print(f"  {item}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
