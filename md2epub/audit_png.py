#!/usr/bin/env python3
"""PNG EPUB 通用排版审核；几何阈值来自当前配置。"""
from __future__ import annotations
import argparse
from io import BytesIO
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import zipfile
from PIL import Image


def audit_png(path: Path, cfg: dict | None = None):
    from convert import RASTER_SCALE, DISPLAY_RASTER_SCALE, INLINE_PX_BANDS, FALLBACK_BOX_PX
    cfg=cfg or {}
    px=cfg.get('png_px_per_ex',8.0); canvas=cfg.get('png_display_canvas_px',720)
    max_width=round(canvas/RASTER_SCALE)
    heights={round(min(b*px,FALLBACK_BOX_PX[1])) for _,b in INLINE_PX_BANDS}
    errors,warnings=[],[]
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if not name.endswith('.xhtml'): continue
            root=ET.fromstring(z.read(name))
            for el in root.iter():
                tag=el.tag.rsplit('}',1)[-1]
                if tag=='li' and not ''.join(el.itertext()).strip() and not el.findall('.//{*}img'):
                    errors.append(name+': empty list item')
                if tag=='p' and len(el)==1 and 'math-inline' in el[0].get('class','') and not ''.join(el.itertext()).strip():
                    warnings.append(name+': isolated inline formula paragraph')
                if tag!='img': continue
                classes=el.get('class','').split()
                if 'math-inline' in classes and int(el.get('height','0')) not in heights:
                    errors.append(name+': invalid inline height '+str(el.get('height')))
                if 'math-display-img' in classes:
                    if int(el.get('width','0'))>max_width:
                        errors.append(name+': display width exceeds configured canvas')
                    from posixpath import dirname,normpath,join
                    target=normpath(join(dirname(name),el.get('src','')))
                    with Image.open(BytesIO(z.read(target))) as im:
                        if im.width > canvas*DISPLAY_RASTER_SCALE/RASTER_SCALE:
                            warnings.append(name+': wide formula preserved with proportional scaling')
    return {'ok':not errors,'errors':errors,'warnings':warnings}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('epub',type=Path);p.add_argument('--config',type=Path)
    a=p.parse_args(argv)
    import json
    try: result=audit_png(a.epub,json.loads(a.config.read_text()) if a.config else {})
    except Exception as exc: print('❌',exc);return 1
    print(f"{'✅' if result['ok'] else '❌'} PNG audit: {len(result['errors'])} errors, {len(result['warnings'])} warnings")
    for e in result['errors']: print(e)
    return 0 if result['ok'] else 1

if __name__=='__main__':sys.exit(main())
