#!/usr/bin/env python3
"""
locate.py — 在预处理后的书里查找关键词或句子，返回印刷页码、所在章节和原文上下文。

用法：
  python locate.py <工作目录> "可能性空间"            # 普通查找（忽略空白、全半角、引号差异）
  python locate.py <工作目录> "超稳定.{0,10}系统" --regex   # 正则查找（在去掉空白的文本上匹配）
  python locate.py <工作目录> "负反馈" --chapter 2 --max 10 --context 80

写读书笔记时，摘录引文、回查细节、确认某句话在哪一页，都先用它查，不凭记忆。
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import Book, normalize  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('workdir')
    ap.add_argument('query')
    ap.add_argument('--regex', action='store_true')
    ap.add_argument('--context', type=int, default=60, help='前后各显示多少字（默认 60）')
    ap.add_argument('--max', type=int, default=30, help='最多显示几条（默认 30）')
    ap.add_argument('--chapter', type=int, help='只在第几号章节（chapters.tsv 的 idx）里找')
    args = ap.parse_args()

    book = Book(args.workdir)
    full, starts, _ = book.index()
    if args.regex:
        pat = re.compile(args.query, re.I)
        hits = [(m.start(), m.end()) for m in pat.finditer(full)]
    else:
        q, _ = normalize(args.query)
        if not q:
            raise SystemExit('查询为空')
        hits, k = [], full.find(q)
        while k >= 0:
            hits.append((k, k + len(q)))
            k = full.find(q, k + 1)
    if args.chapter is not None:
        hits = [h for h in hits if book.units[book.unit_at(h[0])].get('chapter') == args.chapter]
    print(f'共 {len(hits)} 处' + (f'，显示前 {args.max} 处' if len(hits) > args.max else ''))
    for a, b in hits[:args.max]:
        ua, ub = book.unit_at(a), book.unit_at(max(a, b - 1))
        label = book.unit_label(book.units[ua])
        if ub != ua:
            label += ' → ' + book.unit_label(book.units[ub], with_pdf=False) + '（跨页）'
        title = book.chapter_title(book.units[ua])
        before = book.original_snippet(max(0, a - args.context), a)
        hit = book.original_snippet(a, b)
        after = book.original_snippet(b, min(len(full), b + args.context))
        src = '［OCR 文本，引用前请看原页图片核对］' if book.units[ua].get('src') == 'ocr' else ''
        print(f'\n[{label}]{"  " + title if title else ""}{src}')
        print(f'…{before}【{hit}】{after}…')


if __name__ == '__main__':
    main()
