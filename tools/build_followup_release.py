#!/usr/bin/env python3
"""Build the data/config/static-page follow-up ZIP, without runtimes or photos."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'tools') not in sys.path:
    sys.path.insert(0, str(ROOT / 'tools'))
import build_budget_patch
import face_identity_index

SOURCE_SHA256 = '247c644252d2e1312c579bd6633a26788163e5d027d888f193931621d762963b'
XML_MEMBER = '1_게임데이터/FM26_unified_funded.xml'
STAMP = (2026, 10, 19, 0, 0, 0)
DOCS = ('budgets.md', 'face-repair.md', 'youth-face-compatibility.md',
        'internationals.md', 'club-restrictions.md', 'security-review.md')


def need(ok, message):
    if not ok:
        raise ValueError(message)


def sha256(body):
    return hashlib.sha256(body).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode('utf-8')


def safe_member(name):
    path = PurePosixPath(name)
    need(name and not name.startswith('/') and '\\' not in name
         and ':' not in name and all(x not in ('', '.', '..') for x in name.split('/')),
         f'Unsafe archive member: {name!r}')
    return path


def read_source(path):
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            need(archive.testzip() is None, 'Source ZIP CRC failure')
            need(archive.namelist().count('FM26_unified.xml') == 1, 'Missing unique source XML')
            body = archive.read('FM26_unified.xml')
    else:
        body = path.read_bytes()
    need(sha256(body) == SOURCE_SHA256, 'Input XML does not match reviewed source SHA-256')
    return body


def face_members(path):
    body = path.read_bytes()
    configs, reports = {}, {}
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        need(archive.testzip() is None, 'Face config ZIP CRC failure')
        need(len(archive.namelist()) == len(set(archive.namelist())), 'Duplicate face ZIP member')
        for item in archive.infolist():
            if item.is_dir():
                continue
            member = safe_member(item.filename)
            if member.name == 'config.xml':
                data = archive.read(item)
                root = ET.fromstring(data)
                need(root.tag == 'record' and root.find("list[@id='maps']") is not None,
                     f'Missing face map list: {member}')
                for record in root.findall("list[@id='maps']/record"):
                    need(record.get('from') and record.get('to'), f'Incomplete face map: {member}')
                configs['2_기존얼굴_수정/' + item.filename] = data
            elif member.suffix.lower() in {'.csv', '.json', '.md', '.txt'}:
                reports['4_검사결과/얼굴팩/' + item.filename] = archive.read(item)
    need(configs, 'Face ZIP contains no config.xml')
    return configs, reports, sha256(body)


def static_members(web_root):
    files = {'3_얼굴_도구/' + path.name: path.read_bytes()
             for path in sorted(web_root.iterdir())
             if path.is_file() and path.suffix in {'.html', '.js'}}
    for required in ('index.html', 'face.html', 'repair.html', 'repair-catalog.js',
                     'repair-core.js', 'layout-core.js', 'layout-app.js'):
        need('3_얼굴_도구/' + required in files, f'Missing static tool: {required}')
    for name, data in files.items():
        if name.endswith('.html'):
            for reference in re.findall(r'<script\b[^>]*\bsrc=[\"\']([^\"\']+)', data.decode('utf-8')):
                need('3_얼굴_도구/' + reference in files, f'Missing local script: {reference}')
    return files


def archive_bytes(files):
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w') as archive:
        for name in sorted(files):
            safe_member(name)
            item = zipfile.ZipInfo(name, STAMP)
            item.create_system = 3
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = 0o100644 << 16
            archive.writestr(item, files[name], compresslevel=9)
    body = result.getvalue()
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        need(archive.testzip() is None, 'Output ZIP CRC failure')
        need(archive.namelist() == sorted(files), 'Output member list differs')
        for name, data in files.items():
            need(archive.read(name) == data, f'Packaged bytes differ: {name}')
    return body


def build(source=None, face_configs=None, output=None, *, web_root=None, docs_root=None):
    """Write a repeatable ZIP from current inputs; return artifact hashes/report.

    The known generated output may be replaced. Source inputs are never modified.
    The output report includes its SHA-256; the inner manifest hashes every other
    member, avoiding an impossible self-referential archive hash.
    """
    source = Path(source or ROOT / 'downloads/fm26-unified-data.zip')
    face_configs = Path(face_configs or ROOT / 'downloads/fm26-face-current-save.zip')
    output = Path(output or ROOT / 'downloads/fm26-followup.zip')
    web_root = Path(web_root or ROOT / 'web')
    docs_root = Path(docs_root or ROOT / 'docs')
    need(output.resolve() not in {source.resolve(), face_configs.resolve()}, 'Cannot overwrite source')
    root = ET.fromstring(read_source(source))
    budgets = build_budget_patch.patch_tree(root)
    identities = face_identity_index.patch_player_aliases(root)
    need(budgets['initial_finance_clubs'] == 39
         and budgets['league_teams_covered_by_recurring_rule'] == 500, 'Unexpected funding scope')
    need(identities['changed_properties'] == {'Pnti': 723, 'Pdob': 51}, 'Unexpected identity patch scope')
    xml = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    ET.fromstring(xml)
    configs, face_reports, config_sha = face_members(face_configs)
    repair_reports = [json.loads(body) for name, body in face_reports.items()
                      if PurePosixPath(name).name == 'FACE_REPAIR_REPORT.json']
    need(len(repair_reports) == 1, 'The full face-pack report is required; a two-player sample is not a release input')
    full_face = repair_reports[0]
    need(full_face.get('scope') == 'current_save_calibrated_model'
         and full_face.get('source_xml_sha256') == SOURCE_SHA256
         and full_face.get('covered_photos') == 963
         and full_face.get('connections') == 959
         and len(configs) == 33, 'Full face coverage differs from the release guide')
    need({row['name']: int(row['actual_id']) for row in full_face.get('anchors', [])}
         == {'카리나': 2002042553, '배성연': 2002042923,
             '제시카': 2002043293, '이현주': 2002043294}, 'Unexpected face ID anchors')
    face_mode = 'save_calibrated_config_pack'
    static = static_members(web_root)
    catalog_text = static['3_얼굴_도구/repair-catalog.js'].decode('utf-8')
    catalog = json.loads(catalog_text[catalog_text.index('['):catalog_text.rindex(']') + 1])
    need(len(catalog) == 963 and len({row['path'] for row in catalog}) == 963,
         'Expected the full 963-photo reviewed caption catalog')
    files = {XML_MEMBER: xml, **configs, **face_reports, **static}
    guide = (docs_root / 'followup.md').read_text(encoding='utf-8')
    files['먼저읽기.txt'] = guide.replace('\n', '\r\n').encode('utf-8-sig')
    for name in DOCS:
        files['4_설명서/' + name] = (docs_root / name).read_bytes()
    files['4_검사결과/budgets.json'] = json_bytes(budgets)
    files['4_검사결과/identity-aliases.json'] = json_bytes(identities)
    report = {
        'status': 'STATICALLY_CHECKED_FM26_EDITOR_AND_GAME_UNAVAILABLE',
        'source_xml_sha256': SOURCE_SHA256,
        'output_xml_sha256': sha256(xml),
        'editor_xml_member': XML_MEMBER,
        'editor_xml_count': 1,
        'face_config_input': face_configs.name,
        'face_config_input_sha256': config_sha,
        'face_config_count': len(configs),
        'face_mapping_count': sum(len(ET.fromstring(data).findall("list[@id='maps']/record"))
                                  for data in configs.values()),
        'reviewed_caption_photos': len(catalog),
        'face_mode': face_mode,
        'face_pack_reports': repair_reports,
        'face_live_id_anchors': {'카리나': '2002042553', '배성연': '2002042923',
                                 '제시카': '2002043293', '이현주': '2002043294'},
        'face_other_ids_live_verified': False,
        'initial_finance_clubs': 39,
        'recurring_payment_teams': 500,
        'alias_records_changed': identities['changed_properties'],
        'requires_new_save_for_editor_data': True,
        'face_config_can_apply_to_existing_save': True,
        'excluded': ['source photos', 'automation', 'Python', 'CMD', 'EXE', 'DLL', 'runtime', 'launcher'],
        'verification': ['source SHA-256 pin', 'XML parse', 'patch scope assertions',
                         'local static script references', 'ZIP CRC', 'member list', 'member byte equality'],
        'limitations': ['No FM26 or FM26 Editor execution; static and browser checks are not game validation.',
                        'Only four supplied IDs are live anchors; remaining face IDs require game verification.',
                        'Raw finance units and wage display period are not independently verified.'],
    }
    files['4_검사결과/release-report.json'] = json_bytes(report)
    need([n for n in files if n.startswith('1_게임데이터/')] == [XML_MEMBER], 'More than one editor XML')
    for name in files:
        need(PurePosixPath(name).suffix.lower() in {'.xml', '.html', '.js', '.txt', '.md', '.csv', '.json'},
             f'Unapproved output type: {name}')
        need('automation' not in PurePosixPath(name).parts, f'Automation output is forbidden: {name}')
    manifest = {'format': 'SHA-256', 'excludes_self': True,
                'files': {name: {'bytes': len(body), 'sha256': sha256(body)}
                          for name, body in sorted(files.items())}}
    files['4_검사결과/manifest.json'] = json_bytes(manifest)
    packed = archive_bytes(files)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=output.name + '.', suffix='.tmp', delete=False) as file:
            temporary = Path(file.name)
            file.write(packed)
        temporary.replace(output)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return {**report, 'output': str(output), 'zip_bytes': len(packed),
            'zip_sha256': sha256(packed), 'zip_members': len(files), 'zip_crc_checked': True,
            'manifest_members_checked': len(manifest['files'])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--face-configs', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.face_configs, args.output), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
