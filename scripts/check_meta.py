#!/usr/bin/env python3
"""
check_meta.py — 校验读书笔记的 frontmatter 与附录二、三、四的结构。

用法：
  python check_meta.py <草稿.md> [--json]

正文怎么写都行，这个脚本不管；它只管那些日后要被脚本抽取、归并成跨书概念库的部分：

  1. frontmatter 存在、能解析、必填字段齐全、取值在允许的范围内
  2. 附录二是规定的四列；概念数 8—20；概念、界定、位置三列不留空；页码只写数字
  3. frontmatter.concepts 的标准名与附录二第一列逐行一致（含顺序）
  4. 附录三每条是「引文（页码）｜用途标签｜说明」三段式，标签在封闭词表内，条目数 10—30
  5. frontmatter.topics 与附录三的分组标题（#### 级）逐条一致（含顺序）
  6. 附录四是规定的五列，3—8 条，每列都填

报 ❌ 的是硬错误，必须改到通过；报 ⚠️ 的是提醒，自己判断。
规范见 references/appendix.md。
"""

import json
import re
import sys
from pathlib import Path

PURPOSE_TAGS = ['定义', '判据', '核心命题', '方法', '例证', '作者自述']
PAGE_SOURCES = ['印刷页码', 'PDF页序', '无']
READ_MODES = ['顺序通读', '并行阅读', '重点阅读']
BOOK_KEYS = ['title', 'author', 'publisher', 'year', 'edition', 'page_source']
READING_KEYS = ['date', 'mode', 'ocr', 'quotes_verified']
APPENDIX2_COLS = ['概念', '作者的界定或用法', '页码', '在全书中的位置']
APPENDIX4_COLS = ['文献', '作者', '出版信息', '年份', '与本书的关系']

CONCEPT_MIN, CONCEPT_MAX = 8, 20
QUOTE_MIN, QUOTE_MAX = 10, 30
TOPIC_MIN, TOPIC_MAX = 3, 6
FURTHER_MIN, FURTHER_MAX = 3, 8

# 页码列：数字和分隔符，另允许「序一13」「序二46—51」这类带前置部分标签的写法
PAGE_OK = re.compile(r'^(?:[序前导附][^0-9；;]{0,3})?[0-9\s、，,；;—–‒−\-~～至]'
                     r'(?:[0-9\s、，,；;—–‒−\-~～至]|[序前导附][^0-9；;]{0,3})*$')
# 主题标题前的序号（「一、去嵌入与抽象化」→「去嵌入与抽象化」），比对时忽略
TOPIC_ORD = re.compile(r'^(?:[（(][一二三四五六七八九十\d]+[）)]\s*[、.．:：]?'
                       r'|[一二三四五六七八九十\d]+\s*[、.．:：])\s*(?=\S)')


def cn(n):
    return '零一二三四五六七八九十'[n] if 0 <= n <= 10 else str(n)


# ---------------------------------------------------------------- frontmatter

def split_frontmatter(text):
    """返回 (frontmatter 文本, 正文)；没有 frontmatter 时前者为 None。"""
    if not text.startswith('---'):
        return None, text
    m = re.match(r'---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)', text, re.S)
    if not m:
        return None, text
    return m.group(1), text[m.end():]


def parse_yaml(src):
    """解析 frontmatter。优先用 PyYAML；没装就用够这套固定格式的简化解析。"""
    try:
        import yaml
        return yaml.safe_load(src) or {}
    except ImportError:
        return _mini_yaml(src)


def _scalar(s):
    s = s.strip()
    if not s or s in ('null', '~'):
        return None
    if s in ('true', 'True'):
        return True
    if s in ('false', 'False'):
        return False
    if re.fullmatch(r'-?\d+', s):
        return int(s)
    if s.startswith('[') and s.endswith(']'):
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [_scalar(x) for x in inner.split(',')]
    if len(s) >= 2 and s[0] == s[-1] and s[0] in '"\'':
        return s[1:-1]
    return s


def _mini_yaml(src):
    """没有 PyYAML 时的备用解析器，只认本 skill 用到的形状：
    两层映射、字符串列表（行内或多行）、映射列表。"""
    lines = [l for l in src.split('\n') if l.strip() and not l.strip().startswith('#')]
    if not lines:
        return {}
    pos = [0]

    def indent_of(l):
        return len(l) - len(l.lstrip(' '))

    def parse_block(base):
        if pos[0] >= len(lines):
            return None
        if lines[pos[0]].strip().startswith('- '):
            out = []
            while pos[0] < len(lines):
                l = lines[pos[0]]
                if indent_of(l) != base or not l.strip().startswith('- '):
                    break
                item = l.strip()[2:].strip()
                pos[0] += 1
                if ':' in item and item[0] not in '[\'"':
                    k, _, v = item.partition(':')
                    d = {k.strip(): _scalar(v)}
                    # 同一条目下缩进更深的后续键
                    while pos[0] < len(lines) and indent_of(lines[pos[0]]) > base \
                            and not lines[pos[0]].strip().startswith('- '):
                        k2, _, v2 = lines[pos[0]].strip().partition(':')
                        pos[0] += 1
                        if k2:
                            d[k2.strip()] = _scalar(v2)
                    out.append(d)
                else:
                    out.append(_scalar(item))
            return out
        out = {}
        while pos[0] < len(lines):
            l = lines[pos[0]]
            if indent_of(l) != base or ':' not in l.strip():
                break
            pos[0] += 1
            k, _, v = l.strip().partition(':')
            if v.strip():
                out[k.strip()] = _scalar(v)
            else:
                nxt = indent_of(lines[pos[0]]) if pos[0] < len(lines) else -1
                out[k.strip()] = parse_block(nxt) if nxt > base else None
        return out

    return parse_block(indent_of(lines[0]))


# -------------------------------------------------------------------- 附录定位

def section(body, ordinal):
    """取出「附录X」这一节的内容。

    到下一个「附录」标题（同级或更高级）为止，或到更高一级的标题为止。
    这样即使主题分组标题和「附录三」写成了同一级，也仍然算在附录三里面。
    """
    pat = re.compile(r'^(#{1,4})\s*附录\s*' + ordinal + r'(?![一二三四五六七八九十])', re.M)
    m = pat.search(body)
    if not m:
        return None
    level = len(m.group(1))
    rest = body[m.end():]
    ends = [x.start() for x in re.finditer(r'^#{1,%d}\s*附录' % level, rest, re.M)]
    if level > 1:
        ends += [x.start() for x in re.finditer(r'^#{1,%d}\s' % (level - 1), rest, re.M)]
    return rest[:min(ends)] if ends else rest


def parse_table(src):
    """解析 markdown 表格，返回 (表头, 数据行)；找不到返回 (None, [])。"""
    rows = []
    for line in src.split('\n'):
        s = line.strip()
        if not s.startswith('|'):
            if rows:
                break
            continue
        cells = [c.strip() for c in s.strip('|').split('|')]
        rows.append(cells)
    if len(rows) < 2:
        return None, []
    sep = rows[1]
    if not all(re.fullmatch(r':?-{2,}:?', c) for c in sep):
        return None, []
    return rows[0], rows[2:]


# ---------------------------------------------------------------------- 校验

class Report:
    def __init__(self):
        self.errors, self.warnings, self.info = [], [], []

    def err(self, where, msg):
        self.errors.append((where, msg))

    def warn(self, where, msg):
        self.warnings.append((where, msg))


def check(path):
    r = Report()
    text = Path(path).read_text(encoding='utf-8')
    fm_src, body = split_frontmatter(text)

    fm = {}
    if fm_src is None:
        r.err('frontmatter', '文件开头没有 frontmatter。要在第一行写 ---，中间是 YAML，再用一行 --- 收尾（字段表见 references/appendix.md 开头）')
    else:
        try:
            fm = parse_yaml(fm_src)
        except Exception as e:
            r.err('frontmatter', f'YAML 解析失败：{e}')
            fm = {}
        if not isinstance(fm, dict):
            r.err('frontmatter', 'frontmatter 不是一组键值对')
            fm = {}

    book = fm.get('book') or {}
    reading = fm.get('reading') or {}
    if fm_src is not None:
        if not isinstance(book, dict):
            r.err('frontmatter', 'book 不是一组键值对')
            book = {}
        if not isinstance(reading, dict):
            r.err('frontmatter', 'reading 不是一组键值对')
            reading = {}
        for k in BOOK_KEYS:
            if book.get(k) in (None, ''):
                r.err('frontmatter', f'book.{k} 缺失或为空')
        for k in READING_KEYS:
            if reading.get(k) is None:
                r.err('frontmatter', f'reading.{k} 缺失')
        ps = book.get('page_source')
        if ps is not None and ps not in PAGE_SOURCES:
            r.err('frontmatter', f'book.page_source 写的是「{ps}」，只能是：{" / ".join(PAGE_SOURCES)}')
        mode = reading.get('mode')
        if mode is not None and mode not in READ_MODES:
            r.err('frontmatter', f'reading.mode 写的是「{mode}」，只能是：{" / ".join(READ_MODES)}')
        if mode == '重点阅读' and not reading.get('intensive'):
            r.err('frontmatter', 'reading.mode 是重点阅读，但 reading.intensive 没有列出精读的章序号')
        for k in ('ocr', 'quotes_verified'):
            if reading.get(k) is not None and not isinstance(reading.get(k), bool):
                r.err('frontmatter', f'reading.{k} 要写 true 或 false')
        d = reading.get('date')
        if d is not None and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(d)):
            r.err('frontmatter', f'reading.date 要写成 2026-10-09 的样子，现在是「{d}」')

    # ---- 附录二 ----
    fm_concepts = fm.get('concepts') or []
    a2 = section(body, '二')
    a2_names = []
    if a2 is None:
        r.err('附录二', '找不到「附录二」标题')
    else:
        head, rows = parse_table(a2)
        if head is None:
            r.err('附录二', '没找到表格，或表格缺少 |---|---| 分隔行')
        elif head != APPENDIX2_COLS:
            r.err('附录二', f'表头必须是这{cn(len(APPENDIX2_COLS))}列，顺序一致：{" | ".join(APPENDIX2_COLS)}；现在是：{" | ".join(head)}')
        else:
            rows = [x for x in rows if any(c for c in x)]
            if not (CONCEPT_MIN <= len(rows) <= CONCEPT_MAX):
                r.err('附录二', f'有 {len(rows)} 个概念，要求 {CONCEPT_MIN}—{CONCEPT_MAX} 个')
            for i, row in enumerate(rows, 1):
                if len(row) != 4:
                    r.err('附录二', f'第 {i} 行有 {len(row)} 格，应为 4 格')
                    continue
                name, desc, pages, place = row
                a2_names.append(name)
                for col, v in (('概念', name), ('作者的界定或用法', desc), ('在全书中的位置', place)):
                    if not v:
                        r.err('附录二', f'第 {i} 行（{name or "无名"}）的「{col}」是空的')
                if book.get('page_source') != '无':
                    if not pages:
                        r.err('附录二', f'第 {i} 行（{name}）没写页码')
                    elif not PAGE_OK.fullmatch(pages):
                        r.err('附录二', f'第 {i} 行（{name}）的页码写成了「{pages}」，只写数字和、—，不要「第」「页」')

    if fm_src is not None:
        if not isinstance(fm_concepts, list) or not fm_concepts:
            r.err('frontmatter', 'concepts 缺失或为空')
        else:
            fm_names = []
            for i, c in enumerate(fm_concepts, 1):
                if not isinstance(c, dict) or not c.get('name'):
                    r.err('frontmatter', f'concepts 第 {i} 条缺 name')
                    continue
                fm_names.append(c['name'])
                if 'aliases' not in c:
                    r.err('frontmatter', f'concepts「{c["name"]}」缺 aliases 键（没有别名就写 []）')
                canon = c.get('canonical')
                if not canon:
                    r.err('frontmatter', f'concepts「{c["name"]}」缺 canonical（归并名；本书自创的概念就写同一个词）')
                elif re.search(r'[/／、,，]', str(canon)):
                    r.err('frontmatter', f'canonical「{canon}」里有斜杠或顿号。并列名跨书对不上，'
                                         f'选一个做归并名，其余放 aliases（name 里保留作者的并列写法即可）')
            if a2_names and fm_names != a2_names:
                only_fm = [x for x in fm_names if x not in a2_names]
                only_a2 = [x for x in a2_names if x not in fm_names]
                if only_fm or only_a2:
                    r.err('一致性', 'frontmatter.concepts 与附录二的概念对不上'
                                    + (f'；只在 frontmatter 里：{"、".join(only_fm)}' if only_fm else '')
                                    + (f'；只在附录二里：{"、".join(only_a2)}' if only_a2 else ''))
                else:
                    r.err('一致性', 'frontmatter.concepts 与附录二的概念相同但顺序不同，要逐行对应')
            empty_alias = sum(1 for c in fm_concepts
                              if isinstance(c, dict) and not c.get('aliases'))
            if fm_concepts and empty_alias == len(fm_concepts):
                r.warn('frontmatter', '所有概念的 aliases 都是空的。外文概念的原文词、其他译法都该填上，否则跨书归并时并不到一起')
            renamed = sum(1 for c in fm_concepts if isinstance(c, dict)
                          and c.get('canonical') and c.get('canonical') != c.get('name'))
            if fm_concepts and renamed == len(fm_concepts):
                r.warn('frontmatter', '每一个概念的 canonical 都和 name 不同。本书自创的概念（作者的发明）'
                                      '应当两者相同，检查一下是不是把作者的说法也换掉了')

    # ---- 附录三 ----
    fm_topics = fm.get('topics') or []
    a3 = section(body, '三')
    a3_topics, n_quotes, tags_seen = [], 0, set()
    if a3 is None:
        r.err('附录三', '找不到「附录三」标题')
    else:
        for line in a3.split('\n'):
            s = line.strip()
            if re.match(r'#{1,6}\s*\S', s):
                a3_topics.append(TOPIC_ORD.sub('', s.lstrip('#').strip()))
                continue
            if not s.startswith('- '):
                continue
            n_quotes += 1
            item = s[2:].strip()
            parts = [p.strip() for p in item.split('｜')]
            if len(parts) != 3:
                r.err('附录三', f'这条不是三段式「引文（页码）｜用途｜说明」，用全角竖线隔开：{item[:46]}…')
                continue
            quote, tag, note = parts
            if tag not in PURPOSE_TAGS:
                r.err('附录三', f'用途标签「{tag}」不在词表内，只能是：{" / ".join(PURPOSE_TAGS)}')
            else:
                tags_seen.add(tag)
            if not note:
                r.err('附录三', f'这条缺第三段的说明：{quote[:30]}…')
            if book.get('page_source') != '无' and not re.search(r'[（(][^）)]*\d+\s*页[^）)]*[）)]', quote):
                r.err('附录三', f'这条引文后面没有「（23页）」式的页码：{quote[:36]}…')
        if not (QUOTE_MIN <= n_quotes <= QUOTE_MAX):
            r.err('附录三', f'有 {n_quotes} 条引文，要求 {QUOTE_MIN}—{QUOTE_MAX} 条')
        if not (TOPIC_MIN <= len(a3_topics) <= TOPIC_MAX):
            if not a3_topics:
                r.err('附录三', '附录三下面没有主题分组标题。每组用一个 #### 标题开头（比“附录三”低一级），'
                               f'分 {TOPIC_MIN}—{TOPIC_MAX} 组')
            else:
                r.err('附录三', f'有 {len(a3_topics)} 个主题分组，要求 {TOPIC_MIN}—{TOPIC_MAX} 个')
        for t in ('定义', '核心命题'):
            if n_quotes and t not in tags_seen:
                r.warn('附录三', f'一条「{t}」标签的引文都没有。写论文时这类引文最常用，回卡片里找找')

    if fm_src is not None and a3_topics:
        if not isinstance(fm_topics, list) or not fm_topics:
            r.err('frontmatter', 'topics 缺失或为空')
        elif [TOPIC_ORD.sub('', str(t)) for t in fm_topics] != a3_topics:
            r.err('一致性', 'frontmatter.topics 与附录三的分组标题对不上，两处要逐条一致（含顺序）：\n'
                            f'      frontmatter：{" / ".join(map(str, fm_topics))}\n'
                            f'      附录三　　：{" / ".join(a3_topics)}')

    # ---- 附录四 ----
    a4 = section(body, '四')
    if a4 is None:
        r.err('附录四', '找不到「附录四」标题（延伸阅读现在是必有项）')
    else:
        head, rows = parse_table(a4)
        if head is None:
            r.err('附录四', '没找到表格，或表格缺少 |---|---| 分隔行')
        elif head != APPENDIX4_COLS:
            r.err('附录四', f'表头必须是这{cn(len(APPENDIX4_COLS))}列，顺序一致：{" | ".join(APPENDIX4_COLS)}；现在是：{" | ".join(head)}')
        else:
            rows = [x for x in rows if any(c for c in x)]
            if not (FURTHER_MIN <= len(rows) <= FURTHER_MAX):
                r.err('附录四', f'有 {len(rows)} 条，要求 {FURTHER_MIN}—{FURTHER_MAX} 条')
            for i, row in enumerate(rows, 1):
                if len(row) != 5:
                    r.err('附录四', f'第 {i} 行有 {len(row)} 格，应为 5 格')
                    continue
                for col, v in zip(APPENDIX4_COLS, row):
                    if not v:
                        r.err('附录四', f'第 {i} 行（{row[0] or "无题"}）的「{col}」是空的')
                if not re.fullmatch(r'\s*\d{3,4}\s*', row[3]):
                    r.err('附录四', f'第 {i} 行（{row[0]}）的年份写成了「{row[3]}」，只写四位数字')

    # ---- 附录一 ----
    if section(body, '一') is None:
        r.err('附录一', '找不到「附录一」标题')

    stats = {
        'concepts': len(a2_names),
        'renamed': sum(1 for c in (fm_concepts or []) if isinstance(c, dict)
                       and c.get('canonical') and c.get('canonical') != c.get('name')),
        'quotes': n_quotes,
        'topics': len(a3_topics),
        'tags': sorted(tags_seen),
        'title': book.get('title'),
    }
    return r, stats


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    as_json = '--json' in sys.argv
    if not args:
        raise SystemExit(__doc__)
    r, stats = check(args[0])

    if as_json:
        print(json.dumps({
            'ok': not r.errors,
            'errors': [{'where': w, 'msg': m} for w, m in r.errors],
            'warnings': [{'where': w, 'msg': m} for w, m in r.warnings],
            'stats': stats,
        }, ensure_ascii=False, indent=2))
        sys.exit(1 if r.errors else 0)

    print(f'《{stats["title"] or "未命名"}》：核心概念 {stats["concepts"]} 个'
          f'（其中 {stats["renamed"]} 个的归并名与作者说法不同）；'
          f'主题引文 {stats["quotes"]} 条，分 {stats["topics"]} 组；'
          f'用途标签用到 {len(stats["tags"])} 种（{"、".join(stats["tags"]) or "无"}）。')
    for where, msg in r.warnings:
        print(f'  ⚠️  [{where}] {msg}')
    if not r.errors:
        print('结构校验通过，这份笔记可以归并进概念库。')
        return
    print(f'\n{len(r.errors)} 处必须改：')
    for where, msg in r.errors:
        print(f'  ❌ [{where}] {msg}')
    sys.exit(1)


if __name__ == '__main__':
    main()
