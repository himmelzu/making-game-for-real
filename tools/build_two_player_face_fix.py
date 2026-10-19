#!/usr/bin/env python3
"""Package a config-only fix using two user-confirmed live IDs and captions."""
from __future__ import annotations

import argparse
import hashlib
import io
from pathlib import Path
import re
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SHA = 'd9eb2d892f5a4f59e5cc6b1524eef079137afadd7165032d5169337cde551ac1'
CONFIG_PATH = '2부리그/무소속,블리츠웨이,바로엔터,제이와이드/config.xml'
FIXES = {'2002043283': '2002043293', '2002043284': '2002043294'}


def mappings(data):
    root = ET.fromstring(data)
    return [(node.get('from'), node.get('to')) for node in root.findall("list[@id='maps']/record")]


def build(source):
    original = source.read_bytes()
    if hashlib.sha256(original).hexdigest() != SOURCE_SHA:
        raise ValueError('Different original config: use the browser repair tool instead')
    before = mappings(original)
    if len(before) != 50:
        raise ValueError('Unexpected original mapping count')
    text = original.decode('utf-8')
    for old, actual in FIXES.items():
        find = f'from="{old}" to="graphics/pictures/person/r-{old}/portrait"'
        replacement = f'from="{old}" to="graphics/pictures/person/r-{actual}/portrait"'
        if text.count(find) != 1:
            raise ValueError('Missing unique original source')
        text = text.replace(find, replacement)
        pattern = rf'[ \t]*<record from="{actual}" to="graphics/pictures/person/r-{actual}/portrait"\s*/>\r?\n'
        text, count = re.subn(pattern, '', text)
        if count != 1:
            raise ValueError('Missing original conflicting target')
    updated = text.encode('utf-8')
    after = mappings(updated)
    expected = [(old, 'graphics/pictures/person/r-' + FIXES.get(old, old) + '/portrait')
                for old, _ in before if old not in FIXES.values()]
    if after != expected or len({target for _, target in after}) != 48:
        raise ValueError('Unexpected mapping change')
    guide = (ROOT / 'docs/face-two-examples.md').read_text(encoding='utf-8')
    files = {CONFIG_PATH: updated, '적용방법.txt': guide.replace('\n', '\r\n').encode('utf-8-sig')}
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for name, body in files.items():
            item = zipfile.ZipInfo(name, (2026, 10, 18, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = 0o100644 << 16
            archive.writestr(item, body, compresslevel=9)
    result = output.getvalue()
    with zipfile.ZipFile(io.BytesIO(result)) as archive:
        if archive.testzip() is not None or archive.namelist() != list(files):
            raise ValueError('Delivery ZIP verification failed')
        if archive.read(CONFIG_PATH) != updated:
            raise ValueError('Packaged config differs')
    return result, updated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('original_config', type=Path)
    args = parser.parse_args()
    body, config = build(args.original_config)
    target = ROOT / 'downloads/fm26-face-two-player-fix.zip'
    target.write_bytes(body)
    print('ZIP', len(body), hashlib.sha256(body).hexdigest())
    print('config', len(config), hashlib.sha256(config).hexdigest())


if __name__ == '__main__':
    main()
