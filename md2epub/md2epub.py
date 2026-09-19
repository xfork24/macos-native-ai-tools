#!/usr/bin/env python3
"""MinerU 输出 → 三版 EPUB：自动配置、转换、校验与报告，无需模型服务。"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from datetime import datetime, timezone

import convert
from analyze_book import analyze, discover, TOP
from audit_png import audit_png
from validate import validate_epub, digest_code

TOOL = Path(__file__).resolve().parent


def write_json(path, data):
    convert.atomic_text(path, json.dumps(data,ensure_ascii=False,indent=2)+'\n')


def dependencies(modes):
    needed=['pandoc','pdftoppm']
    if any(m in modes for m in ('png','svg')): needed.append('node')
    if 'png' in modes: needed.append('rsvg-convert')
    missing=[x for x in needed if not shutil.which(x)]
    if missing: raise ValueError('缺少依赖：'+', '.join(missing))
    import yaml
    from PIL import Image
    if 'node' in needed:
        subprocess.run(['node','-e',"require('mathjax-full/js/mathjax.js')"],cwd=TOOL,check=True,capture_output=True)


def inventory(text, directory):
    """在进入各渲染分支之前独立使用 Pandoc AST 建立内容基线。"""
    proc=subprocess.run(['pandoc','--from','markdown','--to','json'],input=text.encode(),capture_output=True,check=True,cwd=directory)
    tree=json.loads(proc.stdout)
    codes, images=[],[]
    def visit(node):
        if isinstance(node,dict):
            if node.get('t')=='CodeBlock': codes.append(digest_code(node['c'][1]))
            if node.get('t')=='Image': images.append(node['c'][2][0])
            for v in node.values(): visit(v)
        elif isinstance(node,list):
            for v in node:visit(v)
    visit(tree)
    # Raw HTML images and tables are included in the HTML baseline too.
    html_proc=subprocess.run(['pandoc','--from','markdown','--to','html','--wrap=none'],input=text.encode(),capture_output=True,check=True,cwd=directory)
    rendered=html_proc.stdout.decode()
    images.extend(re.findall(r'<img[^>]*src="([^"]+)"',rendered))
    from urllib.parse import unquote,urlsplit
    hashes=[]
    for src in set(images):
        parts=urlsplit(src)
        if parts.scheme or parts.netloc: raise ValueError('不支持离线 EPUB 的远程图片：'+src)
        path=directory/unquote(parts.path)
        if not path.is_file():raise ValueError('源图片缺失：'+str(path))
        hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
    sections=convert._split_sections(text)
    return {'code_hashes':codes,'image_hashes':hashes,'tables':len(re.findall(r'<table(?:\s|>)',rendered)),
            'math_occurrences':sum(convert.count_math(text)),
            'nav_top_level':sum(s['level']==1 for s in sections), 'nav_leaves':len(sections)}


@contextlib.contextmanager
def build_lock(build):
    with (build/'.lock').open('a+') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('此输出目录已有转换任务正在运行')
        try:yield
        finally:fcntl.flock(lock,fcntl.LOCK_UN)


def run(directory: Path, out_dir: Path | None = None, modes=None, overrides=None, analyze_only=False):
    directory=directory.resolve();out=(out_dir or directory.parent).resolve()
    if not directory.is_dir():raise ValueError('MinerU 目录不存在：'+str(directory))
    modes=modes or ['mathml','svg','png'];overrides=overrides or {}
    dependencies(modes)
    build=out/'md2epub-build';build.mkdir(parents=True,exist_ok=True)
    with build_lock(build):
        text,cfg,analysis=analyze(directory,overrides)
        # Existing explicitly supplied book-specific configurations retain their semantics.
        if 'chapter_regex' in overrides and overrides.get('schema_version') != 2:
            cfg=json.loads(json.dumps(convert.DEFAULTS));cfg.update(overrides)
            md,_=discover(directory,cfg.get('md_glob','*.md'));text=md.read_text()
            analysis['warnings'].append('使用显式旧版书籍配置；沿用其正文裁剪与标题规则。')
        normalized=build/'normalized.md'
        convert.atomic_text(normalized,text)
        prepared=convert.preprocess(text,cfg)
        body=next((s['title'] for s in convert._split_sections(prepared) if TOP.match(s['title'])),None)
        if not body:body=next((s['title'] for s in convert._split_sections(prepared)), '正文')
        cfg.setdefault('metadata',{})['body_title']=body
        if cfg.get('colophon_enabled'):
            prepared=cfg['colophon_md'].rstrip()+'\n\n'+prepared
        baseline=inventory(prepared,directory)
        baseline['contributors'] = {
            'trl': cfg.get('metadata', {}).get('translator', []),
            'rev': cfg.get('metadata', {}).get('reviewer', []),
        }
        analysis['baseline']=baseline
        cfg['expected']={**cfg.get('expected',{}),'nav_top_level':baseline['nav_top_level'],'nav_leaves':baseline['nav_leaves']}
        config_path=build/'book_config.generated.json';write_json(config_path,cfg);write_json(build/'analysis.json',analysis)
        warnings=list(analysis['warnings'])
        report={'source':str(directory),'output':str(out),'status':'analyzed','warnings':warnings,'modes':{},'analysis':str(build/'analysis.json')}
        if analyze_only:
            write_json(build/'report.json',report);return report
        for mode in modes:
            print(f'\n📚 {mode}: 开始构建与验收',flush=True)
            mode_result={}
            failure=build/'failures.log'
            mode_failure=build/f'failures.{mode}.log'
            error_log=build/f'error.{mode}.log'
            # Diagnostics are current-run evidence.  Do not let a failed
            # converter inherit the preceding mode's shared failure log or
            # an earlier run's per-mode traceback.
            failure.unlink(missing_ok=True)
            mode_failure.unlink(missing_ok=True)
            error_log.unlink(missing_ok=True)
            try:
                with tempfile.TemporaryDirectory(prefix='pending-',dir=build) as pending:
                    argv=[str(directory),'--config',str(config_path),'--source-md',str(normalized),'--build-dir',str(build),'--out-dir',pending,'--math',mode]
                    convert.main(argv)
                    candidate=Path(pending)/(cfg['book_name']+'.'+mode+'.epub')
                    validation=validate_epub(candidate,baseline,mode)
                    mode_result['validation']=validation
                    if mode=='png' and validation['ok']:
                        audit=audit_png(candidate,cfg);mode_result['audit']=audit
                        validation['ok'] &= audit['ok']
                    # Retain current-run diagnostic per mode before the converter writes another one.
                    if failure.exists():shutil.copyfile(failure,mode_failure)
                    if not validation['ok']:
                        mode_result['status']='failed'
                        diagnostics=[
                            f"{c['name']}: {c.get('detail','')}".rstrip()
                            for c in validation['checks'] if not c['ok']
                        ]
                        diagnostics.extend(mode_result.get('audit',{}).get('errors',[]))
                        if not diagnostics:diagnostics=['EPUB 验收未通过']
                        mode_result['error']='；'.join(diagnostics[:3])
                        existing=mode_failure.read_text() if mode_failure.exists() else ''
                        detail='\n【构建/验收失败】\n'+'\n'.join(f'  - {x}' for x in diagnostics)+'\n'
                        convert.atomic_text(mode_failure,existing.rstrip()+"\n"+detail)
                        for c in validation['checks']:
                            if not c['ok']:print('❌',c['name'],c['detail'])
                        for detail in mode_result.get('audit',{}).get('errors',[]):
                            print('❌ PNG audit',detail)
                    else:
                        destination=out/candidate.name
                        os.replace(candidate,destination)
                        mode_result.update(status='success',file=str(destination),size_bytes=destination.stat().st_size)
                        warnings.extend(f'{mode}: {w}' for w in validation['warnings'])
                        warnings.extend(f'{mode}: {w}' for w in mode_result.get('audit',{}).get('warnings',[]))
            except Exception as exc:
                mode_result.update(status='failed',error=str(exc))
                trace=traceback.format_exc()
                convert.atomic_text(error_log,trace)
                convert.atomic_text(
                    mode_failure,
                    '【构建异常】\n'
                    f'  - {type(exc).__name__}: {exc}\n'
                    f'  - 完整堆栈：{error_log}\n',
                )
                print('❌',mode,str(exc),flush=True)
            report['modes'][mode]=mode_result
            write_json(build/'report.json',report)
        report['status']='failed' if any(x['status']=='failed' for x in report['modes'].values()) else 'success_with_warnings' if warnings else 'success'
        report['completed_at']=datetime.now(timezone.utc).isoformat()
        write_json(build/'validation.json',{m:r.get('validation',{}) for m,r in report['modes'].items()})
        write_json(build/'report.json',report)
        logs=[(build/f'failures.{m}.log').read_text() for m in modes if (build/f'failures.{m}.log').exists()]
        convert.atomic_text(build/'failures.log','\n\n'.join(logs))
        lines=['# EPUB 转换报告','',f"状态：{report['status']}",f'源目录：{directory}','',f"代码块：{len(baseline['code_hashes'])}；插图资源：{len(baseline['image_hashes'])}；公式：{baseline['math_occurrences']}",'']
        for mode,result in report['modes'].items():
            lines.append(f"- {mode}: {result['status']} {result.get('file',result.get('error',''))}")
        lines+=['','## 警告','']+[f'- {w}' for w in warnings]
        lines+=['','识别依据及源位置见 analysis.json；逐项验收结果见 validation.json。']
        convert.atomic_text(build/'report.md','\n'.join(lines)+'\n')
        print(f"\n✅ 报告：{build/'report.md'}；状态：{report['status']}",flush=True)
        return report


def main(argv=None):
    p=argparse.ArgumentParser(
        description=__doc__,
        epilog='示例：python md2epub.py /书籍/hybrid_auto --out-dir /输出目录',
    )
    p.add_argument(
        'hybrid_dir', type=Path,
        help='MinerU 的 hybrid_auto 输出目录',
    )
    p.add_argument(
        '--math', choices=['all', 'mathml', 'svg', 'png'], default='all',
        help='生成的公式版本；默认 all 依次生成三版',
    )
    p.add_argument(
        '--config', type=Path,
        help='覆盖自动识别结果的 JSON 配置',
    )
    p.add_argument(
        '--out-dir', type=Path,
        help='EPUB、缓存和报告的输出根目录；默认是 hybrid_dir 的上一级',
    )
    p.add_argument(
        '--analyze-only', action='store_true',
        help='只生成自动配置和分析报告，不构建 EPUB',
    )
    a=p.parse_args(argv)
    try:
        overrides=json.loads(a.config.read_text()) if a.config else {}
        result=run(a.hybrid_dir,a.out_dir,['mathml','svg','png'] if a.math=='all' else [a.math],overrides,a.analyze_only)
        return 1 if result['status']=='failed' else 0
    except KeyboardInterrupt:
        print('\n⏹️ 已中断；已有有效 EPUB 保留。');return 130
    except Exception as exc:
        print('❌ '+str(exc),file=sys.stderr);return 1

if __name__=='__main__':sys.exit(main())
