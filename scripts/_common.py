"""Let's Read 各脚本共用的工具函数：读取工作目录、文本归一化、全书索引。"""

import bisect
import json
import re
import unicodedata
from pathlib import Path

CJK_RE = re.compile(r'[㐀-鿿豈-﫿]')

# 引号统一为 ASCII，避免中英文引号、直角引号造成的误判
QUOTE_MAP = {
    '“': '"', '”': '"', '„': '"', '‟': '"', '「': '"', '」': '"',
    '『': '"', '』': '"', '﹁': '"', '﹂': '"', '＂': '"',
    '‘': "'", '’': "'", '‚': "'", '‛': "'", '＇': "'",
}
DASH_CHARS = set('—–―─‐‑‒−﹣－')
DROP_CHARS = set('­​‌‍⁠﻿')

# 很多用思源/Noto 字体排版的 PDF，提取出的文字会把常用字变成外形相同的“部首字符”
# （如 ⽅⻚⻜⻄⼈⺠），看着一样，编码却不同，会导致引文核对失败、摘录出错。这里统一换回正常汉字。
RADICAL_SUPPLEMENT = {
    '⺠': '民', '⻄': '西', '⻅': '见', '⻆': '角', '⻇': '角', '⻉': '贝', '⻋': '车',
    '⻑': '長', '⻓': '长', '⻔': '门', '⻘': '青', '⻙': '韦', '⻚': '页', '⻛': '风',
    '⻜': '飞', '⻢': '马', '⻣': '骨', '⻤': '鬼', '⻥': '鱼', '⻦': '鸟', '⻧': '卤',
    '⻨': '麦', '⻩': '黄', '⻪': '黾', '⻫': '斉', '⻬': '齐', '⻭': '歯', '⻮': '齿',
    '⻯': '竜', '⻰': '龙', '⻲': '亀', '⻳': '龟', '⺟': '母', '⻁': '虎', '⺊': '卜',
    '⺜': '日', '⺝': '月',
}


def fix_cjk_compat(text: str) -> str:
    """把康熙部首（U+2F00–2FDF）、部首补充和兼容汉字换成对应的统一汉字，其余字符不动。"""
    out = []
    for ch in text:
        cp = ord(ch)
        if 0x2F00 <= cp <= 0x2FDF or 0xF900 <= cp <= 0xFAFF:
            out.append(unicodedata.normalize('NFKC', ch))
        elif ch in RADICAL_SUPPLEMENT:
            out.append(RADICAL_SUPPLEMENT[ch])
        else:
            out.append(ch)
    return ''.join(out)


def estimate_tokens(text: str) -> int:
    """粗估 token 数：中日韩字符约 1.2 token/字，其余约 3.5 字符/token。"""
    cjk = len(CJK_RE.findall(text))
    other = len(re.sub(r'\s', '', text)) - cjk
    return int(cjk * 1.2 + other / 3.5)


def is_cjk_dominant(text: str) -> bool:
    sample = text[:200000]
    cjk = len(CJK_RE.findall(sample))
    letters = len(re.findall(r'[A-Za-z]', sample))
    return cjk >= letters / 4


def normalize(text: str):
    """归一化文本用于比对，返回 (归一化字符串, 每个字符对应原文下标的列表)。

    规则：去掉所有空白和换行；英文行末连字符断词合并；全角转半角（NFKC）；
    各种引号、破折号、省略号统一；英文转小写。
    """
    out, idx = [], []
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        if ch in DROP_CHARS:
            i += 1
            continue
        if ch == '-' and i + 1 < n and text[i + 1] == '\n' and out and out[-1].isalpha():
            i += 2
            continue
        if ch.isspace():
            i += 1
            continue
        if ch in RADICAL_SUPPLEMENT:
            m = RADICAL_SUPPLEMENT[ch]
        elif ch in QUOTE_MAP:
            m = QUOTE_MAP[ch]
        elif ch in DASH_CHARS:
            m = '-'
        elif ch == '…':
            m = '...'
        else:
            m = unicodedata.normalize('NFKC', ch)
        for c in m:
            if c.isspace():
                continue
            out.append(c.lower())
            idx.append(i)
        i += 1
    return ''.join(out), idx


class Book:
    """读取 prepare_book.py 生成的工作目录。"""

    def __init__(self, workdir):
        self.dir = Path(workdir)
        meta_path = self.dir / 'meta.json'
        if not meta_path.exists():
            raise SystemExit(f'找不到 {meta_path}，请先运行 prepare_book.py')
        self.meta = json.loads(meta_path.read_text(encoding='utf-8'))
        self.units = []
        with open(self.dir / 'pages.jsonl', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    self.units.append(json.loads(line))
        self.chapters = []
        tsv = self.dir / 'chapters.tsv'
        if tsv.exists():
            rows = tsv.read_text(encoding='utf-8').splitlines()
            head = rows[0].split('\t')
            for r in rows[1:]:
                vals = r.split('\t')
                self.chapters.append(dict(zip(head, vals)))
        self._index = None

    # ---------- 标签 ----------
    def unit_label(self, u, with_pdf=True):
        parts = []
        if u.get('page'):
            parts.append(f"{u['page']}页")
        if u.get('pdf') and (with_pdf or not u.get('page')):
            parts.append(f"PDF第{u['pdf']}页")
        if not parts:
            parts.append(u.get('loc') or f"位置{u['i'] + 1}")
        return ' / '.join(parts)

    def chapter_title(self, u):
        c = u.get('chapter')
        if c is None or not self.chapters:
            return ''
        for ch in self.chapters:
            if ch.get('idx') == str(c):
                return ch.get('title', '')
        return ''

    # ---------- 全书归一化索引 ----------
    def index(self):
        """返回 (全书归一化字符串, 各单元起点列表, 各单元的下标映射)。"""
        if self._index is None:
            parts, starts, maps = [], [], []
            pos = 0
            for u in self.units:
                norm, idx = normalize(u['text'])
                starts.append(pos)
                parts.append(norm)
                maps.append(idx)
                pos += len(norm)
            self._index = (''.join(parts), starts, maps)
        return self._index

    def unit_at(self, gpos):
        _, starts, _ = self.index()
        return max(0, bisect.bisect_right(starts, gpos) - 1)

    def original_snippet(self, gstart, gend, pad=0):
        """把归一化区间映射回原文片段（可跨页），返回去掉多余空白的原文。"""
        full, starts, maps = self.index()
        gstart = max(0, gstart - pad)
        gend = min(len(full), gend + pad)
        ua, ub = self.unit_at(gstart), self.unit_at(max(gstart, gend - 1))
        pieces = []
        for ui in range(ua, ub + 1):
            u, m = self.units[ui], maps[ui]
            if not m:
                continue
            ls = max(0, gstart - starts[ui]) if ui == ua else 0
            le = min(len(m), gend - starts[ui]) if ui == ub else len(m)
            if ls >= len(m) or le <= 0:
                continue
            o1 = m[ls]
            o2 = m[le - 1] + 1
            pieces.append(u['text'][o1:o2])
        text = ''.join(pieces)
        if self.meta.get('lang') == 'zh':
            text = re.sub(r'\s+', '', text)
        else:
            text = re.sub(r'\s+', ' ', text)
        return text

    # ---------- 页码 ----------
    def units_for_pages(self, p1, p2, slack=1):
        """返回印刷页码落在 [p1-slack, p2+slack] 的单元下标。p1/p2 可以是整数或罗马数字字符串。"""
        res = []
        for k, u in enumerate(self.units):
            pg = u.get('page')
            if pg is None:
                continue
            if isinstance(p1, int) and str(pg).isdigit():
                if p1 - slack <= int(pg) <= p2 + slack:
                    res.append(k)
            elif str(pg).lower() == str(p1).lower():
                res.append(k)
        return res

    def units_for_pdf_pages(self, p1, p2, slack=1):
        return [k for k, u in enumerate(self.units)
                if u.get('pdf') and p1 - slack <= u['pdf'] <= p2 + slack]


# ---------------------------------------------------------------- 知识库

def books_dir_of(root):
    """知识库根目录下的 books/；传入的就是 books/ 时原样返回。"""
    root = Path(root).expanduser()
    return root / 'books' if (root / 'books').is_dir() else root


def find_book_notes(root):
    """返回 [(书目录名, 笔记路径, 是否旧名 note.md)]。

    笔记正本是 books/<书名>/<书名>.md；2.2.0 之前叫 note.md，仍然认，但标为旧名。
    """
    out = []
    for d in sorted(p for p in books_dir_of(root).iterdir() if p.is_dir() and not p.name.startswith('.')):
        main = d / f'{d.name}.md'
        legacy = d / 'note.md'
        if main.exists():
            out.append((d.name, main, False))
        elif legacy.exists():
            out.append((d.name, legacy, True))
    return out
