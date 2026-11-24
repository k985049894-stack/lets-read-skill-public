#!/usr/bin/env python3
"""
prepare_book.py — 把一本书预处理成便于逐章精读的工作目录。

支持格式：PDF（含扫描版，需 --ocr）、EPUB、DOCX、TXT、MD。MOBI/AZW3 需先转成 EPUB 或 PDF。

用法：
  python prepare_book.py <书籍文件> [--out 工作目录]
  python prepare_book.py <书籍文件> --toc 章节表.txt      # 自动切章不准时，手工指定章节起点
  python prepare_book.py <书籍文件> --ocr                  # 扫描版：默认用 RapidOCR 识别，自动安装
  python prepare_book.py <书籍文件> --ocr --ocr-engine tesseract [--lang chi_sim+eng] [--jobs 4]

扫描版的识别结果按页缓存在 <工作目录>/ocr_cache/，中途被打断后重跑同一条命令会接着识别，
已识别的页不再重复。整本书识别耗时较长（云端约 10 秒一页），可以放到后台运行：
  nohup python prepare_book.py 书.pdf --ocr --out W > W.log 2>&1 &

--toc 文件格式：每行“标题<TAB>起点”，起点写 PDF 页序（如 35）或印刷页码（如 p23）。

产出（工作目录内）：
  overview.md    概况：格式、页数、字数、估计 token、扫描版与否、页码对齐、版本线索、章节表、警告
  chapters.tsv   章节表：序号、标题、类别、起止 PDF 页、起止印刷页、字数、估计 token、文件名
  chapters/      按章切分的文本，每页前有标记行：=== [pdf:35] [页:23] ===
  book.txt       全书文本（同样带页标记），供关键词回查
  pages.jsonl    每个单元（PDF 页 / EPUB 页或文档 / 文本块）一行，供 locate.py 与 verify_quotes.py 使用
  meta.json      元信息
"""

import argparse
import json
import warnings as _warnings
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
_warnings.filterwarnings('ignore', message='.*XML.*')
from _common import estimate_tokens, fix_cjk_compat, is_cjk_dominant  # noqa: E402

# ---------------------------------------------------------------- 正则

CN_NUM = '一二三四五六七八九十百零〇两'
CHAPTER_RE = re.compile(
    rf'^(第\s*[{CN_NUM}\d]{{1,5}}\s*[章篇部编讲卷回]'
    r'|(?:Chapter|CHAPTER)\s+[\dIVXLCivxlc]+'
    r'|(?:Part|PART)\s+[\dIVXLCivxlc]+)'
    r'(?:\s|：|:|·|\.|$)')
PART_RE = re.compile(rf'^(第\s*[{CN_NUM}\d]{{1,5}}\s*[部编篇卷]|(?:Part|PART)\s)')
INTRO_WORDS = ('序', '序言', '自序', '代序', '总序', '前言', '引言', '导言', '导论', '绪论', '绪言',
               '楔子', '译者序', '出版说明', '中文版序', 'Preface', 'Foreword', 'Introduction', 'Prologue')
OUTRO_WORDS = ('结语', '结论', '结束语', '余论', '尾声', '后记', '跋', '译后记',
               'Conclusion', 'Epilogue', 'Afterword', 'Postscript')
BACK_WORDS = ('附录', '参考文献', '参考书目', '索引', '致谢', '注释',
              'Appendix', 'Bibliography', 'References', 'Index', 'Notes',
              'Acknowledgments', 'Acknowledgements', 'Acknowledgment', 'Acknowledgement')
_ALL_SPECIAL = sorted(INTRO_WORDS + OUTRO_WORDS + BACK_WORDS, key=len, reverse=True)
SPECIAL_RE = re.compile(
    r'^(' + '|'.join(re.escape(w) for w in _ALL_SPECIAL) + r')'
    r'(?:\s*[一二三四五六七八九十\dA-Z]{0,3})?(?:\s|：|:|$)')
TOC_TITLE_RE = re.compile(r'^(目\s*录|目\s*次|Contents|CONTENTS|Table of Contents)$')

NUM_LINE = re.compile(r'^[\s\-—–·•\[\(（〔<]*(\d{1,4})[\s\-—–·•\]\)）〕>]*$')
NUM_PAGE_CN = re.compile(r'^第\s*(\d{1,4})\s*页$')
NUM_SLASH = re.compile(r'(?:^|\s)(\d{1,4})\s*/\s*\d{1,4}$')
NUM_EDGE_START = re.compile(r'^(\d{1,4})\s+\S')
NUM_EDGE_END = re.compile(r'\S\s+(\d{1,4})$')
ROMAN_RE = re.compile(r'^[\s\-—–·•\[\(（]*([ivxlcdm]{1,7}|[IVXLCDM]{1,7})[\s\-—–·•\]\)）]*$')

EDITION_RE = re.compile(r'(ISBN|出版社|出版发行|版\s*次|印\s*次|第\s*\d+\s*版|\d{4}\s*年\s*\d{1,2}\s*月.*[版印]|'
                        r'定\s*价|CIP|Copyright|©|Published by|First published|Edition|Printed in)', re.I)


def is_heading(line):
    s = line.strip()
    if not s or len(s) > 40:
        return False
    return bool(CHAPTER_RE.match(s) or SPECIAL_RE.match(s))


def heading_kind(title):
    s = title.strip()
    if CHAPTER_RE.match(s):
        return 'chapter'
    for kind, words in (('intro', INTRO_WORDS), ('outro', OUTRO_WORDS), ('back', BACK_WORDS)):
        for w in sorted(words, key=len, reverse=True):
            if s.startswith(w):
                return kind
    return 'chapter'


def nonspace_len(s):
    return len(re.sub(r'\s', '', s))


# ---------------------------------------------------------------- PDF

def pdf_npages(path):
    try:
        out = subprocess.run(['pdfinfo', str(path)], capture_output=True, text=True, check=True).stdout
        m = re.search(r'^Pages:\s+(\d+)', out, re.M)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    from pypdf import PdfReader
    return len(PdfReader(str(path)).pages)


def pdf_text_pages(path, npages):
    if shutil.which('pdftotext'):
        out = subprocess.run(['pdftotext', '-enc', 'UTF-8', str(path), '-'], capture_output=True, check=True).stdout
        pages = out.decode('utf-8', errors='replace').split('\f')
    else:
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            pages = [(p.extract_text() or '') for p in pdf.pages]
    pages = pages[:npages]
    pages += [''] * (npages - len(pages))
    return pages


def pdf_outline(path):
    try:
        from pypdf import PdfReader
        r = PdfReader(str(path))
        items = []

        def walk(ol, level):
            for o in ol:
                if isinstance(o, list):
                    walk(o, level + 1)
                    continue
                try:
                    p = r.get_destination_page_number(o)
                except Exception:
                    continue
                if p is None or p < 0:
                    continue
                items.append((level, str(o.title).strip(), p + 1))

        walk(r.outline, 0)
        return items
    except Exception:
        return []


def choose_outline(items):
    if not items:
        return []
    levels = Counter(l for l, _, _ in items)
    base = 0 if levels.get(0, 0) >= 3 else 1
    chosen = [it for it in items if it[0] == base]
    if chosen and sum(1 for _, t, _ in chosen if PART_RE.match(t)) >= max(2, len(chosen) // 2):
        chosen = [it for it in items if it[0] in (base, base + 1)]
    if len(chosen) < 2:
        return []
    return [(t, p) for _, t, p in sorted(chosen, key=lambda x: x[2])]


TESS_ENV = None


def tesseract_langs():
    try:
        out = subprocess.run(['tesseract', '--list-langs'], capture_output=True, text=True, env=TESS_ENV).stdout
        return set(l.strip() for l in out.splitlines()[1:] if l.strip())
    except Exception:
        return set()


CJK_PUNCT = '　-〿＀-￯一-鿿㐀-䶿“”‘’（）《》'
CHAPTER_GLUE = re.compile(rf'^(第\s*[{CN_NUM}\d]{{1,5}}\s*[章篇部编讲卷回])(?=[^\s：:·.\d{CN_NUM}])')


def clean_ocr(text):
    text = re.sub(rf'(?<=[{CJK_PUNCT}])[ \t]+(?=[{CJK_PUNCT}])', '', text)
    # OCR 常把“第一章 控制”识别成“第一章控制”，切章靠标题里的这个空格，短行里补回来
    return '\n'.join(CHAPTER_GLUE.sub(r'\1 ', ln) if len(ln.strip()) <= 30 else ln for ln in text.split('\n'))


def ocr_pages(path, page_numbers, lang, jobs, dpi=300):
    tmp = Path(tempfile.mkdtemp(prefix='ocr_'))

    def one(p):
        prefix = tmp / f'p{p}'
        subprocess.run(['pdftoppm', '-f', str(p), '-l', str(p), '-r', str(dpi), '-gray', '-png',
                        '-singlefile', str(path), str(prefix)], check=True, capture_output=True)
        res = subprocess.run(['tesseract', str(prefix) + '.png', 'stdout', '-l', lang, '--psm', '3'],
                             capture_output=True, text=True, env=TESS_ENV)
        try:
            (Path(str(prefix) + '.png')).unlink()
        except Exception:
            pass
        return p, clean_ocr(res.stdout)

    results = {}
    done = 0
    with ThreadPoolExecutor(max_workers=jobs) as ex:
        for p, txt in ex.map(one, page_numbers):
            results[p] = txt
            done += 1
            if done % 20 == 0 or done == len(page_numbers):
                print(f'  OCR 进度 {done}/{len(page_numbers)}', flush=True)
    shutil.rmtree(tmp, ignore_errors=True)
    return results


def ensure_rapidocr():
    """导入 RapidOCR；没有就用 pip 安装（约半分钟）。装不上返回 None。"""
    try:
        from rapidocr_onnxruntime import RapidOCR
        return RapidOCR
    except ImportError:
        pass
    print('正在安装 RapidOCR（只需一次，约半分钟）…', flush=True)
    base = [sys.executable, '-m', 'pip', 'install', '-q', 'rapidocr_onnxruntime']
    for extra in ([], ['--break-system-packages'], ['--user', '--break-system-packages']):
        if subprocess.run(base + extra, capture_output=True).returncode == 0:
            break
    try:
        import importlib
        importlib.invalidate_caches()
        from rapidocr_onnxruntime import RapidOCR
        return RapidOCR
    except ImportError:
        return None


PAGE_LIKE = re.compile(r'[\s\-—–·•\[\]()（）〔〕]*(\d{1,4}|[ivxlcdmIVXLCDM]{1,7})[\s\-—–·•\[\]()（）〔〕]*')


def _is_cjk(ch):
    return bool(re.match(rf'[{CJK_PUNCT}]', ch))


def boxes_to_text(result):
    """把 RapidOCR 的文本框按位置排成行：同一高度的框并成一行，从左到右拼接。"""
    items = []
    for box, txt, score in result or []:
        ys = [pt[1] for pt in box]
        xs = [pt[0] for pt in box]
        items.append((min(ys), max(ys), min(xs), txt.strip(), float(score)))
    # 调低了检测门槛（见 ocr_pages_rapid），以免漏掉页码这类孤立的小数字；
    # 低把握的框只保留像页码的（数字、罗马数字），其余丢掉，免得混进噪声
    items = [it for it in items if it[3] and (it[4] >= 0.5 or PAGE_LIKE.fullmatch(it[3]))]
    items.sort(key=lambda t: ((t[0] + t[1]) / 2, t[2]))
    lines = []
    for it in items:
        cy, h = (it[0] + it[1]) / 2, it[1] - it[0]
        if lines and abs(cy - lines[-1]['cy']) < 0.5 * max(h, lines[-1]['h']):
            lines[-1]['items'].append(it)
        else:
            lines.append({'cy': cy, 'h': h, 'items': [it]})
    out = []
    for ln in lines:
        s = ''
        for it in sorted(ln['items'], key=lambda t: t[2]):
            if s and not (_is_cjk(s[-1]) or _is_cjk(it[3][0])):
                s += ' '
            s += it[3]
        out.append(s)
    body = [it for it in items if it[4] >= 0.5]
    conf = sum(it[4] for it in body) / len(body) if body else 0.0
    return '\n'.join(out), conf


def ocr_pages_rapid(path, page_numbers, cache_dir, dpi=200):
    """用 RapidOCR 逐页识别，结果缓存在 cache_dir/pNNNN.json，重跑时跳过已识别的页。"""
    RapidOCR = ensure_rapidocr()
    if RapidOCR is None:
        return None
    cache_dir.mkdir(parents=True, exist_ok=True)
    todo = [p for p in page_numbers if not (cache_dir / f'p{p:04d}.json').exists()]
    if len(todo) < len(page_numbers):
        print(f'  已有 {len(page_numbers) - len(todo)} 页的识别结果，接着识别剩下的 {len(todo)} 页', flush=True)
    engine = RapidOCR() if todo else None
    tmp = Path(tempfile.mkdtemp(prefix='ocr_'))
    t0 = time.time()
    for k, p in enumerate(todo, 1):
        img = tmp / f'p{p}'
        subprocess.run(['pdftoppm', '-f', str(p), '-l', str(p), '-r', str(dpi), '-gray', '-png',
                        '-singlefile', str(path), str(img)], check=True, capture_output=True)
        result, _ = engine(str(img) + '.png', box_thresh=0.3, text_score=0.3)
        text, conf = boxes_to_text(result)
        (cache_dir / f'p{p:04d}.json').write_text(
            json.dumps({'text': text, 'conf': round(conf, 4)}, ensure_ascii=False), encoding='utf-8')
        Path(str(img) + '.png').unlink(missing_ok=True)
        if k % 10 == 0 or k == len(todo):
            left = (time.time() - t0) / k * (len(todo) - k)
            print(f'  OCR 进度 {k}/{len(todo)}，预计还要 {left / 60:.0f} 分钟', flush=True)
    shutil.rmtree(tmp, ignore_errors=True)
    res = {}
    for p in page_numbers:
        d = json.loads((cache_dir / f'p{p:04d}.json').read_text(encoding='utf-8'))
        res[p] = (clean_ocr(d['text']), d['conf'])
    return res


def page_candidates(lines, npages):
    nums, romans = set(), set()
    edge = lines[:3] + lines[-3:]
    for ln in edge:
        s = ln.strip()
        if not s:
            continue
        for pat in (NUM_LINE, NUM_PAGE_CN):
            m = pat.match(s)
            if m:
                nums.add(int(m.group(1)))
        m = NUM_SLASH.search(s)
        if m:
            nums.add(int(m.group(1)))
        if len(s) <= 60:
            m = NUM_EDGE_START.match(s)
            if m:
                nums.add(int(m.group(1)))
            m = NUM_EDGE_END.search(s)
            if m:
                nums.add(int(m.group(1)))
        m = ROMAN_RE.match(s)
        if m:
            romans.add(m.group(1).lower())
    return {n for n in nums if 1 <= n <= npages + 60}, romans


def build_page_map(cands, romans, npages, window=8):
    """先确认“识别到”的页码（本页的候选数字与附近几页的页码规律一致），再给没有页码的页插值推断。

    前后两个已识别页的页码差 = PDF 页差时才插值；不一致说明中间夹了不编页码的插页（图版等），保持未知。
    """
    cnt_cache = []
    for i in range(npages):
        cnt = Counter()
        for j in range(max(0, i - window), min(npages, i + window + 1)):
            for n in cands[j]:
                cnt[(j + 1) - n] += 1
        cnt_cache.append(cnt)
    res = [(None, 'unknown')] * npages
    for i in range(npages):
        pdf = i + 1
        own = [(cnt_cache[i][pdf - n], pdf - n) for n in cands[i] if cnt_cache[i][pdf - n] >= 2]
        if own:
            _, d = max(own)
            res[i] = (pdf - d, 'detected')
    det = [i for i in range(npages) if res[i][1] == 'detected']
    for i in range(npages):
        if res[i][1] == 'detected':
            continue
        prev = max((j for j in det if j < i), default=None)
        nxt = min((j for j in det if j > i), default=None)
        val = None
        if prev is not None and nxt is not None:
            if res[nxt][0] - res[prev][0] == nxt - prev:
                val = res[prev][0] + (i - prev)
        elif prev is not None and i - prev <= 3:
            val = res[prev][0] + (i - prev)
        elif nxt is not None and nxt - i <= 1 and res[nxt][0] - (nxt - i) >= 1:
            val = res[nxt][0] - (nxt - i)
        if val is not None and val >= 1:
            res[i] = (val, 'inferred')
        elif romans[i]:
            res[i] = (sorted(romans[i])[0], 'roman')
    detected = {v for v, s in res if s == 'detected'}
    res = [((None, 'unknown') if s == 'inferred' and v in detected else (v, s)) for v, s in res]
    return [(str(v) if v is not None else None, s) for v, s in res]


def strip_edges(pages_lines, title_keys=(), keep_positions=()):
    """去掉页眉页脚：纯页码行、与章名相同的页眉，以及在很多页重复出现的边缘行（书名等）。

    keep_positions 是各章标题真正所在的 (页, 行)，这些行保留。
    """
    npages = len(pages_lines)
    freq = Counter()
    keep_positions = set(keep_positions)

    def key(s):
        return re.sub(r'[\d\s\-—–·•/]', '', s)

    title_keys = {key(t) for t in title_keys if key(t)}

    for lines in pages_lines:
        ne = [l for l in lines if l.strip()]
        for l in ne[:2] + ne[-2:]:
            if len(l.strip()) <= 60 and key(l):
                freq[key(l)] += 1
    threshold = max(4, int(npages * 0.05))
    repeated = {k for k, c in freq.items() if c >= threshold} | title_keys
    out = []
    for pi, lines in enumerate(pages_lines):
        idxs = [k for k, l in enumerate(lines) if l.strip()]
        edge = set(idxs[:2] + idxs[-2:])
        keep = []
        for k, l in enumerate(lines):
            s = l.strip()
            if k in edge and s and (pi, k) not in keep_positions:
                if NUM_LINE.match(s) or NUM_PAGE_CN.match(s) or ROMAN_RE.match(s) or NUM_SLASH.search(s) and len(s) <= 12:
                    continue
                if len(s) <= 60 and key(s) in repeated:
                    continue
            keep.append(l)
        out.append(keep)
    return out


def load_pdf(path, args, warnings):
    npages = pdf_npages(path)
    print(f'PDF 共 {npages} 页，提取文字层…', flush=True)
    texts = pdf_text_pages(path, npages)
    low = [i for i, t in enumerate(texts) if nonspace_len(t) < 30]
    scanned = npages >= 3 and len(low) / npages > 0.6
    src = ['text'] * npages
    conf = [None] * npages
    ocr_used = False
    engine_used = None
    if args.ocr or (scanned and args.auto_ocr):
        targets = list(range(1, npages + 1)) if args.ocr_all or scanned else [i + 1 for i in low]
        if args.pages:
            a, b = args.pages
            targets = [p for p in targets if a <= p <= b]
        if args.ocr_engine in ('auto', 'rapidocr'):
            print(f'对 {len(targets)} 页做 OCR（RapidOCR）…', flush=True)
            res = ocr_pages_rapid(path, targets, args.ocr_cache)
            if res is not None:
                for p, (t, c) in res.items():
                    texts[p - 1] = t
                    src[p - 1] = 'ocr'
                    conf[p - 1] = c
                ocr_used, engine_used = True, 'rapidocr'
            elif args.ocr_engine == 'rapidocr':
                print('RapidOCR 装不上（可能是网络限制），可以改用 --ocr-engine tesseract。')
                sys.exit(3)
            else:
                warnings.append('RapidOCR 装不上，改用 tesseract 识别，错字可能较多。')
    if (args.ocr or (scanned and args.auto_ocr)) and not ocr_used:
        langs = tesseract_langs()
        need = [l for l in args.lang.split('+') if l]
        missing = [l for l in need if l not in langs]
        if not shutil.which('tesseract') or missing:
            print('\n需要 OCR，但缺少 tesseract 或语言包：' + ', '.join(missing or ['tesseract']))
            print('可尝试（任选其一）：\n'
                  '  1) 下载语言包到一个可写目录，再用 --tessdata 指定该目录：\n'
                  '     mkdir -p ~/tessdata && cp /usr/share/tesseract-ocr/*/tessdata/*.traineddata ~/tessdata/ 2>/dev/null\n'
                  '     curl -sSL -o ~/tessdata/chi_sim.traineddata '
                  'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/chi_sim.traineddata\n'
                  '     python prepare_book.py 书.pdf --ocr --tessdata ~/tessdata\n'
                  '  2) apt-get update && apt-get install -y tesseract-ocr tesseract-ocr-chi-sim\n'
                  '（下载后用 ls -l 确认文件有几 MB，只有几百字节说明下载被拦截了）')
            sys.exit(3)
        print(f'对 {len(targets)} 页做 OCR（tesseract {args.lang}，{args.jobs} 线程）…', flush=True)
        res = ocr_pages(path, targets, args.lang, args.jobs)
        for p, t in res.items():
            texts[p - 1] = t
            src[p - 1] = 'ocr'
        ocr_used, engine_used = True, 'tesseract'
    elif scanned and not ocr_used:
        warnings.append(f'这是扫描版（{len(low)}/{npages} 页几乎没有文字层）。请加 --ocr 重新运行'
                        '（默认用 RapidOCR 识别，会自动安装）。')
    elif low and len(low) > 3 and not ocr_used:
        warnings.append(f'有 {len(low)} 页几乎没有文字（可能是插图、空白页或扫描页）：PDF 第 '
                        + ', '.join(str(i + 1) for i in low[:20]) + (' 等' if len(low) > 20 else '') + ' 页。')

    pages_lines = [t.splitlines() for t in texts]
    cands, romans = [], []
    for lines in pages_lines:
        c, r = page_candidates([l for l in lines if l.strip()], npages)
        cands.append(c)
        romans.append(r)
    pmap = build_page_map(cands, romans, npages)
    covered = sum(1 for p, s in pmap if s in ('detected', 'inferred'))
    has_printed = covered >= max(2, npages * 0.3)
    if not has_printed:
        warnings.append('未能可靠识别印刷页码（覆盖率 {:.0%}）。引用暂用 PDF 页序，请向用户确认版本或人工抽查几页。'.format(
            covered / max(1, npages)))
        pmap = [(None, 'unknown')] * npages

    units = []
    for i in range(npages):
        units.append({'i': i, 'pdf': i + 1, 'page': pmap[i][0], 'page_status': pmap[i][1],
                      'src': src[i], 'lines': pages_lines[i], 'ocr_conf': conf[i]})
    outline = choose_outline(pdf_outline(path))
    weak = [i + 1 for i in range(npages) if conf[i] is not None and conf[i] < 0.85]
    if weak:
        warnings.append(f'有 {len(weak)} 页识别把握较低（平均置信度低于 0.85），引用这些页时要看原页图片：PDF 第 '
                        + ', '.join(map(str, weak[:30])) + (' 等' if len(weak) > 30 else '') + ' 页。')
    return units, {'format': 'pdf', 'pdf_pages': npages, 'scanned': scanned, 'ocr': ocr_used, 'ocr_engine': engine_used,
                   'has_printed_pages': has_printed, 'page_coverage': round(covered / max(1, npages), 3),
                   'unit_kind': 'pdf_page'}, outline


# ---------------------------------------------------------------- EPUB

def load_epub(path, args, warnings):
    from bs4 import BeautifulSoup, NavigableString
    z = zipfile.ZipFile(str(path))
    container = BeautifulSoup(z.read('META-INF/container.xml'), 'html.parser')
    opf_path = container.find('rootfile')['full-path']
    base = PurePosixPath(opf_path).parent
    opf = BeautifulSoup(z.read(opf_path), 'html.parser')

    def resolve(href, rel_base):
        href = href.split('#')[0]
        p = PurePosixPath(rel_base) / href if str(rel_base) not in ('', '.') else PurePosixPath(href)
        parts = []
        for part in p.parts:
            if part == '..':
                if parts:
                    parts.pop()
            elif part != '.':
                parts.append(part)
        return '/'.join(parts)

    manifest = {}
    nav_href, ncx_href = None, None
    for it in opf.find_all('item'):
        full = resolve(it.get('href', ''), base)
        manifest[it.get('id')] = (full, it.get('media-type', ''))
        if 'nav' in (it.get('properties') or ''):
            nav_href = full
        if it.get('media-type') == 'application/x-dtbncx+xml':
            ncx_href = full
    spine = [manifest[i['idref']][0] for i in opf.find_all('itemref') if i.get('idref') in manifest]

    titles = {}
    if nav_href:
        nav = BeautifulSoup(z.read(nav_href), 'html.parser')
        toc = nav.find('nav', attrs={'epub:type': 'toc'}) or nav.find('nav')
        if toc:
            for a in toc.find_all('a'):
                f = resolve(a.get('href', ''), PurePosixPath(nav_href).parent)
                titles.setdefault(f, a.get_text(' ', strip=True))
    if not titles and ncx_href:
        ncx = BeautifulSoup(z.read(ncx_href), 'html.parser')
        for np_ in ncx.find_all('navpoint'):
            lab = np_.find('navlabel')
            con = np_.find('content')
            if lab and con:
                f = resolve(con.get('src', ''), PurePosixPath(ncx_href).parent)
                titles.setdefault(f, lab.get_text(' ', strip=True))

    units = []
    starts = []  # (unit index, line index, title)
    any_pages = False
    for d, href in enumerate(spine):
        try:
            html = z.read(href)
        except KeyError:
            continue
        soup = BeautifulSoup(html, 'html.parser')
        for tag in soup(['script', 'style']):
            tag.decompose()
        for pb in soup.find_all(lambda t: t.get('epub:type') == 'pagebreak' or t.get('role') == 'doc-pagebreak'):
            label = pb.get('title') or pb.get('aria-label') or re.sub(r'\D', '', pb.get('id', '') or '')
            pb.replace_with(NavigableString(f'\n<<<PAGE:{label}>>>\n'))
        for br in soup.find_all('br'):
            br.replace_with(NavigableString('\n'))
        body = soup.body or soup
        text = body.get_text('\n')
        text = re.sub(r'\n\s*\n+', '\n\n', text).strip()
        if not text:
            continue
        chunks = re.split(r'\n?<<<PAGE:([^>]*)>>>\n?', text)
        first_unit = len(units)
        # chunks: [前文, label1, 文1, label2, 文2, ...]
        if chunks[0].strip():
            units.append({'i': len(units), 'pdf': None, 'page': None, 'page_status': 'unknown',
                          'src': 'text', 'lines': chunks[0].splitlines(), 'loc': f'文档{d + 1}'})
        for k in range(1, len(chunks), 2):
            any_pages = True
            label = chunks[k].strip() or None
            units.append({'i': len(units), 'pdf': None, 'page': label, 'page_status': 'epub' if label else 'unknown',
                          'src': 'text', 'lines': chunks[k + 1].splitlines(), 'loc': f'文档{d + 1}'})
        if href in titles and len(units) > first_unit:
            starts.append((first_unit, 0, titles[href]))
    if not any_pages:
        warnings.append('EPUB 没有页码信息，引文改注章节（如“第三章”），不注页码。')
    meta = {'format': 'epub', 'has_printed_pages': any_pages, 'unit_kind': 'epub_page' if any_pages else 'epub_doc'}
    return units, meta, starts


# ---------------------------------------------------------------- DOCX / TXT / MD

def chunk_paragraphs(paras, loc_prefix, size=2500):
    """把段落切成约 size 字的单元，返回 (units, heading_marks)。"""
    units, marks = [], []
    cur, cur_len = [], 0
    for text, is_head in paras:
        if is_head and cur:
            units.append(cur)
            cur, cur_len = [], 0
        if is_head:
            marks.append((len(units), len(cur), text.strip()))
        cur.append(text)
        cur_len += len(text)
        if cur_len >= size:
            units.append(cur)
            cur, cur_len = [], 0
    if cur:
        units.append(cur)
    res = [{'i': k, 'pdf': None, 'page': None, 'page_status': 'unknown', 'src': 'text', 'lines': u,
            'loc': f'{loc_prefix}{k + 1}'} for k, u in enumerate(units)]
    return res, marks


def load_docx(path, args, warnings):
    import docx
    d = docx.Document(str(path))
    paras = []
    for p in d.paragraphs:
        style = (p.style.name or '').lower() if p.style is not None else ''
        is_head = (('heading 1' in style or '标题 1' in style or style == 'title' or 'heading 2' in style
                    or '标题 2' in style) and p.text.strip()) or is_heading(p.text)
        paras.append((p.text, bool(is_head)))
    units, marks = chunk_paragraphs(paras, '位置')
    warnings.append('DOCX 没有固定页码，引文改注章节。')
    return units, {'format': 'docx', 'has_printed_pages': False, 'unit_kind': 'chunk'}, marks


def load_text(path, args, warnings):
    raw = Path(path).read_bytes()
    for enc in ('utf-8', 'gb18030', 'big5', 'utf-16'):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode('utf-8', errors='replace')
    md = str(path).lower().endswith(('.md', '.markdown'))
    paras = []
    for line in text.splitlines():
        s = line.strip()
        is_head = False
        if md and re.match(r'^#{1,2}\s+\S', s):
            is_head = True
            line = re.sub(r'^#{1,2}\s+', '', s)
        elif is_heading(s):
            is_head = True
        paras.append((line, is_head))
    units, marks = chunk_paragraphs(paras, '位置')
    warnings.append('纯文本没有页码，引文改注章节。')
    return units, {'format': 'md' if md else 'txt', 'has_printed_pages': False, 'unit_kind': 'chunk'}, marks


# ---------------------------------------------------------------- 切章

def detect_headings(units):
    found, toc_units = [], set()
    seen = set()
    for u in units:
        lines = u['lines']
        ne = [(k, l.strip()) for k, l in enumerate(lines) if l.strip()]
        hits = [s for _, s in ne if is_heading(s)]
        if any(TOC_TITLE_RE.match(s) for _, s in ne[:5]) or len(hits) >= 4:
            toc_units.add(u['i'])
            continue
        for pos, (k, s) in enumerate(ne):
            if not is_heading(s):
                continue
            title = s
            m = CHAPTER_RE.match(s)
            if m and nonspace_len(s) <= nonspace_len(m.group(0)) + 1 and pos + 1 < len(ne):
                nxt = ne[pos + 1][1]
                if (len(nxt) <= 30 and not is_heading(nxt) and not NUM_LINE.match(nxt)
                        and not re.search(r'[。！？；，]$', nxt)):
                    title = s + ' ' + nxt
            key = re.sub(r'\s', '', title)
            if key in seen:
                continue
            seen.add(key)
            found.append((u['i'], k, title))
    return found, toc_units


def parse_toc_file(path, units):
    starts = []
    by_pdf = {u['pdf']: u['i'] for u in units if u.get('pdf')}
    by_page = {}
    for u in units:
        if u.get('page') and u['page'] not in by_page:
            by_page[str(u['page'])] = u['i']
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        if '\t' not in line:
            raise SystemExit(f'--toc 行格式应为“标题<TAB>起点”：{line}')
        title, pos = line.rsplit('\t', 1)
        pos = pos.strip()
        m = re.match(r'^(?:p|页)\s*(\w+)$', pos, re.I)
        if m:
            ui = by_page.get(m.group(1))
        else:
            ui = by_pdf.get(int(pos)) if pos.isdigit() else None
        if ui is None:
            raise SystemExit(f'--toc 找不到起点：{line}')
        # 在该页里找标题行的位置
        k0 = 0
        key = re.sub(r'\s', '', title)[:6]
        for k, l in enumerate(units[ui]['lines']):
            if key and key in re.sub(r'\s', '', l):
                k0 = k
                break
        starts.append((ui, k0, title.strip()))
    return starts


def outline_to_starts(outline, units):
    by_pdf = {u['pdf']: u['i'] for u in units if u.get('pdf')}
    starts = []
    for title, p in outline:
        ui = by_pdf.get(p)
        if ui is None:
            continue
        k0 = 0
        key = re.sub(r'\s', '', title)[:6]
        for k, l in enumerate(units[ui]['lines']):
            if key and key in re.sub(r'\s', '', l):
                k0 = k
                break
        starts.append((ui, k0, title))
    return starts


def build_chapters(units, starts, warnings):
    starts = sorted(set(starts), key=lambda x: (x[0], x[1]))
    # 同一位置的多个标题合并
    merged = []
    for s in starts:
        if merged and merged[-1][0] == s[0] and abs(merged[-1][1] - s[1]) <= 3:
            merged[-1] = (merged[-1][0], merged[-1][1], merged[-1][2] + ' / ' + s[2])
        else:
            merged.append(s)
    starts = merged
    if not starts or starts[0][:2] != (0, 0):
        starts = [(0, 0, '（前置部分：封面、版权页、目录等）')] + starts
    chapters = []
    for c, (ui, k, title) in enumerate(starts):
        end = starts[c + 1] if c + 1 < len(starts) else (len(units), 0, None)
        segs = []  # (unit index, lines)
        for uj in range(ui, min(end[0], len(units) - 1) + 1):
            if uj >= len(units):
                break
            lines = units[uj]['lines']
            a = k if uj == ui else 0
            b = end[1] if uj == end[0] else len(lines)
            if uj == end[0] and b == 0:
                continue
            segs.append((uj, lines[a:b]))
        chapters.append({'title': title, 'segs': segs})
    # 过短的部分（如“第一部”扉页）并入下一章
    out = []
    pending = None
    for ch in chapters:
        n = sum(nonspace_len('\n'.join(l)) for _, l in ch['segs'])
        if pending:
            ch['title'] = pending['title'] + ' / ' + ch['title']
            ch['segs'] = pending['segs'] + ch['segs']
            pending = None
        is_part = bool(PART_RE.match(ch['title'].split(' / ')[-1].strip()))
        if (n < 40 or (is_part and n < 300)) and ch is not chapters[-1] and not ch['title'].startswith('（前置'):
            pending = ch
            continue
        out.append(ch)
    if pending:
        out.append(pending)
    for c, ch in enumerate(out):
        ch['idx'] = c
        if ch['title'].startswith('（前置'):
            ch['kind'] = 'front'
        else:
            last = ch['title'].split(' / ')[-1]
            ch['kind'] = heading_kind(last)
            if any(CHAPTER_RE.match(t.strip()) for t in ch['title'].split(' / ')):
                ch['kind'] = 'chapter'
    return out


def auto_chunks(units, warnings, pages_per=20):
    warnings.append('没有识别出章节标题，已按每 {} 页自动切分。请对照书的目录，用 --toc 指定章节起点后重新运行。'.format(pages_per))
    starts = []
    for k in range(0, len(units), pages_per):
        u = units[k]
        a = units[k].get('pdf') or k + 1
        b = units[min(len(units), k + pages_per) - 1].get('pdf') or min(len(units), k + pages_per)
        starts.append((k, 0, f'第{k // pages_per + 1}段（自动切分，单元 {a}–{b}）'))
    return starts


# ---------------------------------------------------------------- 输出

def marker(u):
    if u.get('pdf'):
        return f"=== [pdf:{u['pdf']}] [页:{u['page'] or '?'}] ==="
    if u.get('page'):
        return f"=== [页:{u['page']}] [{u.get('loc', '')}] ==="
    return f"=== [{u.get('loc') or '位置' + str(u['i'] + 1)}] ==="


def safe_name(s, n=24):
    s = re.sub(r'[\\/:*?"<>|\s]+', '_', s).strip('_')
    return s[:n] or 'untitled'


def page_range(units, segs, key):
    vals = [units[uj].get(key) for uj, _ in segs if units[uj].get(key)]
    if not vals:
        return '', ''
    return str(vals[0]), str(vals[-1])


def main():
    ap = argparse.ArgumentParser(description='把书预处理成按章切分、带页码标记的工作目录。')
    ap.add_argument('book')
    ap.add_argument('--out', help='工作目录（默认：./book_work/<文件名>）')
    ap.add_argument('--toc', help='手工章节表：每行“标题<TAB>起点”，起点为 PDF 页序或 p+印刷页码')
    ap.add_argument('--ocr', action='store_true', help='对缺少文字层的页做 OCR（扫描版整本 OCR）')
    ap.add_argument('--ocr-all', action='store_true', help='对所有页做 OCR')
    ap.add_argument('--auto-ocr', action='store_true', help='检测到扫描版时自动 OCR')
    ap.add_argument('--lang', default='chi_sim+eng', help='OCR 语言，默认 chi_sim+eng')
    ap.add_argument('--jobs', type=int, default=4, help='OCR 并行线程数')
    ap.add_argument('--pages', nargs=2, type=int, metavar=('起', '止'), help='只 OCR 这一段 PDF 页')
    ap.add_argument('--tessdata', help='OCR 语言包所在目录（设置 TESSDATA_PREFIX）')
    ap.add_argument('--ocr-engine', choices=['auto', 'rapidocr', 'tesseract'], default='auto',
                    help='OCR 引擎：auto（默认，优先 RapidOCR，装不上再用 tesseract）')
    args = ap.parse_args()
    if args.tessdata:
        import os
        global TESS_ENV
        TESS_ENV = dict(os.environ, TESSDATA_PREFIX=str(Path(args.tessdata).expanduser()))

    path = Path(args.book)
    if not path.exists():
        raise SystemExit(f'找不到文件：{path}')
    out = Path(args.out) if args.out else Path('book_work') / safe_name(path.stem, 40)
    out.mkdir(parents=True, exist_ok=True)
    args.ocr_cache = out / 'ocr_cache'
    warnings = []
    ext = path.suffix.lower()
    outline = []
    if ext == '.pdf':
        units, meta, outline = load_pdf(path, args, warnings)
        heading_starts = None
    elif ext == '.epub':
        units, meta, heading_starts = load_epub(path, args, warnings)
    elif ext == '.docx':
        units, meta, heading_starts = load_docx(path, args, warnings)
    elif ext in ('.txt', '.md', '.markdown', '.text'):
        units, meta, heading_starts = load_text(path, args, warnings)
    elif ext in ('.mobi', '.azw', '.azw3', '.kfx'):
        conv = shutil.which('ebook-convert')
        if not conv:
            raise SystemExit('MOBI/AZW3 需要先转换：请用户用 Calibre 转成 EPUB 或 PDF 后再上传，'
                             '或在本机安装 calibre（提供 ebook-convert 命令）。')
        epub = out / (path.stem + '.epub')
        subprocess.run([conv, str(path), str(epub)], check=True)
        units, meta, heading_starts = load_epub(epub, args, warnings)
    else:
        raise SystemExit(f'暂不支持的格式：{ext}（支持 pdf / epub / docx / txt / md）')

    # 部首字符（⽅⻚⻜⻄ 等）换回正常汉字，否则摘录和核对都会出错
    fixed = 0
    for u in units:
        new = [fix_cjk_compat(l) for l in u['lines']]
        fixed += sum(1 for a, b in zip(u['lines'], new) for x, y in zip(a, b) if x != y)
        u['lines'] = new
    outline = [(fix_cjk_compat(t), p) for t, p in outline]
    if heading_starts:
        heading_starts = [(a, b, fix_cjk_compat(t)) for a, b, t in heading_starts]
    if fixed:
        warnings.append(f'已把 {fixed} 处“部首字符”（如 ⽅⻚⻜⻄，PDF 字体造成）换回正常汉字。')

    # 章节起点：手工 > PDF 书签 > EPUB 目录/DOCX 标题 > 正则识别 > 自动切分
    toc_units = set()
    source = ''
    if args.toc:
        starts = parse_toc_file(args.toc, units)
        source = '手工章节表（--toc）'
    elif outline:
        starts = outline_to_starts(outline, units)
        source = 'PDF 书签'
    elif heading_starts:
        starts = heading_starts
        source = 'EPUB 目录' if meta['format'] == 'epub' else '标题样式/标题行'
    else:
        found, toc_units = detect_headings(units)
        starts = found
        source = '标题行识别'
    if len(starts) < 2 and not args.toc and source != '标题行识别':
        found, toc_units = detect_headings(units)
        if len(found) >= 2:
            starts, source = found, '标题行识别'
    if len(starts) < 2:
        starts = auto_chunks(units, warnings)
        source = '自动切分'

    chapters = build_chapters(units, starts, warnings)
    for ch in chapters:
        for uj, _ in ch['segs']:
            units[uj].setdefault('chapter', ch['idx'])

    lang = 'zh' if is_cjk_dominant('\n'.join('\n'.join(u['lines']) for u in units[:200])) else 'en'
    pages_text = []
    if meta['format'] == 'pdf':
        tkeys = set()
        for ch in chapters:
            for t in ch['title'].split(' / '):
                tkeys.add(t)
        keep = set() if source == '自动切分' else {(ui, k) for ui, k, _ in starts}
        stripped = strip_edges([u['lines'] for u in units], tkeys, keep)
    else:
        stripped = [u['lines'] for u in units]
    for u, lines in zip(units, stripped):
        u['text'] = '\n'.join(lines).strip('\n')
        pages_text.append(u['text'])

    # 写 pages.jsonl / book.txt
    with open(out / 'pages.jsonl', 'w', encoding='utf-8') as f:
        for u in units:
            rec = {k: u.get(k) for k in ('i', 'pdf', 'page', 'page_status', 'src', 'ocr_conf', 'chapter', 'loc', 'text')}
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    with open(out / 'book.txt', 'w', encoding='utf-8') as f:
        for u in units:
            f.write(marker(u) + '\n' + u['text'] + '\n\n')

    # 章节文件：保留章内的页标记，但只取本章那一段文字
    chdir = out / 'chapters'
    if chdir.exists():
        shutil.rmtree(chdir)
    chdir.mkdir()
    rows = []
    strip_map = {u['i']: set(stripped[u['i']]) if meta['format'] == 'pdf' else None for u in units}
    for ch in chapters:
        buf = [f"# {ch['title']}", '']
        for uj, lines in ch['segs']:
            keep = lines
            if meta['format'] == 'pdf':
                allowed = strip_map[uj]
                keep = [l for l in lines if l in allowed]
            buf.append(marker(units[uj]))
            buf.append('\n'.join(keep).strip('\n'))
            buf.append('')
        text = '\n'.join(buf)
        fname = f"{ch['idx']:02d}_{safe_name(ch['title'])}.txt"
        (chdir / fname).write_text(text, encoding='utf-8')
        body = '\n'.join('\n'.join(l) for _, l in ch['segs'])
        p1, p2 = page_range(units, ch['segs'], 'page')
        d1, d2 = page_range(units, ch['segs'], 'pdf')
        rows.append({'idx': ch['idx'], 'title': ch['title'], 'kind': ch['kind'], 'pdf_start': d1, 'pdf_end': d2,
                     'page_start': p1, 'page_end': p2, 'chars': nonspace_len(body),
                     'est_tokens': estimate_tokens(body), 'file': f'chapters/{fname}'})
    head = ['idx', 'title', 'kind', 'pdf_start', 'pdf_end', 'page_start', 'page_end', 'chars', 'est_tokens', 'file']
    with open(out / 'chapters.tsv', 'w', encoding='utf-8') as f:
        f.write('\t'.join(head) + '\n')
        for r in rows:
            f.write('\t'.join(str(r[h]).replace('\t', ' ') for h in head) + '\n')

    # 版本线索
    edition = []
    first_body = next((uj for ch in chapters if ch['kind'] in ('chapter', 'intro') for uj, _ in ch['segs'][:1]), 6)
    for u in units[:max(3, min(15, first_body + 1))]:
        for l in u['lines']:
            s = l.strip()
            if s and len(s) <= 80 and EDITION_RE.search(s) and s not in edition:
                edition.append(s)
    edition = edition[:15]

    total_text = '\n'.join(pages_text)
    meta.update({'source': str(path.resolve()), 'lang': lang, 'units': len(units),
                 'chars': nonspace_len(total_text), 'est_tokens': estimate_tokens(total_text),
                 'chapter_source': source, 'toc_pages_pdf': sorted(units[i]['pdf'] for i in toc_units if units[i].get('pdf')),
                 'created': time.strftime('%Y-%m-%d %H:%M')})
    (out / 'meta.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')

    # overview.md
    kinds = {'front': '前置', 'intro': '序/导论', 'chapter': '正文', 'outro': '结语/后记', 'back': '附录/文献/索引'}
    L = [f'# 预处理概况：{path.name}', '']
    L.append(f"- 格式：{meta['format']}；单元数：{len(units)}（{meta['unit_kind']}）")
    if meta['format'] == 'pdf':
        st = Counter(u['page_status'] for u in units)
        L.append(f"- PDF 页数：{meta['pdf_pages']}；扫描版：{'是' if meta['scanned'] else '否'}；OCR：{'已做' if meta['ocr'] else '未做'}")
        L.append(f"- 印刷页码：{'已对齐' if meta['has_printed_pages'] else '未能识别'}"
                 f"（识别 {st.get('detected', 0)} 页，推断 {st.get('inferred', 0)} 页，罗马数字 {st.get('roman', 0)} 页，未知 {st.get('unknown', 0)} 页）")
    else:
        L.append(f"- 页码：{'有（EPUB 页码标记）' if meta['has_printed_pages'] else '无，引文注章节'}")
    L.append(f"- 正文字数（不计空白）：约 {meta['chars']:,}；估计 token：约 {meta['est_tokens']:,}")
    L.append(f'- 章节来源：{source}')
    if meta.get('toc_pages_pdf'):
        L.append(f"- 目录页（PDF 页序）：{', '.join(map(str, meta['toc_pages_pdf']))} —— 请用它核对下表的切章是否正确")
    L.append('')
    if edition:
        L.append('## 版本线索（版权页附近）')
        L += [f'- {e}' for e in edition]
        L.append('')
    L.append('## 章节表')
    L.append('| # | 标题 | 类别 | 印刷页 | PDF 页 | 字数 | 估计 token |')
    L.append('|---|---|---|---|---|---|---|')
    for r in rows:
        pg = f"{r['page_start']}–{r['page_end']}" if r['page_start'] else '—'
        pdf = f"{r['pdf_start']}–{r['pdf_end']}" if r['pdf_start'] else '—'
        L.append(f"| {r['idx']} | {r['title']} | {kinds.get(r['kind'], r['kind'])} | {pg} | {pdf} | {r['chars']:,} | {r['est_tokens']:,} |")
    L.append('')
    if warnings:
        L.append('## 警告')
        L += [f'- {w}' for w in warnings]
        L.append('')
    (out / 'overview.md').write_text('\n'.join(L), encoding='utf-8')
    print('\n'.join(L))
    print(f'\n工作目录：{out}')


if __name__ == '__main__':
    main()
