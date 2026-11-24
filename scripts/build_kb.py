#!/usr/bin/env python3
"""
build_kb.py — 把知识库里各本书的笔记合并成跨书的概念页、总目录和待读清单。

用法：
  python build_kb.py <知识库目录>                 # 合并，写出概念页、index.md、待读.md，并在 log.md 追加一条
  python build_kb.py <知识库目录> --dry-run       # 只报告会做什么，不写文件
  python build_kb.py <知识库目录> --rename-notes  # 把旧名 books/<书名>/note.md 改成 books/<书名>/<书名>.md

读什么：books/<书名>/<书名>.md 的 frontmatter（concepts、topics、book）、附录二、附录三、附录四。
没通过 check_meta.py 的笔记不参与合并，并在报告里列出。

写什么：
  concepts/<归并名>.md   每个概念一页。只重写两行标记之间的“生成区”，以及 frontmatter 里的
                         aliases、type、books 三个键；你写的“综述”和你加的其他属性都不动。
                         如果标记被删掉了，这一页整页跳过，不会覆盖。
  index.md               总目录（整页生成，不要手改）
  待读.md                各书附录四推荐、你还没读的书（整页生成，不要手改）
  log.md                 每次合并追加一行（只追加）
  知识库说明.md          第一次运行时生成；之后不再改动

报告里还会列出：疑似重复的归并名、同一个别名挂在两个概念下、已经没有书引用的概念页。
"""

import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from _common import books_dir_of, find_book_notes  # noqa: E402
from check_meta import check as check_note, parse_table, parse_yaml, section, split_frontmatter, TOPIC_ORD  # noqa: E402

AUTO_START = '<!-- lets-read:auto:start 本区由 build_kb.py 生成，每次合并都会重写，请不要在这里修改 -->'
AUTO_END = '<!-- lets-read:auto:end -->'
AUTO_START_RE = re.compile(r'<!--\s*lets-read:auto:start.*?-->')
AUTO_END_RE = re.compile(r'<!--\s*lets-read:auto:end\s*-->')
MANAGED_KEYS = ('aliases', 'type', 'books')
BAD_FILENAME = re.compile(r'[\\/:*?"<>|#^\[\]]')

GUIDE = """# 知识库说明

这个知识库由 Let's Read 插件维护。读完一本书，笔记会放进 `books/<书名>/`；你说“检查一下”时，Claude 会核对笔记，再把各书的概念合并到 `concepts/`。

## 哪些可以随便改

- `books/<书名>/<书名>.md`：读书笔记正本。正文、批注随便改。开头的 frontmatter 和附录二、三、四是给脚本读的，改的时候保持原有格式；改坏了，检查会指出是哪一行。
- `books/<书名>/questions.md`：疑问与批注。“后来的批注”一节专门留给你。
- `books/<书名>/cards/`、`book_map.md`、`sources.md`：读书时的工作笔记，可以改。
- `concepts/<概念>.md` 里“综述”一节及以下：留给你写，合并时不会动。你也可以给概念页加自己的属性和标签。
- 合并时如果提醒两个概念“很像”，而你确认它们不是同一个概念，就在其中一页的属性里加 `distinct_from: [另一个概念]`，以后不再提醒。

## 哪些不要手改

- 概念页里两行 `lets-read:auto` 标记之间的部分，以及 frontmatter 里的 `aliases`、`type`、`books`：每次合并都会重写。想改某个概念的说法，去改对应那本书的笔记。
- `index.md`、`待读.md`：整页生成。
- `books/<书名>/source/`：预处理后的原文，核对引文和回查原文要用，不要改，也不要删。

## 改完之后

对 Claude 说“检查一下”。它会核对格式和引文，然后更新概念页、总目录和待读清单，并在 `log.md` 记一笔。
"""


# ---------------------------------------------------------------- 读笔记

def table_rows(src):
    head, rows = parse_table(src or '')
    return head, [r for r in rows if any(c for c in r)]


def load_book(dirname, path):
    text = path.read_text(encoding='utf-8')
    fm_src, body = split_frontmatter(text)
    fm = parse_yaml(fm_src) if fm_src else {}
    fm = fm if isinstance(fm, dict) else {}
    book = fm.get('book') or {}
    reading = fm.get('reading') or {}

    concepts = []
    _, a2 = table_rows(section(body, '二'))
    fm_concepts = [c for c in (fm.get('concepts') or []) if isinstance(c, dict)]
    for i, row in enumerate(a2):
        if len(row) != 4:
            continue
        name, desc, pages, place = row
        meta = fm_concepts[i] if i < len(fm_concepts) else {}
        concepts.append({
            'name': name, 'desc': desc, 'pages': pages, 'place': place,
            'canonical': str(meta.get('canonical') or name).strip(),
            'aliases': [str(a).strip() for a in (meta.get('aliases') or []) if str(a).strip()],
        })

    quotes, topic = [], None
    for line in (section(body, '三') or '').split('\n'):
        s = line.strip()
        if re.match(r'#{1,6}\s*\S', s):
            topic = TOPIC_ORD.sub('', s.lstrip('#').strip())
        elif s.startswith('- ') and s.count('｜') >= 2:
            q, tag, note = [p.strip() for p in s[2:].split('｜', 2)]
            quotes.append({'quote': q, 'tag': tag, 'note': note, 'topic': topic})

    _, a4 = table_rows(section(body, '四'))
    further = [dict(zip(['title', 'author', 'pub', 'year', 'rel'], r)) for r in a4 if len(r) == 5]

    return {
        'dir': dirname, 'path': path, 'title': str(book.get('title') or dirname),
        'author': str(book.get('author') or ''), 'year': book.get('year') or '',
        'date': str(reading.get('date') or ''), 'verified': reading.get('quotes_verified'),
        'topics': [str(t) for t in (fm.get('topics') or [])],
        'concepts': concepts, 'quotes': quotes, 'further': further,
    }


def link_for(b):
    """书的链接：笔记文件叫 <书名>.md 时直接 [[书名]]，旧名 note.md 时带路径。"""
    if b['path'].name == f"{b['dir']}.md":
        return f"[[{b['dir']}]]"
    return f"[[books/{b['dir']}/note|{b['dir']}]]"


# ---------------------------------------------------------------- 概念页

def split_page(text):
    """返回 (frontmatter 文本或 None, 生成区之前, 生成区之后)；没有标记时后两项为 None。"""
    fm_src, body = split_frontmatter(text)
    s, e = AUTO_START_RE.search(body), AUTO_END_RE.search(body)
    if not s or not e or e.start() < s.end():
        return fm_src, None, None
    return fm_src, body[:s.start()], body[e.end():]


def yaml_list(items):
    def q(x):
        x = str(x)
        return x if re.fullmatch(r'[^\s:#,\[\]{}&*!|>\'"%@`]+', x) else '"' + x.replace('"', '\\"') + '"'
    return '[' + ', '.join(q(i) for i in items) + ']'


def merge_frontmatter(old_src, managed):
    """只替换顶层的 aliases / type / books 三个键，其余行原样保留。"""
    lines = (old_src or '').split('\n') if old_src else []
    blocks, cur = [], None
    for ln in lines:
        m = re.match(r'^([^\s#][^:]*):', ln)
        if m:
            cur = [m.group(1).strip(), [ln]]
            blocks.append(cur)
        elif cur is not None:
            cur[1].append(ln)
        else:
            blocks.append([None, [ln]])
    out, seen = [], set()
    for key, ls in blocks:
        if key in managed:
            out.append(f'{key}: {managed[key]}')
            seen.add(key)
        else:
            out.extend(ls)
    for k in MANAGED_KEYS:
        if k not in seen:
            out.append(f'{k}: {managed[k]}')
    return '\n'.join(l for l in out if l is not None).strip('\n')


def mentions(quote, words):
    return any(w and len(w) >= 2 and w in quote for w in words)


def auto_zone(canon, entries, quotes):
    L = [AUTO_START, '', '## 各书中的说法', '']
    if not entries:
        L += ['目前没有哪本书的笔记用到这个概念（可能是改了归并名或删掉了）。确认没用后，可以把这一页删掉。', '']
    for b, c in entries:
        L.append(f"### {link_for(b)}")
        L.append(f"- 作者的说法：{c['name']}")
        L.append(f"- 界定或用法：{c['desc']}")
        if c['pages']:
            L.append(f"- 页码：{c['pages']}")
        L.append(f"- 在全书中的位置：{c['place']}")
        if b['verified'] is False:
            L.append('- 注意：这本书的笔记没有对照原文核对引文。')
        L.append('')
    if quotes:
        L += ['## 提到它的引文', '']
        for b, q in quotes:
            L.append(f"- {link_for(b)}：{q['quote']}｜{q['tag']}｜{q['note']}")
        L.append('')
    L.append(AUTO_END)
    return '\n'.join(L)


def new_page(canon, fm_text, zone):
    return (f"---\n{fm_text}\n---\n\n# {canon}\n\n{zone}\n\n## 综述\n\n"
            "（这一节留给你写，也可以请 Claude 依据上面各书的说法起草。合并时不会改动这一节及以下的内容。）\n")


def known_distinct(existing):
    """读取概念页属性里的 distinct_from：用户确认过“不是同一个概念”的组合，不再提醒。"""
    pairs = set()
    for stem, path in existing.items():
        fm_src, _ = split_frontmatter(path.read_text(encoding='utf-8'))
        if not fm_src:
            continue
        try:
            fm = parse_yaml(fm_src) or {}
        except Exception:
            continue
        others = fm.get('distinct_from') if isinstance(fm, dict) else None
        if isinstance(others, str):
            others = [others]
        for o in others or []:
            pairs.add(frozenset((stem, str(o).strip())))
    return pairs


# ---------------------------------------------------------------- 主流程

def norm_title(t):
    return re.sub(r'[《》\s"“”\'‘’:：·・,，.。]', '', str(t)).lower()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        raise SystemExit(__doc__)
    root = Path(args[0]).expanduser()
    dry = '--dry-run' in sys.argv
    kb = root.parent if root.name == 'books' and not (root / 'books').is_dir() else root
    books_dir = books_dir_of(kb)
    if not books_dir.is_dir() or books_dir == kb:
        raise SystemExit(f'{kb} 下没有 books/ 文件夹。知识库的结构是 <知识库>/books/<书名>/<书名>.md')

    if '--rename-notes' in sys.argv:
        n = 0
        for name, path, old in find_book_notes(kb):
            if old:
                target = path.parent / f'{name}.md'
                print(f'  {path.relative_to(kb)} → {target.relative_to(kb)}')
                if not dry:
                    path.rename(target)
                n += 1
        print(f'改名 {n} 本。' if n else '没有需要改名的笔记。')
        return

    found = find_book_notes(kb)
    if not found:
        raise SystemExit(f'在 {books_dir} 下找不到任何 <书名>/<书名>.md')

    books, skipped = [], []
    for name, path, old in found:
        rep, _ = check_note(path)
        if rep.errors:
            skipped.append((name, len(rep.errors)))
            continue
        books.append(load_book(name, path))

    # ---- 按归并名汇总 ----
    by_canon = defaultdict(list)
    for b in books:
        for c in b['concepts']:
            by_canon[c['canonical']].append((b, c))

    alias_owner = defaultdict(set)
    for canon, items in by_canon.items():
        for b, c in items:
            for w in [c['name'], *c['aliases']]:
                if w != canon:
                    alias_owner[w].add(canon)

    concepts_dir = kb / 'concepts'
    written, created, kept, orphaned = [], [], [], []
    warn = []
    bad_names = [c for c in by_canon if BAD_FILENAME.search(c)]
    for c in bad_names:
        warn.append(f'归并名「{c}」含有不能做文件名的字符，这个概念没有生成页面。请改 canonical。')
    for b in books:
        if b['dir'] in by_canon:
            warn.append(f'概念「{b["dir"]}」和书名同名，[[{b["dir"]}]] 会链到哪一页说不准。请换一个归并名。')

    # 跳过 ._ 开头的文件：外接硬盘（exFAT 等）上 macOS 会为每个文件生成一个同名的 ._ 隐藏文件
    existing = {p.stem: p for p in concepts_dir.glob('*.md') if not p.name.startswith('.')} if concepts_dir.is_dir() else {}
    pages = {}
    for canon, items in sorted(by_canon.items()):
        if canon in bad_names:
            continue
        aliases = []
        for b, c in items:
            for w in [c['name'], *c['aliases']]:
                if w and w != canon and w not in aliases:
                    aliases.append(w)
        words = [canon, *aliases]
        qs = [(b, q) for b in {id(b): b for b, _ in items}.values() for q in b['quotes'] if mentions(q['quote'], words)]
        book_names = []
        for b, _ in items:
            if b['dir'] not in book_names:
                book_names.append(b['dir'])
        managed = {'aliases': yaml_list(aliases), 'type': '概念', 'books': yaml_list(book_names)}
        zone = auto_zone(canon, items, qs)
        pages[canon] = (managed, zone)

    for canon, (managed, zone) in pages.items():
        path = concepts_dir / f'{canon}.md'
        if path.exists():
            text = path.read_text(encoding='utf-8')
            fm_src, before, after = split_page(text)
            if before is None:
                kept.append(canon)
                continue
            new = f"---\n{merge_frontmatter(fm_src, managed)}\n---\n{before}{zone}{after}"
            if new != text:
                written.append(canon)
                if not dry:
                    path.write_text(new, encoding='utf-8')
        else:
            created.append(canon)
            if not dry:
                concepts_dir.mkdir(parents=True, exist_ok=True)
                fm_text = '\n'.join(f'{k}: {managed[k]}' for k in MANAGED_KEYS)
                path.write_text(new_page(canon, fm_text, zone), encoding='utf-8')

    # 没有书再用到的概念页：生成区改成提示，不删除
    for stem, path in existing.items():
        if stem in pages:
            continue
        text = path.read_text(encoding='utf-8')
        fm_src, before, after = split_page(text)
        if before is None:
            continue
        orphaned.append(stem)
        zone = auto_zone(stem, [], [])
        new = f"---\n{merge_frontmatter(fm_src, {'aliases': '[]', 'type': '概念', 'books': '[]'})}\n---\n{before}{zone}{after}"
        if new != text and not dry:
            path.write_text(new, encoding='utf-8')

    # ---- 体检 ----
    for w, owners in sorted(alias_owner.items()):
        if len(owners) > 1:
            warn.append(f'别名「{w}」同时挂在 {"、".join(sorted(owners))} 下，可能是同一个概念，或者要换个别名。')
    canons = sorted(pages)
    distinct = known_distinct(existing)
    for i, a in enumerate(canons):
        for b in canons[i + 1:]:
            if frozenset((a, b)) in distinct:
                continue
            if len(a) >= 2 and len(b) >= 2 and (a in b or b in a):
                warn.append(f'归并名「{a}」和「{b}」很像，确认是不是同一个概念。'
                            f'确认不是的话，在「{a}」页的属性里加 distinct_from: [{b}]，以后就不再提醒。')
            elif a in alias_owner and b in alias_owner.get(a, set()) or b in alias_owner and a in alias_owner.get(b, set()):
                warn.append(f'「{a}」是「{b}」的别名，又自成一个概念页，确认是否要合并。'
                            f'确认不合并的话，在「{a}」页的属性里加 distinct_from: [{b}]。')

    # ---- index.md ----
    I = ['# 读书库总目录', '', '本页由 build_kb.py 生成，每次合并都会重写，请不要手改。', '',
         f'共 {len(books)} 本书，{len(pages)} 个概念。上次合并：{datetime.now():%Y-%m-%d %H:%M}。', '',
         '## 书目', '', '| 书 | 作者 | 出版年 | 读完日期 | 概念数 | 引文已核对 | 疑问与批注 |', '|---|---|---|---|---|---|---|']
    for b in sorted(books, key=lambda x: x['date'], reverse=True):
        qlink = f"[[books/{b['dir']}/questions\\|疑问]]" if (b['path'].parent / 'questions.md').exists() else ''
        ver = '是' if b['verified'] else ('否' if b['verified'] is False else '')
        I.append(f"| {link_for(b).replace('|', chr(92) + '|')} | {b['author']} | {b['year']} | {b['date']} | {len(b['concepts'])} | {ver} | {qlink} |")
    I += ['', '## 概念', '', '按涉及的书的数量排列，多本书都谈到的概念在前。', '']
    for canon in sorted(pages, key=lambda c: (-len({b['dir'] for b, _ in by_canon[c]}), c)):
        n = len({b['dir'] for b, _ in by_canon[canon]})
        I.append(f'- [[{canon}]]（{n} 本）')
    I += ['', '## 各书的主题', '']
    for b in books:
        if b['topics']:
            I.append(f"- {link_for(b)}：{'；'.join(b['topics'])}")
    index_text = '\n'.join(I) + '\n'

    # ---- 待读.md ----
    have = {norm_title(b['title']) for b in books} | {norm_title(b['dir']) for b in books}
    rec = defaultdict(list)
    info = {}
    for b in books:
        for f in b['further']:
            key = norm_title(f['title'])
            if not key or key in have or any(key in h or h in key for h in have if len(h) >= 3 and len(key) >= 3):
                continue
            rec[key].append((b, f))
            info.setdefault(key, f)
    multi = sorted((k for k in rec if len({b['dir'] for b, _ in rec[k]}) >= 2), key=lambda k: -len(rec[k]))
    single = sorted(k for k in rec if k not in multi)
    T = ['# 待读', '', '本页由 build_kb.py 生成，每次合并都会重写，请不要手改。来源是各书附录四的延伸阅读，已经读过、在知识库里的书不列。', '']
    T += ['## 多本书都推荐的', '']
    if not multi:
        T += ['暂时没有。读的书多了，这里会出现几本书共同指向的书。', '']
    for k in multi:
        f = info[k]
        T.append(f"### {f['title']}（{f['author']}，{f['year']}）")
        for b, ff in rec[k]:
            T.append(f"- {link_for(b)}：{ff['rel']}")
        T.append('')
    T += ['## 其他推荐', '']
    for k in single:
        b, f = rec[k][0]
        T.append(f"- {f['title']}（{f['author']}，{f['pub']}，{f['year']}）— {link_for(b)}：{f['rel']}")
    todo_text = '\n'.join(T) + '\n'

    # ---- 报告 ----
    print(f'参与合并 {len(books)} 本书：{"、".join(b["dir"] for b in books) or "无"}')
    for name, n in skipped:
        print(f'  ✗ {name} 没通过结构检查（{n} 处），这次没有并入。先运行 check_meta.py 修好。')
    print(f'概念页：新建 {len(created)}，更新 {len(written)}，没有变化 {len(pages) - len(created) - len(written) - len(kept)}。')
    if kept:
        print(f'  ⚠️ 这些概念页的生成区标记被删掉了，整页没有动：{"、".join(kept)}。想恢复自动更新，把两行标记加回去。')
    if orphaned:
        print(f'  ⚠️ 这些概念页已经没有书用到：{"、".join(orphaned)}。页面已标注，确认后可以删掉。')
    legacy = [n for n, _, old in found if old]
    if legacy:
        print(f'  提示：{"、".join(legacy)} 的笔记还叫 note.md，可以用 --rename-notes 改名。')
    for w in warn:
        print(f'  ⚠️ {w}')
    print(f'待读：多本书推荐 {len(multi)} 本，其他 {len(single)} 本。')

    if dry:
        print('\n（--dry-run：没有写任何文件）')
        return
    (kb / 'index.md').write_text(index_text, encoding='utf-8')
    (kb / '待读.md').write_text(todo_text, encoding='utf-8')
    if not (kb / '知识库说明.md').exists():
        (kb / '知识库说明.md').write_text(GUIDE, encoding='utf-8')
    log = kb / 'log.md'
    head = '' if log.exists() else '# 合并记录\n\n每次合并追加一行，最新的在最下面。\n\n'
    with open(log, 'a', encoding='utf-8') as f:
        f.write(head + f"- {datetime.now():%Y-%m-%d %H:%M}：{len(books)} 本书，{len(pages)} 个概念；"
                f"新建 {len(created)}，更新 {len(written)}"
                + (f"，未并入 {'、'.join(n for n, _ in skipped)}" if skipped else '')
                + (f"，提醒 {len(warn) + len(kept) + len(orphaned)} 条" if (warn or kept or orphaned) else '') + '\n')
    print('已写入 index.md、待读.md、log.md。')


if __name__ == '__main__':
    main()
