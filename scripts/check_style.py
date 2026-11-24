#!/usr/bin/env python3
"""
check_style.py — 检查读书笔记草稿的文风与格式。

用法：
  python check_style.py <草稿.md>

检查项（只针对“附录”之前的书评正文）：
  1. 套话与空泛拔高的说法（清单维护在 references/style.md 的 banned 区块里）
  2. 正文中出现列表、加粗、感叹号、小标题过多
  3. 引文页码格式：括注写在引号里面、半角括号、同一篇里混用多种页码写法
  4. 篇幅统计：正文字数、附录字数、最长段落
脚本只报告问题，不改文件。逐条判断：确属套话或格式问题就改，个别有实际含义的用法可以保留。
"""

import re
import sys
from pathlib import Path

STYLE_MD = Path(__file__).resolve().parent.parent / 'references' / 'style.md'


def load_banned():
    text = STYLE_MD.read_text(encoding='utf-8')
    m = re.search(r'<!-- banned:start -->(.*?)<!-- banned:end -->', text, re.S)
    items = []
    if not m:
        return items
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line.startswith('- '):
            continue
        entry = line[2:].split('｜')[0].strip()
        if not entry:
            continue
        if entry.startswith('re:'):
            items.append((entry[3:], re.compile(entry[3:])))
        else:
            for alt in entry.split('/'):
                alt = alt.strip()
                if alt:
                    items.append((alt, re.compile(re.escape(alt))))
    return items


def nonspace(s):
    return len(re.sub(r'\s', '', s))


QUOTED = re.compile(r'“[^”]{0,400}”|「[^」]{0,400}」')


def mask_quotes(line):
    """把引号里的原话换成等长的占位符：原书怎么写就是怎么写，套话和感叹号
    的检查不应落在引文上（列数不变，报错时的上下文位置仍然对得上）。"""
    return QUOTED.sub(lambda m: m.group(0)[0] + '·' * (len(m.group(0)) - 2) + m.group(0)[-1], line)


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    draft = Path(sys.argv[1]).read_text(encoding='utf-8')
    # frontmatter 归 check_meta.py 管，这里跳过，免得 YAML 的 "- name:" 被当成正文列表。
    # 换成同样行数的空行而不是直接删掉，这样报出的行号仍然对得上原文件。
    if draft.startswith('---'):
        m = re.match(r'---[ \t]*\r?\n.*?\r?\n---[ \t]*(?:\r?\n|$)', draft, re.S)
        if m:
            draft = '\n' * m.group(0).count('\n') + draft[m.end():]
    lines = draft.split('\n')
    app_line = None
    for k, l in enumerate(lines):
        if re.match(r'^#{1,3}\s*附录', l.strip()):
            app_line = k
            break
    body_lines = lines[:app_line] if app_line is not None else lines
    body = '\n'.join(body_lines)
    issues = []

    for phrase, rx in load_banned():
        for k, l in enumerate(body_lines):
            if l.lstrip().startswith('>'):
                continue  # 引文里的原话不算
            l = mask_quotes(l)  # 行内“……”引文同样不算
            for m in rx.finditer(l):
                a, b = max(0, m.start() - 12), min(len(l), m.end() + 12)
                issues.append((k + 1, '套话', f'“{m.group(0)}”：…{l[a:b]}…'))

    heads = 0
    for k, l in enumerate(body_lines):
        s = l.strip()
        if re.match(r'^#{2,4}\s', s):
            heads += 1
        if re.match(r'^([-*+]|\d+[.、)])\s', s):
            issues.append((k + 1, '格式', '正文里出现列表，请改写成段落（原书本身有编号时，用（1）（2）写进段落）'))
        if '**' in s:
            issues.append((k + 1, '格式', '正文里出现加粗，请去掉'))
        sm = mask_quotes(s)
        if '！' in sm or re.search(r'[一-鿿]!', sm):
            if not s.startswith('>'):
                issues.append((k + 1, '语气', '正文里出现感叹号（引文里的不算）'))
        if re.search(r'“[^”]*[（(]\s*(?:《[^》]*》\s*)?(?:第\s*)?\d+\s*(?:[-—–~～至]\s*\d+)?\s*页[^）)]*[）)]\s*”', s):
            issues.append((k + 1, '引文格式', '页码括注写在了引号里面，请挪到后引号之外'))
        if re.search(r'\((?:第\s*)?\d+\s*(?:[-—–]\s*\d+)?\s*页\)', s):
            issues.append((k + 1, '引文格式', '页码用了半角括号，请改为全角（）'))
    if heads > 6:
        issues.append((0, '结构', f'正文有 {heads} 个小标题，偏多；书评正文一般 3—4 个小标题，逐章部分不另设标题'))

    styles = set()
    for m in re.finditer(r'[（(]\s*(?:《[^》]*》\s*)?(第\s*)?\d+\s*(?:[-—–~～至]\s*\d+)?\s*页', body):
        styles.add('第X页' if m.group(1) else 'X页')
    if len(styles) > 1:
        issues.append((0, '引文格式', '同一篇里混用了“（第23页）”和“（23页）”两种写法，请统一为“（23页）”'))

    paras = [p for p in re.split(r'\n\s*\n', body) if p.strip() and not p.strip().startswith('#')]
    longest = max((nonspace(p) for p in paras), default=0)
    my = len(re.findall(r'我认为|我觉得|在我看来', body))
    author = len(re.findall(r'作者(认为|指出|提出|主张|强调|写道|的主要观点)', body))

    print(f'正文约 {nonspace(body):,} 字；附录约 {nonspace(chr(10).join(lines[app_line:])) if app_line is not None else 0:,} 字；'
          f'正文段落 {len(paras)} 个，最长 {longest} 字；小标题 {heads} 个。')
    print(f'归属标记：“作者认为/指出……” {author} 处，“我认为/我觉得” {my} 处。')
    if longest > 700:
        print('提示：有段落超过 700 字，考虑按意思拆开。')
    if not issues:
        print('未发现文风或格式问题。')
        return
    print(f'\n发现 {len(issues)} 处需要看一看：')
    for ln, kind, msg in issues:
        print(f'  [{kind}] ' + (f'第 {ln} 行：' if ln else '') + msg)
    sys.exit(1)


if __name__ == '__main__':
    main()
