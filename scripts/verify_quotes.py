#!/usr/bin/env python3
"""
verify_quotes.py — 把读书笔记草稿里的每一条引文拿回原书逐字比对，并核对页码。

用法：
  python verify_quotes.py <草稿.md|.txt|.docx> <工作目录> [--title 书名] [--report 报告路径]

识别的引文形式：
  “原文”（23页）     “原文”（《书名》23页，下同）     “原文”（23—24页）
  “原文（23页）”     ——页码写在引号里也能识别，但会提示挪到引号外
  > 多行的大段引文
  > ……（188页）
  "English quote" (p. 23)  /  (pp. 23–24)
  无页码的书（EPUB/DOCX/TXT）：“原文”（第三章）

比对时忽略空白、换行、全半角、引号样式和破折号样式的差异；引文中的省略号（……）把引文分成几段，
各段须按顺序出现在原文里。标注了其他书名的引文（如《实践论》）视为他书引文，不核对。

结果：✅ 一致；⚠️ 页码不符 / 措辞有出入 / 跨页；❌ 原文中找不到。存在 ⚠️ 或 ❌ 时退出码为 1。
"""

import argparse
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import Book, normalize  # noqa: E402

PAGE_TOKEN = r'(?:\d{1,4}|[ivxlcdm]{1,7})'
CITE_CN = (r'[（(]\s*(?:《(?P<title>[^》]{1,60})》\s*[，,]?\s*)?(?:第\s*)?(?P<p1>' + PAGE_TOKEN + r')\s*'
           r'(?:[-—–~～至到]\s*(?P<p2>' + PAGE_TOKEN + r'))?\s*页[^（()）]{0,12}[）)]')
CITE_EN = (r'\(\s*(?:(?P<title>[^()]{1,60}?),\s*)?pp?\.\s*(?P<p1>' + PAGE_TOKEN + r')\s*'
           r'(?:[-–—]\s*(?P<p2>' + PAGE_TOKEN + r'))?\s*\)')
CN_NUM = '一二三四五六七八九十百零〇两'
CITE_CH = (r'[（(]\s*(?:《(?P<title>[^》]{1,60})》\s*[，,]?\s*)?'
           r'(?P<ch>第\s*[' + CN_NUM + r'\d]{1,5}\s*[章篇部编讲卷回](?:\s*第\s*[' + CN_NUM + r'\d]{1,4}\s*节)?'
           r'|序言?|自序|前言|引言|导言|导论|绪论|结语|结论|余论|后记|跋|附录[^）)]{0,6})\s*[）)]')
CITE_RES = [re.compile(CITE_CN, re.I), re.compile(CITE_EN, re.I), re.compile(CITE_CH)]
CITE_END_RES = [re.compile(p + r'\s*$', re.I) for p in (CITE_CN, CITE_EN)] + [re.compile(CITE_CH + r'\s*$')]
ELLIPSIS = re.compile(r'(?:……|…|⋯⋯|⋯|\.{3,}|［…+］|\[…+\]|\[\.{3}\])')


def read_draft(path):
    p = Path(path)
    if p.suffix.lower() == '.docx':
        import docx
        return '\n'.join(par.text for par in docx.Document(str(p)).paragraphs)
    return p.read_text(encoding='utf-8')


def parse_cite(m):
    gd = {k: v for k, v in m.groupdict().items() if v}
    title = gd.get('title')
    if 'ch' in gd:
        return {'kind': 'chapter', 'ch': re.sub(r'\s', '', gd['ch']), 'title': title, 'raw': m.group(0)}
    p1 = gd.get('p1')
    p2 = gd.get('p2') or p1
    return {'kind': 'page', 'p1': p1, 'p2': p2, 'title': title, 'raw': m.group(0)}


def match_cite(after):
    """引号后紧跟的页码括注（允许前面有空白）。"""
    s = after.lstrip(' \t')
    for rx in CITE_RES:
        m = rx.match(s)
        if m:
            return parse_cite(m)
    return None


def search_end_cite(s):
    """在一段文字末尾找页码括注，返回 (cite, 起点) 或 (None, None)。"""
    for rx in CITE_END_RES:
        m = rx.search(s)
        if m:
            return parse_cite(m), m.start()
    return None, None


def extract_quotes(text):
    quotes = []
    lines = text.split('\n')
    offsets, pos = [], 0
    for ln in lines:
        offsets.append(pos)
        pos += len(ln) + 1

    def lineno(off):
        lo, hi = 0, len(offsets) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if offsets[mid] <= off:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    # 1) 块引用
    block_spans = []
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith('>'):
            j = i
            buf = []
            while j < len(lines) and lines[j].lstrip().startswith('>'):
                buf.append(re.sub(r'^\s*>\s?', '', lines[j]))
                j += 1
            body = '\n'.join(buf).strip()
            cite, cpos = search_end_cite(body)
            if cite:
                body = body[:cpos].rstrip()
            elif j < len(lines):
                cite = match_cite(lines[j])
            body = body.strip().strip('“”"')
            quotes.append({'text': body, 'cite': cite, 'line': i + 1, 'cite_inside': False, 'block': True})
            block_spans.append((offsets[i], offsets[j - 1] + len(lines[j - 1])))
            i = j
        else:
            i += 1

    def in_block(off):
        return any(a <= off <= b for a, b in block_spans)

    # 2) 中文引号
    for m in re.finditer(r'“([^“”]{1,3000})”', text):
        if in_block(m.start()):
            continue
        inner = m.group(1)
        inside = False
        cite, cpos = search_end_cite(inner)
        if cite:
            inner = inner[:cpos]
            inside = True
        else:
            cite = match_cite(text[m.end():m.end() + 80])
        quotes.append({'text': inner, 'cite': cite, 'line': lineno(m.start()), 'cite_inside': inside, 'block': False})

    # 3) 英文直引号
    for m in re.finditer(r'"([^"\n]{15,3000})"', text):
        if in_block(m.start()):
            continue
        cite = match_cite(text[m.end():m.end() + 80])
        if cite:
            quotes.append({'text': m.group(1), 'cite': cite, 'line': lineno(m.start()), 'cite_inside': False, 'block': False})
    return quotes


def segments(q):
    parts = [normalize(p)[0] for p in ELLIPSIS.split(q)]
    return [p for p in parts if len(p) >= 2] or [normalize(q)[0]]


def find_ordered(full, segs, start, end):
    """在 full[start:end] 中按顺序找各段，返回 (起点, 终点) 或 None。"""
    k = full.find(segs[0], start, end)
    while k >= 0:
        pos, ok = k + len(segs[0]), True
        limit = min(end, k + sum(len(s) for s in segs) * 4 + 400)
        for s in segs[1:]:
            j = full.find(s, pos, limit)
            if j < 0:
                ok = False
                break
            pos = j + len(s)
        if ok:
            return k, pos
        k = full.find(segs[0], k + 1, end)
    return None


def best_window(q, text):
    L = len(q)
    if not text or not L:
        return 0.0, 0, 0
    if len(text) <= L:
        return SequenceMatcher(None, q, text, autojunk=False).ratio(), 0, len(text)
    step = max(1, L // 8)
    best = (0.0, 0, L)
    for s in range(0, len(text) - L + 1, step):
        sm = SequenceMatcher(None, q, text[s:s + L], autojunk=False)
        if sm.real_quick_ratio() <= best[0] or sm.quick_ratio() <= best[0]:
            continue
        r = sm.ratio()
        if r > best[0]:
            best = (r, s, L)
    r0, s0, _ = best
    for s in range(max(0, s0 - step), min(len(text) - 1, s0 + step) + 1):
        for LL in sorted({max(1, L - L // 8), L, L + L // 8}):
            if s + LL > len(text):
                continue
            r = SequenceMatcher(None, q, text[s:s + LL], autojunk=False).ratio()
            if r > best[0]:
                best = (r, s, LL)
    return best


def region_for(book, cite):
    """根据引文标注找原书里的比对范围：返回 (起单元, 止单元, 严格单元集合)。"""
    if not cite:
        return None
    if cite['kind'] == 'chapter':
        key = cite['ch']
        idxs = [int(c['idx']) for c in book.chapters if key in re.sub(r'\s', '', c.get('title', ''))]
        units = [k for k, u in enumerate(book.units) if u.get('chapter') in idxs]
        return (min(units), max(units), set(units)) if units else None
    p1, p2 = cite['p1'], cite['p2']
    numeric = p1.isdigit() and p2.isdigit()
    if book.meta.get('has_printed_pages'):
        if numeric:
            strict = set(book.units_for_pages(int(p1), int(p2), slack=0))
            loose = book.units_for_pages(int(p1), int(p2), slack=1)
        else:
            strict = set(book.units_for_pages(p1, p1, slack=0))
            loose = sorted(strict)
    elif book.meta.get('format') == 'pdf' and numeric:
        strict = set(book.units_for_pdf_pages(int(p1), int(p2), slack=0))
        loose = book.units_for_pdf_pages(int(p1), int(p2), slack=1)
    else:
        return None
    if not loose:
        return None
    return min(loose), max(loose), strict


def is_other_book(cite, title):
    if not cite or not cite.get('title') or not title:
        return False
    a = normalize(cite['title'])[0]
    b = normalize(title)[0]
    return not (a in b or b in a)


def check(book, q, title):
    full, starts, maps = book.index()
    segs = segments(q['text'])
    qn = ''.join(segs)
    res = {'q': q, 'status': None}
    cite = q['cite']
    if is_other_book(cite, title):
        res['status'] = 'other'
        return res
    reg = region_for(book, cite)

    def span_label(a, b):
        ua, ub = book.unit_at(a), book.unit_at(max(a, b - 1))
        lab = book.unit_label(book.units[ua])
        if ub != ua:
            lab += ' → ' + book.unit_label(book.units[ub], with_pdf=False)
        t = book.chapter_title(book.units[ua])
        return lab + (f'（{t}）' if t else ''), ua, ub

    if reg:
        ua, ub, strict = reg
        hit = None
        # 先在标注的页内找（引文可以延续到下一页），找不到再放宽到前后各一页
        ranges = []
        if strict:
            sa, sb = min(strict), max(strict)
            ranges.append((starts[sa], starts[sb + 2] if sb + 2 < len(starts) else len(full)))
        ranges.append((starts[ua], starts[ub + 1] if ub + 1 < len(starts) else len(full)))
        for gs, ge in ranges:
            hit = find_ordered(full, segs, gs, ge)
            if hit and (book.unit_at(hit[0]) in strict or (gs, ge) == ranges[-1]):
                break
            hit = None
        if hit:
            lab, a_u, b_u = span_label(*hit)
            res.update(found=hit, label=lab)
            if a_u in strict:
                res['status'] = 'ok'
                if cite['kind'] == 'page' and b_u not in strict and book.units[b_u].get('page') != book.units[a_u].get('page'):
                    res['status'] = 'span'
            else:
                res['status'] = 'page'
            res['ocr'] = book.units[a_u].get('src') == 'ocr'
            return res
    hit = find_ordered(full, segs, 0, len(full))
    if hit:
        lab, a_u, _ = span_label(*hit)
        res.update(found=hit, label=lab, status='page' if cite else 'nocite_found', ocr=book.units[a_u].get('src') == 'ocr')
        return res
    # 模糊比对：先在标注范围内，再用若干锚点在全书找候选
    windows = []
    if reg:
        ua, ub, _ = reg
        windows.append((starts[ua], starts[ub + 1] if ub + 1 < len(starts) else len(full)))
    L = len(qn)
    for pos in sorted({0, L // 4, L // 2, (3 * L) // 4, max(0, L - 6)}):
        anchor = qn[pos:pos + 6]
        if len(anchor) < 4:
            continue
        k, n = full.find(anchor), 0
        while k >= 0 and n < 15:
            windows.append((max(0, k - pos - L // 2), min(len(full), k - pos + L + L // 2)))
            k = full.find(anchor, k + 1)
            n += 1
    best = (0.0, 0, 0)
    for a, b in windows:
        r, s, ll = best_window(qn, full[a:b])
        if r > best[0]:
            best = (r, a + s, ll)
    r, s, ll = best
    if r > 0:
        lab, a_u, _ = span_label(s, s + ll)
        res.update(ratio=r, label=lab, original=book.original_snippet(s, s + ll), ocr=book.units[a_u].get('src') == 'ocr')
    else:
        res['ratio'] = 0.0
    res['status'] = 'close' if r >= 0.85 else 'missing'
    return res


def short(s, n=40):
    s = re.sub(r'\s+', '', s) if re.search(r'[一-鿿]', s) else re.sub(r'\s+', ' ', s)
    return s if len(s) <= n else s[:n] + '…'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('draft')
    ap.add_argument('workdir')
    ap.add_argument('--title', help='本书书名（用来区分他书引文）；默认取源文件名')
    ap.add_argument('--report', help='报告输出路径（默认：工作目录/verify_report.md）')
    ap.add_argument('--min-len', type=int, default=15, help='无页码的引号内容达到多少字才视为引文（默认 15）')
    args = ap.parse_args()

    book = Book(args.workdir)
    title = args.title or Path(book.meta.get('source', '')).stem
    text = read_draft(args.draft)
    quotes = extract_quotes(text)
    results, skipped_terms = [], 0
    for q in quotes:
        if not q['cite']:
            if len(normalize(q['text'])[0]) < args.min_len:
                skipped_terms += 1
                continue
        results.append(check(book, q, title))

    cnt = {}
    for r in results:
        cnt[r['status']] = cnt.get(r['status'], 0) + 1
    L = ['# 引文核对报告', '',
         f'草稿：{args.draft}　书：{title}', '',
         f"共核对 {len(results)} 条：✅ 一致 {cnt.get('ok', 0)} 条；"
         f"⚠️ 跨页 {cnt.get('span', 0)}、页码或章节不符 {cnt.get('page', 0)}、措辞有出入 {cnt.get('close', 0)} 条；"
         f"❌ 找不到 {cnt.get('missing', 0)} 条；他书引文未核对 {cnt.get('other', 0)} 条；"
         f"缺页码的长引文 {cnt.get('nocite_found', 0) + sum(1 for r in results if not r['q']['cite'] and r['status'] in ('close', 'missing'))} 条。"
         f"（另有 {skipped_terms} 处引号内容较短且无页码，视为术语或强调，未核对。）", '']

    problems = [r for r in results if r['status'] not in ('ok', 'other')]
    if problems:
        L.append('## 需要处理')
        for n, r in enumerate(problems, 1):
            q = r['q']
            cite = q['cite']
            ctext = cite['raw'] if cite else '（无页码）'
            L.append(f"\n### {n}. 第 {q['line']} 行　“{short(q['text'])}”{ctext}")
            st = r['status']
            if st == 'span':
                L.append(f"- ⚠️ 引文跨页：实际位于 {r['label']}。建议页码写成起止页，如“23—24页”。")
            elif st == 'page':
                what = '章节标注' if cite and cite['kind'] == 'chapter' else '页码'
                L.append(f"- ⚠️ {what}不符：这句话实际在 {r['label']}。请改正{what}。")
            elif st == 'nocite_found':
                L.append(f"- ⚠️ 缺页码：原文在 {r['label']}，请补上页码。")
            elif st == 'close':
                L.append(f"- ⚠️ 措辞有出入（相似度 {r['ratio']:.2f}），位置 {r['label']}。")
                L.append(f"- 原文：{r['original']}")
                L.append('- 请照原文逐字改正，或去掉引号改为转述。')
            elif st == 'missing':
                L.append(f"- ❌ 原文中找不到（最接近处相似度 {r.get('ratio', 0):.2f}"
                         + (f"，在 {r['label']}" if r.get('label') else '') + '）。')
                if r.get('original'):
                    L.append(f"- 最接近的原文：{short(r['original'], 120)}")
                L.append('- 不要保留这条引文：用 locate.py 重新查找并逐字摘录；查不到就去掉引号改为转述。')
            if q.get('cite_inside'):
                L.append('- 格式：页码括注写在了引号里面，请挪到后引号之外。')
            if r.get('ocr'):
                L.append('- 该页是 OCR 文本，可能有识别错字：请用 Read 查看该页图片目视核对。')
    else:
        L.append('全部引文与原文一致。')

    inside = [r for r in results if r['status'] == 'ok' and r['q'].get('cite_inside')]
    ocr_ok = [r for r in results if r['status'] == 'ok' and r.get('ocr')]
    if inside or ocr_ok:
        L.append('\n## 其他提示')
        for r in inside:
            L.append(f"- 第 {r['q']['line']} 行：页码括注写在了引号里面，请挪到引号外。")
        for r in ocr_ok:
            L.append(f"- 第 {r['q']['line']} 行：与 OCR 文本一致，但 OCR 可能有错字，建议目视核对 {r['label']}。")

    oks = [r for r in results if r['status'] == 'ok']
    if oks:
        L.append('\n## 一致的引文')
        for r in oks:
            L.append(f"- 第 {r['q']['line']} 行　{r['label']}　“{short(r['q']['text'], 30)}”")
    others = [r for r in results if r['status'] == 'other']
    if others:
        L.append('\n## 他书引文（未核对，请确认出处可靠）')
        if not args.title:
            L.append(f'（没有指定 --title，暂按文件名“{title}”判断书名；如果下面有本书的引文，请加 --title 重跑。）')
        for r in others:
            L.append(f"- 第 {r['q']['line']} 行　{r['q']['cite']['raw']}　“{short(r['q']['text'], 30)}”")

    report = Path(args.report) if args.report else Path(args.workdir) / 'verify_report.md'
    report.write_text('\n'.join(L) + '\n', encoding='utf-8')
    print('\n'.join(L))
    print(f'\n报告已写入：{report}')
    sys.exit(1 if problems else 0)


if __name__ == '__main__':
    main()
