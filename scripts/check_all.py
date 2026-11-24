#!/usr/bin/env python3
"""
check_all.py — 对知识库里所有已归档的笔记跑一遍结构与引文核对。

用法：
  python check_all.py <知识库目录>          # 目录下应有 books/<书名>/<书名>.md（旧名 note.md 也认）
  python check_all.py <知识库目录> --quotes  # 同时核对引文（慢，需要 source/）
  python check_all.py <知识库目录> --fix-list # 只列出有问题的书名，便于逐本处理

为什么需要它：笔记归档后是可以直接改的——在 Obsidian 里改、在编辑器里改都行。
改完跑这个脚本，如果全绿，这批笔记就还能被脚本归并进概念库；如果报错，按提示改回来。
它把「编辑有风险」变成「编辑完跑一下」。

每周加完一本书、或者准备做跨书归并之前，跑一次。
"""

import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from _common import books_dir_of, find_book_notes  # noqa: E402


def title_of(note):
    """从 frontmatter 里取书名，用于 verify_quotes 的 --title。"""
    text = note.read_text(encoding='utf-8')
    m = re.match(r'---[ \t]*\r?\n(.*?)\r?\n---[ \t]*', text, re.S)
    if m:
        t = re.search(r'^\s{2}title:\s*(.+?)\s*$', m.group(1), re.M)
        if t:
            return t.group(1).strip().strip('"\'')
    return note.parent.name


def run(cmd):
    p = subprocess.run([sys.executable] + cmd, capture_output=True, text=True)
    return p.returncode, (p.stdout or '') + (p.stderr or '')


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        raise SystemExit(__doc__)
    root = Path(args[0]).expanduser()
    do_quotes = '--quotes' in sys.argv
    fix_list = '--fix-list' in sys.argv

    books_dir = books_dir_of(root)
    found = find_book_notes(root)
    if not found:
        raise SystemExit(f'在 {books_dir} 下找不到任何 <书名>/<书名>.md')
    notes = [n for _, n, _ in found]
    legacy = [name for name, _, old in found if old]

    bad, details, no_source = [], [], []
    for note in notes:
        name = note.parent.name
        code, out = run([str(HERE / 'check_meta.py'), str(note)])
        problems = [] if code == 0 else ['结构']
        meta_out = out

        quote_out = ''
        if do_quotes:
            src = note.parent / 'source'
            if not (src / 'meta.json').exists():
                no_source.append(name)  # 没有原文就核对不了引文，这不算笔记出错，只提醒
            else:
                qcode, quote_out = run([str(HERE / 'verify_quotes.py'), str(note),
                                        str(src), '--title', title_of(note)])
                if qcode != 0:
                    problems.append('引文')

        if problems:
            bad.append(name)
            details.append((name, problems, meta_out, quote_out))
            print(f'  ✗ {name}　（{"、".join(problems)}）')
        else:
            print(f'  ✓ {name}' + ('　（无原文，未核对引文）' if name in no_source else ''))

    print(f'\n共 {len(notes)} 本，{len(notes) - len(bad)} 本通过。')
    if no_source:
        print(f'提示：{"、".join(no_source)} 没有归档原文（books/<书名>/source/），引文没法重新核对，'
              '只检查了结构。有原书文件时，重新预处理后把输出目录放进 source/ 即可。')
    if legacy:
        print(f'提示：{"、".join(legacy)} 的笔记还叫 note.md。运行 build_kb.py <知识库> --rename-notes 改成 <书名>.md。')
    if not bad:
        print('全部通过，可以归并进概念库。')
        return

    if fix_list:
        print('需要处理：' + '、'.join(bad))
        sys.exit(1)

    for name, problems, meta_out, quote_out in details:
        print(f'\n{"=" * 56}\n{name}\n{"=" * 56}')
        if '结构' in problems:
            print(meta_out.rstrip())
        if quote_out and '引文' in problems:
            # 引文报告很长，这里只给出结论段，详细报告已写进 source/verify_report.md
            head = [l for l in quote_out.split('\n') if l.startswith('共核对')]
            print('\n'.join(head) or quote_out[:600])
            print(f'（详细报告见 books/{name}/source/verify_report.md）')
    sys.exit(1)


if __name__ == '__main__':
    main()
