#!/usr/bin/env python3
"""
archive_book.py — 把一本书的全部产物打包成知识库里的一个书目录，再连同脚本一起打成压缩包，
方便写进用户电脑上的知识库（Obsidian 仓库）。

用法：
  python archive_book.py <工作目录> <书名简称> --out <输出目录>
  python archive_book.py --tools-only --out <输出目录>     # 只打脚本包，用于更新知识库里的脚本

<工作目录> 是 prepare_book.py 的输出目录，里面应有 notes/draft.md、notes/cards/、
notes/book_map.md、notes/sources.md、notes/questions.md。

产出（都在 <输出目录> 里）：
  <书名简称>.tar.gz        解压到 <知识库>/books/ 下，得到：
                             <书名简称>/<书名简称>.md   笔记正本（draft.md 原样复制）
                             <书名简称>/cards/          章节卡片
                             <书名简称>/book_map.md、sources.md、questions.md
                             <书名简称>/source/         预处理产物（不含 notes/）
  lets-read-tools.tar.gz   解压到 <知识库>/.lets-read/ 下，得到 scripts/ 和 VERSION，
                           供在用户电脑上运行检查和合并
"""

import argparse
import json
import re
import shutil
import sys
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
NEED = ['notes/draft.md', 'notes/book_map.md', 'notes/sources.md', 'notes/questions.md']
SOURCE_KEEP = ['meta.json', 'pages.jsonl', 'chapters.tsv', 'book.txt', 'overview.md', 'chapters']


def plugin_version():
    for p in (HERE.parent / '.claude-plugin' / 'plugin.json', HERE.parent.parent.parent / '.claude-plugin' / 'plugin.json'):
        if p.exists():
            return json.loads(p.read_text(encoding='utf-8')).get('version', '?')
    return '?'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('workdir', nargs='?')
    ap.add_argument('name', nargs='?', help='书名简称，用作文件夹名和笔记文件名，如“全球猎身”')
    ap.add_argument('--out', required=True)
    ap.add_argument('--tools-only', action='store_true', help='只打脚本包')
    args = ap.parse_args()
    out = Path(args.out)
    if args.tools_only:
        out.mkdir(parents=True, exist_ok=True)
        pack_tools(out)
        return
    if not args.workdir or not args.name:
        raise SystemExit('需要 <工作目录> 和 <书名简称>；只更新脚本时用 --tools-only。')

    work = Path(args.workdir)
    name = args.name.strip()
    if not name or re.search(r'[\\/:*?"<>|#^\[\]]', name):
        raise SystemExit(f'书名简称「{name}」不能做文件名，请换一个（不要有 / : * ? " < > | # ^ [ ]）。')
    missing = [n for n in NEED if not (work / n).exists()] + ([] if (work / 'notes/cards').is_dir() else ['notes/cards/'])
    missing += [n for n in ('meta.json', 'pages.jsonl', 'chapters.tsv') if not (work / n).exists()]
    if missing:
        raise SystemExit('工作目录里缺这些文件，先补齐再归档：' + '、'.join(missing))

    stage = out / '_stage' / name
    if stage.parent.exists():
        shutil.rmtree(stage.parent)
    (stage / 'cards').mkdir(parents=True)
    (stage / 'source').mkdir()
    shutil.copy2(work / 'notes/draft.md', stage / f'{name}.md')
    for f in sorted((work / 'notes/cards').glob('*.md')):
        shutil.copy2(f, stage / 'cards' / f.name)
    for n in ('book_map.md', 'sources.md', 'questions.md'):
        shutil.copy2(work / 'notes' / n, stage / n)
    for n in SOURCE_KEEP:
        src = work / n
        if src.is_dir():
            shutil.copytree(src, stage / 'source' / n)
        elif src.exists():
            shutil.copy2(src, stage / 'source' / n)

    out.mkdir(parents=True, exist_ok=True)
    book_tar = out / f'{name}.tar.gz'
    with tarfile.open(book_tar, 'w:gz') as t:
        t.add(stage, arcname=name)
    shutil.rmtree(stage.parent)

    n_files = sum(1 for m in tarfile.open(book_tar).getmembers() if m.isfile())
    print(f'✅ {book_tar}（{n_files} 个文件）')
    pack_tools(out)


def pack_tools(out):
    tools_tar = out / 'lets-read-tools.tar.gz'
    ver = plugin_version()
    tmp = out / '_tools'
    if tmp.exists():
        shutil.rmtree(tmp)
    shutil.copytree(HERE, tmp / 'scripts', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    style = HERE.parent / 'references' / 'style.md'
    if style.exists():  # check_style.py 从 ../references/style.md 读套话清单
        (tmp / 'references').mkdir()
        shutil.copy2(style, tmp / 'references' / 'style.md')
    (tmp / 'VERSION').write_text(ver + '\n', encoding='utf-8')
    with tarfile.open(tools_tar, 'w:gz') as t:
        for p in sorted(tmp.iterdir()):
            t.add(p, arcname=p.name)
    shutil.rmtree(tmp)
    print(f'✅ {tools_tar}（脚本版本 {ver}）')


if __name__ == '__main__':
    main()
