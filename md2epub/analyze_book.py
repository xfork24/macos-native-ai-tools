"""从 MinerU 文本与页面证据生成书籍配置；不使用模型或网络。"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import defaultdict
from pathlib import Path

from markdown_regions import mask_code

TOP = re.compile(r'^(第\s*[零〇一二三四五六七八九十百\d]+\s*[章篇]|预篇|附录\s*[A-FＡ-Ｆ])\s*(.*)$')
SECTION = re.compile(r'^(?:§\s*\d+|(?:\d+|[A-Z])\.\d+)(?!\d)')
PAGE_END = re.compile(r'\s*(?:[.…·]{2,}|\s{2,})\s*[（(]?\d+[)）]?\s*$')


def norm(s):
    return re.sub(r'[\s\\_*#]', '', s)


def top_key(s):
    m = TOP.match(s.strip())
    return norm(m[1]) if m else None


def load_blocks(path: Path):
    """v1 为首选；v2 按页面展开，保持原有顺序。"""
    data = json.loads(path.read_text(encoding='utf-8'))
    if path.name.endswith('_content_list.json'):
        return data
    blocks = []
    for page, entries in enumerate(data):
        for entry in entries:
            c = entry.get('content', {})
            typ = entry.get('type', '')
            pieces = c.get(f'{typ}_content', [])
            text = ''.join(str(x.get('content', '')) for x in pieces if isinstance(x, dict))
            typ = {'title': 'text', 'paragraph': 'text', 'page_header': 'header', 'page_footer': 'footer'}.get(typ, typ)
            blocks.append({'type': typ, 'text': text, 'text_level': c.get('level'), 'page_idx': page})
    return blocks


def discover(directory: Path, md_glob='*.md'):
    candidates = sorted(directory.glob(md_glob))
    candidates = [p for p in candidates if p.is_file()]
    if len(candidates) != 1:
        raise ValueError(f'需要唯一 Markdown，找到 {len(candidates)} 个；请在 --config 中指定 md_glob')
    md = candidates[0]
    paths = [directory / (md.stem + suffix) for suffix in ('_content_list.json', '_content_list_v2.json')]
    content = next((p for p in paths if p.exists()), None)
    return md, content


def analyze(directory: Path, overrides: dict | None = None):
    overrides = overrides or {}
    cfg = json.loads((Path(__file__).parent / 'book_config.template.json').read_text())
    md, content = discover(directory, overrides.get('md_glob', '*.md'))
    source = md.read_text(encoding='utf-8')
    blocks = load_blocks(content) if content else []
    warnings, changes = [], []
    if not blocks:
        warnings.append('缺少内容列表 JSON，按 Markdown 保守识别；无法恢复扫描图中的标题。')
    pages = defaultdict(list)
    anchors = {}
    cursor = 0
    locations = []
    for block in blocks:
        page = block.get('page_idx', 0)
        pages[page].append(block)
        if block.get('type') in ('header', 'footer', 'page_number'):
            continue
        text = block.get('text') or block.get('code_body') or ''
        if not text:
            continue
        pos = source.find(text, cursor)
        if pos < 0 and len(text) > 80:
            pos = source.find(text[:80], cursor)
        if pos >= 0:
            start = source.rfind('\n', 0, pos) + 1
            anchors.setdefault(page, start)
            locations.append({'offset': start, 'page': page + 1, 'type': block.get('type')})
            cursor = pos + min(len(text), 80)
    ordered = sorted(anchors.items())
    ends = {p: ordered[i+1][1] if i+1 < len(ordered) else len(source) for i, (p, _) in enumerate(ordered)}
    # Dense contents pages are recognized only in the opening portion.
    toc_pages = set()
    for page, entries in pages.items():
        if page > min(80, max(pages, default=0) * .25):
            continue
        text = '\n'.join(x.get('text', '') for x in entries if x.get('type') == 'text')
        lines = [x.strip() for x in text.splitlines() if x.strip()]
        is_label = any(norm(x) in ('目录', '第一册目录', '第二册目录', '第三册目录') for x in lines)
        listing = sum(len(x) < 100 and '。' not in x and bool(TOP.match(x) or SECTION.match(x) or PAGE_END.search(x)) for x in lines)
        if (is_label and listing >= 3) or (page-1 in toc_pages and len(lines) >= 8 and listing / len(lines) >= .7):
            toc_pages.add(page)
    outline = {}
    for page in sorted(toc_pages):
        for block in pages[page]:
            for line in block.get('text', '').splitlines():
                title = PAGE_END.sub('', line.strip())
                m = TOP.match(title)
                if m and m[2].strip():
                    outline.setdefault(norm(m[1]), f'{m[1]} {m[2]}'.strip())
    edits = []
    for page in sorted(toc_pages):
        if page in anchors:
            edits.append((anchors[page], ends[page], '', 'remove_printed_toc', page+1))
    if not toc_pages:
        # Only unambiguous bounded Markdown TOCs may be removed without page data.
        masked = mask_code(source)
        for m in re.finditer(r'^#{1,6}\s*目\s*录\s*$', masked, re.M):
            after = masked[m.end():]
            end = re.search(r'^#{1,6}\s*第\s*[\d一二三四五六七八九十]+\s*章', after, re.M)
            if end and len(PAGE_END.findall(after[:end.start()])) >= 3:
                edits.append((m.start(), m.end()+end.start(), '', 'remove_printed_toc', None))
            else:
                warnings.append(f'第 {source[:m.start()].count(chr(10))+1} 行目录边界不确定，保留原文。')
    # Recover chapter-title pages misclassified as running headers by MinerU.
    seen = set()
    for page in sorted(pages):
        if page in toc_pages or page not in anchors:
            continue
        start, end = anchors[page], ends[page]
        page_source = source[start:end]
        for h in re.finditer(r'^#{1,6}\s+(.+)$', mask_code(source[:end]), re.M):
            if not any(a <= h.start() < b for a,b,_,reason,_ in edits if reason == 'remove_printed_toc'):
                key = top_key(h[1])
                if key: seen.add(key)
        header_lines = [x.get('text', '').strip() for x in pages[page] if x.get('type') == 'header']
        header_candidates = header_lines + [' '.join(header_lines)]
        for head in header_candidates:
            key = top_key(head)
            if key not in outline or key in seen:
                continue
            seen.add(key)
            expected = outline[key]
            m = TOP.match(expected)
            existing = list(re.finditer(r'^#{1,6}\s+(.+)$', mask_code(page_source), re.M))
            matching = next((h for h in existing if top_key(h[1]) == key), None)
            if matching:
                continue
            title_match = next((h for h in existing if norm(h[1]) == norm(m[2])), None)
            if title_match:
                a, b = start+title_match.start(), start+title_match.end()
                edits.append((a, b, '# '+expected, 'restore_heading_number', page+1))
            else:
                edits.append((start, start, '# '+expected+'\n\n', 'restore_heading_from_toc_and_header', page+1))
    # Apply only non-overlapping evidence-based edits, retaining original offsets.
    last = len(source)+1
    text = source
    for a, b, replacement, reason, page in sorted(edits, reverse=True):
        if b > last:
            continue
        changes.append({'action': reason, 'page': page, 'line': source[:a].count('\n')+1,
                        'source_start': a, 'source_end': b, 'replacement': replacement})
        text = text[:a]+replacement+text[b:]
        last = a
    # Reclassify headings without touching code. Number and title may be separated.
    lines = text.splitlines(keepends=True)
    visible = mask_code(text).splitlines(keepends=True)
    body_started = False
    appendix = False
    headings = []
    seen_tops = set()
    for i, line in enumerate(visible):
        match = re.match(r'^#{1,6}\s+(.+?)\s*$', line)
        if not match:
            continue
        title = match[1]
        top = TOP.match(title)
        if top and not top[2].strip():
            j = i+1
            while j < len(lines) and not lines[j].strip():
                j += 1
            candidate = re.match(r'^#{1,6}\s+(.+?)\s*$', visible[j]) if j < len(lines) else None
            if candidate and not (TOP.match(candidate[1]) or SECTION.match(candidate[1]) or candidate[1] == '本章内容' or candidate[1].startswith(('练习', '代码清单'))):
                title += ' '+candidate[1]
                lines[j] = ''; visible[j] = ''
                changes.append({'action': 'merge_heading', 'normalized_line': i+1, 'replacement': title})
                top = TOP.match(title)
        if top:
            if top[1].startswith('附录'):
                level = 2 if norm(top[1]) in seen_tops else 1
                appendix = True
            else:
                level = 2 if appendix and '章' in top[1] else 1
            seen_tops.add(norm(top[1]))
            body_started = True
        elif SECTION.match(title):
            level = 3 if re.match(r'^(?:\d+|[A-Z])\.\d+\.\d+', title) else 2
            body_started = True
        elif not body_started:
            level = 1 if title in ('前 言', '前言', '致 谢', '致谢', '关于本书', '作者简介', '译者简介', '主审人简介') or not headings else 2
        elif title in ('后记', '重排本说明', '索引', '参考文献', '参考文献和延伸阅读'):
            level = 1
        else:
            level = 3
        lines[i] = '#' * level + ' ' + title + '\n'
        headings.append({'level': level, 'title': title})
    text = ''.join(lines)
    if not headings or headings[0]['level'] != 1:
        text = '# 正文\n\n'+text
    # Metadata: source text first, filename is only a fallback.
    first_title = re.search(r'^#\s+(.+)$', mask_code(source), re.M)
    fallback_title = re.sub(r'\s*\([^)]*\)\s*$', '', directory.parent.name)
    title = first_title[1].strip() if first_title and first_title.start() < 200 else fallback_title
    title = re.sub(r'\s*\([^)]*\)\s*$', '', title).strip()
    author = []
    translators = []
    reviewers = []
    for line in source[:5000].splitlines():
        m = re.match(r'^(?:\[[^]]+\]\s*)?([^#\n]{2,100}?)\s+著(?:\s|$)', line)
        if m:
            author = [m[1].strip()]; break
    for line in source[:5000].splitlines():
        m = re.match(r'^([^#\n]{2,100}?)\s+译(?:\s|$)', line)
        if m:
            translators = [
                name for name in re.split(r'[\s　]+', m[1].strip()) if name
            ]
            break
    for line in source[:5000].splitlines():
        m = re.match(r'^([^#\n]{2,100}?)\s+主审(?:\s|$)', line)
        if m:
            reviewers = [
                name for name in re.split(r'[\s　]+', m[1].strip()) if name
            ]
            break
    if not author:
        warnings.append('未可靠识别作者，元数据作者留空。')
    identity = hashlib.sha256(source.encode()).hexdigest()
    meta = {'title': title, 'author': author, 'translator': translators,
            'reviewer': reviewers,
            'lang': 'zh-CN' if re.search(r'[\u4e00-\u9fff]', source[:2000]) else 'en',
            'identifier': 'urn:uuid:'+str(uuid.uuid5(uuid.NAMESPACE_URL, identity))}
    cfg.update({'book_name': title, 'md_glob': md.name, 'metadata': meta, 'prepared': True})
    cfg.update({k:v for k,v in overrides.items() if k != 'metadata'})
    cfg['metadata'] = {**meta, **overrides.get('metadata', {})}
    cfg['book_name'] = overrides.get('book_name', cfg['metadata']['title'])
    cfg['book_name'] = re.sub(r'[/\\\x00-\x1f]', '_', cfg['book_name'])
    report = {'source': str(md), 'content_list': str(content) if content else None, 'source_sha256': identity,
              'changes': list(reversed(changes)), 'warnings': warnings, 'headings': headings,
              'source_locations': locations, 'toc_outline': outline}
    return text, cfg, report
