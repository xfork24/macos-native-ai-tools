#!/usr/bin/env python3
"""EPUB 结构与内容覆盖检查。支持独立 CLI 和无全局状态的 Python API。"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import posixpath
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET
import zipfile


def digest_code(text):
    return hashlib.sha256(text.rstrip('\n').encode()).hexdigest()


def validate_epub(path: Path, expected: dict | None = None, mode: str | None = None):
    expected = expected or {}
    checks, warnings = [], []
    stats = {'math': 0, 'fallback': 0, 'code_hashes': [], 'tables': 0, 'mode': mode}
    def record(name, ok, detail=''):
        checks.append({'name': name, 'ok': bool(ok), 'detail': detail})
    def local(tag):
        return tag.rsplit('}', 1)[-1]
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist()); infos = z.infolist()
            record('mimetype', bool(infos) and infos[0].filename == 'mimetype' and infos[0].compress_type == zipfile.ZIP_STORED and z.read('mimetype') == b'application/epub+zip')
            record('unique ZIP entries', len(names) == len(infos))
            container = ET.fromstring(z.read('META-INF/container.xml'))
            opf_path = container.find('.//{*}rootfile').get('full-path')
            opf = ET.fromstring(z.read(opf_path)); base = posixpath.dirname(opf_path)
            manifest = {x.get('id'): x for x in opf.findall('.//{*}manifest/{*}item')}
            if 'contributors' in expected:
                by_id = {
                    x.get('id'): ''.join(x.itertext())
                    for x in opf.findall('.//{*}contributor')
                    if x.get('id')
                }
                actual = {'trl': [], 'rev': []}
                for role in opf.findall('.//{*}meta[@property="role"]'):
                    key = (role.get('refines') or '').removeprefix('#')
                    value = ''.join(role.itertext()).strip()
                    if value in actual and key in by_id:
                        actual[value].append(by_id[key])
                record(
                    'contributor roles',
                    all(
                        Counter(actual.get(role, [])) == Counter(names)
                        for role, names in expected['contributors'].items()
                    ),
                    str(actual),
                )
            def resolve(origin, href):
                return posixpath.normpath(posixpath.join(posixpath.dirname(origin), unquote(urlsplit(href).path)))
            record('manifest resources', all(resolve(opf_path, x.get('href','')) in names for x in manifest.values()))
            spine = [x.get('idref') for x in opf.findall('.//{*}spine/{*}itemref')]
            record('spine references', bool(spine) and all(x in manifest for x in spine))
            spine_paths = {resolve(opf_path, manifest[x].get('href')) for x in spine if x in manifest}
            trees = {}
            for name in sorted(names):
                if name.endswith(('.xhtml', '.svg', '.ncx')):
                    try:
                        trees[name] = ET.fromstring(z.read(name))
                    except ET.ParseError as exc:
                        record('XML well-formed: '+name, False, str(exc))
            record('XHTML well-formed', all(n in trees for n in names if n.endswith('.xhtml')))
            navitem = next((x for x in manifest.values() if 'nav' in x.get('properties','').split()), None)
            navpath = resolve(opf_path, navitem.get('href')) if navitem is not None else None
            ncxitem = next((x for x in manifest.values() if x.get('media-type') == 'application/x-dtbncx+xml'), None)
            record('nav and NCX present', navpath in trees and ncxitem is not None)
            missing, bad_anchors, empty_src = [], [], []
            for name, tree in trees.items():
                if not name.endswith('.xhtml'):
                    continue
                for e in tree.iter():
                    for attr in ('src', 'href', '{http://www.w3.org/1999/xlink}href'):
                        if attr not in e.attrib:
                            continue
                        href = e.get(attr); parts = urlsplit(href)
                        if not href:
                            if attr == 'src': empty_src.append(name)
                            continue
                        if parts.scheme or parts.netloc:
                            if attr == 'src': missing.append(name+': external resource '+href)
                            continue
                        target = resolve(name, href) if parts.path else name
                        if target not in names:
                            missing.append(name+': '+href)
                        elif parts.fragment and target in trees and not any(x.get('id') == unquote(parts.fragment) for x in trees[target].iter()):
                            bad_anchors.append(name+': '+href)
                if name not in spine_paths:
                    continue
                body = tree.find('.//{*}body')
                if body is None:
                    continue
                for e in body.iter():
                    tag, classes = local(e.tag), e.get('class','').split()
                    if tag == 'math': stats['math'] += 1
                    if tag == 'img' and '/eq-' in e.get('src',''): stats['math'] += 1
                    if 'msym' in classes: stats['math'] += 1
                    if 'math-raw' in classes or (tag == 'span' and 'math' in classes and e.find('.//{*}math') is None): stats['fallback'] += 1
                    if tag == 'pre': stats['code_hashes'].append(digest_code(''.join(e.itertext())))
                    if tag == 'table': stats['tables'] += 1
                record('no unresolved placeholders: '+name, not re.search(r'⟂MATH[DI]:[0-9a-f]+⟂|MD2EPUBCODE\d+END', ''.join(body.itertext())))
                if mode in ('svg','png'):
                    record('external formula media: '+name, not any(local(e.tag) == 'svg' for e in body.iter()))
                    bad_format = [e.get('src') for e in body.iter() if local(e.tag)=='img' and '/eq-' in e.get('src','') and not e.get('src').endswith('.'+mode)]
                    record('formula media format: '+name, not bad_format)
            record('resource references', not missing and not empty_src, (missing+empty_src)[:10])
            record('link anchors', not bad_anchors, bad_anchors[:10])
            html_manifest = {resolve(opf_path,x.get('href')) for x in manifest.values() if x.get('media-type')=='application/xhtml+xml'}
            record('spine completeness', not (html_manifest-spine_paths-{navpath}))
            if navpath in trees:
                nav = trees[navpath].find('.//{*}nav[@{http://www.idpf.org/2007/ops}type="toc"]')
                if nav is not None:
                    top = nav.find('./{*}ol')
                    stats['nav_top_level'] = len(top.findall('./{*}li')) if top is not None else 0
                    stats['nav_leaves'] = len(nav.findall('.//{*}li'))
                    nav_links = [e.get('href') for e in nav.findall('.//{*}a')]
                    if ncxitem is not None:
                        ncx = trees.get(resolve(opf_path,ncxitem.get('href')))
                        if ncx is not None:
                            ncx_path = resolve(opf_path, ncxitem.get('href'))

                            def canonical_links(origin, links):
                                return [
                                    (
                                        resolve(origin, href),
                                        unquote(urlsplit(href).fragment),
                                    )
                                    for href in links
                                ]

                            nav_targets = canonical_links(navpath, nav_links)
                            ncx_targets = canonical_links(
                                ncx_path,
                                [
                                    e.get('src')
                                    for e in ncx.findall('.//{*}content')
                                ],
                            )
                            # Pandoc includes its generated title page in
                            # NCX but intentionally omits it from the user
                            # TOC.  Require every nav target in the same NCX
                            # order while allowing such NCX-only landmarks.
                            cursor = iter(ncx_targets)
                            record(
                                'nav and NCX order',
                                all(any(item == target for item in cursor)
                                    for target in nav_targets),
                            )
                    for key in ('nav_top_level','nav_leaves'):
                        if key in expected: record(key, stats[key] == expected[key], f'{stats[key]} / {expected[key]}')
            if 'math_occurrences' in expected:
                total = stats['math']+stats['fallback']
                record('formula occurrence coverage', total == expected['math_occurrences'], f"rendered {stats['math']}, fallback {stats['fallback']}, expected {expected['math_occurrences']}")
            if 'min_math' in expected:
                record('minimum rendered formulas', stats['math'] >= expected['min_math'])
            if 'code_hashes' in expected:
                record('code content preserved', Counter(stats['code_hashes']) == Counter(expected['code_hashes']), f"{len(stats['code_hashes'])} / {len(expected['code_hashes'])}")
            if 'tables' in expected:
                record('table coverage', stats['tables'] == expected['tables'], f"{stats['tables']} / {expected['tables']}")
            if 'image_hashes' in expected:
                packaged = {hashlib.sha256(z.read(n)).hexdigest() for n in names if not n.endswith(('.xhtml','.opf','.ncx','.css'))}
                record('source images preserved', set(expected['image_hashes']) <= packaged)
            if stats['fallback']:
                warnings.append(f"{stats['fallback']} 处公式使用原文回退")
    except (OSError, ValueError, KeyError, AttributeError, ET.ParseError, zipfile.BadZipFile) as exc:
        record('read EPUB', False, str(exc))
    return {'ok': all(x['ok'] for x in checks), 'checks': checks, 'warnings': warnings, 'stats': stats}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('epub', type=Path)
    p.add_argument('--expect-top', type=int)
    p.add_argument('--expect-leaves', type=int)
    p.add_argument('--min-math', type=int)
    p.add_argument('--json', type=Path)
    a=p.parse_args(argv)
    expected={k:v for k,v in {'nav_top_level':a.expect_top,'nav_leaves':a.expect_leaves,'min_math':a.min_math}.items() if v is not None}
    result=validate_epub(a.epub, expected)
    for check in result['checks']:
        if not check['ok']: print('❌',check['name'],check['detail'])
    print(f"{'✅' if result['ok'] else '❌'} {sum(c['ok'] for c in result['checks'])}/{len(result['checks'])} checks; {result['stats']['math']} rendered formulas, {result['stats']['fallback']} fallbacks")
    if a.json: a.json.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['ok'] else 1

if __name__=='__main__':
    sys.exit(main())
