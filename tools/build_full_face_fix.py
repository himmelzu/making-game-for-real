#!/usr/bin/env python3
"""Build a config-only face mapping package for the calibrated current save."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile

from face_identity_index import build_photo_identity_index, _histories, value

ROOT = Path(__file__).resolve().parents[1]
GRAPHICS = Path('/workspace/shared/downloads/fm26-graphics/extracted/graphics/faces/celeb')
DEFAULT_OUTPUT = ROOT / 'downloads/fm26-face-current-save.zip'
SOURCE_SHA256 = '247c644252d2e1312c579bd6633a26788163e5d027d888f193931621d762963b'
ACTUAL_ANCHORS = {'카리나': 2002042553, '배성연': 2002042923,
                  '제시카': 2002043293, '이현주': 2002043294}
CAPTION_ALIASES = {'윤아': '임윤아', '찬미': '임도화'}
TARGET = re.compile(r'graphics/pictures/person/(?P<prefix>r-)?(?P<id>\d+)/portrait')
TAG = re.compile(r'<record\b[^<>]*?/>', re.DOTALL)
ATTR = re.compile(r'''\b(?P<key>from|to)\s*=\s*(?P<quote>["'])(?P<value>.*?)(?P=quote)''', re.DOTALL)


def need(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def normal(text):
    return unicodedata.normalize('NFC', text).strip()


def mapping_spans(text):
    """Locate only map tags; edits retain all other XML text verbatim."""
    comments = [m.span() for m in re.finditer(r'<!--.*?-->', text, re.DOTALL)]
    rows = []
    for match in TAG.finditer(text):
        if any(start <= match.start() < end for start, end in comments):
            continue
        attrs = {m['key']: m for m in ATTR.finditer(match.group())}
        if set(attrs) != {'from', 'to'}:
            continue
        destination = TARGET.fullmatch(attrs['to']['value'])
        need(destination is not None, 'Unexpected portrait destination')
        rows.append({'from': attrs['from']['value'], 'to': attrs['to']['value'],
                     'old_target_id': int(destination['id']),
                     'prefix': destination['prefix'] or '',
                     'span': match.span(),
                     'to_span': (match.start() + attrs['to'].start('value'),
                                 match.start() + attrs['to'].end('value'))})
    parsed = ET.fromstring(text).findall("list[@id='maps']/record")
    need([(r['from'], r['to']) for r in rows] == [(n.get('from'), n.get('to')) for n in parsed],
         'Text map spans do not match parsed XML')
    return rows


def build(root=ROOT, graphics=GRAPHICS, output=DEFAULT_OUTPUT):
    root, graphics, output = Path(root), Path(graphics), Path(output)
    with zipfile.ZipFile(root / 'downloads/fm26-unified-data.zip') as archive:
        xml = archive.read('FM26_unified.xml')
    need(sha(xml) == SOURCE_SHA256, 'Unexpected source XML SHA-256')
    source = ET.fromstring(xml)
    identities = build_photo_identity_index(source)
    by_high = {r['source_object_high32']: r for r in identities}
    by_name, by_low, aliases = defaultdict(set), defaultdict(list), defaultdict(set)
    histories = _histories(source)[1]
    for high, row in by_high.items():
        by_low[row['latest_record_low32']].append(row)
        for _, record, _, code in histories[high]:
            if code in {'Pcna', 'Psna', 'Pfln'} and value(record, 'new_value'):
                aliases[high].add(normal(value(record, 'new_value')))
        for alias in aliases[high]:
            by_name[alias].add(high)
    anchors = []
    for name, actual in ACTUAL_ANCHORS.items():
        candidates = by_name[normal(name)]
        need(len(candidates) == 1, f'Ambiguous calibration anchor: {name}')
        row = by_high[next(iter(candidates))]
        need(row['last_and_max_version_agree'], f'Conflicting latest anchor history: {name}')
        anchors.append({'name': name, 'actual_id': actual,
                        'source_low32': row['latest_record_low32'],
                        'offset': actual - row['latest_record_low32'],
                        'source_uid': row['latest_record_uid'], 'in_game_verified': True})
    offsets = {a['offset'] for a in anchors}
    need(len(offsets) == 1 and offsets == {3}, 'Four actual anchors do not consistently calibrate +3')
    offset = next(iter(offsets))

    catalog_bytes = (root / 'data/face-catalog.json').read_bytes()
    catalog = json.loads(catalog_bytes)
    need(isinstance(catalog, list) and len(catalog) == 963, 'Expected the 963-photo caption catalog')
    need(len({r['path'] for r in catalog}) == 963, 'Duplicate catalog photo path')
    configs, original_maps = {}, {}
    for file in sorted(graphics.rglob('config.xml')):
        relative = file.relative_to(graphics).as_posix()
        raw = file.read_bytes()
        text = raw.decode('utf-8-sig')
        rows = mapping_spans(text)
        for row in rows:
            path = (Path(relative).parent / (row['from'] + '.png')).as_posix()
            need(path not in original_maps, f'Duplicate original photo map: {path}')
            row['path'], row['config'] = path, relative
            original_maps[path] = row
        configs[relative] = {'raw': raw, 'text': text, 'rows': rows,
                             'bom': raw.startswith(b'\xef\xbb\xbf')}
    need(len(configs) == 33, 'Expected 33 original config files')

    support = defaultdict(Counter)
    for photo in catalog:
        path = photo['path']
        photo_path = graphics / path
        need(photo_path.is_file() and sha(photo_path.read_bytes()) == photo['sha256'],
             f'Photo checksum mismatch: {path}')
        need(path in original_maps, f'Photo lacks original mapping: {path}')
        caption = normal(photo.get('caption') or photo['name'])
        candidate = by_name.get(caption, set())
        original = original_maps[path]
        if len(candidate) == 1 and original['old_target_id'] >= 1_000_000_000:
            person = by_high[next(iter(candidate))]
            support[str(Path(path).parent)][person['latest_record_low32'] - original['old_target_id']] += 1
    models = {}
    for folder, counts in sorted(support.items()):
        ranked = counts.most_common()
        need(ranked[0][1] >= 3 and (len(ranked) == 1 or ranked[0][1] > ranked[1][1]),
             f'Insufficient unique caption support for folder model: {folder}')
        models[folder] = {'folder': folder, 'legacy_delta': ranked[0][0],
                          'unique_exact_support': ranked[0][1],
                          'delta_support': {str(delta): count for delta, count in ranked}}

    mappings, unresolved, warnings = [], [], []
    anchor_ids = {a['actual_id'] for a in anchors}
    for photo in catalog:
        path = photo['path']
        caption = normal(photo.get('caption') or photo['name'])
        original = original_maps[path]
        folder = str(Path(path).parent)
        model = models.get(folder)
        candidates, method = [], None
        if model and original['old_target_id'] >= 1_000_000_000:
            candidates = by_low.get(original['old_target_id'] + model['legacy_delta'], [])
            if len(candidates) == 1:
                method = 'legacy_folder_position'
        if method is None:
            highs = by_name.get(caption, set())
            if len(highs) == 1:
                candidates, method = [by_high[next(iter(highs))]], 'caption_exact_or_historical'
            elif caption in CAPTION_ALIASES and len(by_name.get(CAPTION_ALIASES[caption], set())) == 1:
                high = next(iter(by_name[CAPTION_ALIASES[caption]]))
                candidates, method = [by_high[high]], 'explicit_caption_alias'
            else:
                highs = {high for alias, ids in by_name.items() if len(caption) >= 2 and alias.endswith(caption) for high in ids}
                if len(highs) == 1:
                    candidates, method = [by_high[next(iter(highs))]], 'unique_caption_suffix'
        if method is None:
            unresolved.append({'path': path, 'caption': caption, 'original_to': original['to'],
                               'reason': 'No unique positional or caption candidate'})
            continue
        person = candidates[0]
        target_id = person['latest_record_low32'] + offset
        row = {'path': path, 'caption': caption, 'photo_sha256': photo['sha256'],
               'source_name': person['name'], 'source_aliases': sorted(aliases[person['source_object_high32']]),
               'source_object_high32': person['source_object_high32'],
               'source_uid': person['latest_record_uid'], 'source_low32': person['latest_record_low32'],
               'source_creation_property': person['creation_property'],
               'source_creation_record_index': person['creation_record_index'],
               'source_name_property': person['name_property'],
               'source_name_record_index': person['name_record_index'],
               'source_gender_record_index': person['gender_record_index'],
               'source_latest_record_index': person['latest_record_index'],
               'uid_history': person['uid_history'],
               'target_id': target_id, 'original_to': original['to'],
               'folder_legacy_delta': model['legacy_delta'] if model else None,
               'method': method, 'status': 'mapped',
               'in_game_verified': target_id in anchor_ids}
        if caption != normal(person['name']):
            warning = {'path': path, 'caption': caption, 'source_name': person['name'],
                       'source_alias_match': caption in aliases[person['source_object_high32']],
                       'method': method,
                       'reason': 'Caption differs from selected canonical source name; identity is inferred'}
            warnings.append(warning)
        mappings.append(row)

    grouped = defaultdict(list)
    for row in mappings:
        grouped[row['target_id']].append(row)
    duplicates = []
    for target_id, rows in grouped.items():
        if len(rows) < 2:
            continue
        ranked = sorted(rows, key=lambda row: (not (Path(row['path']).stem.isdigit() and int(Path(row['path']).stem) >= 1_000_000_000), row['path']))
        preferred = ranked[0]
        for alternate in ranked[1:]:
            alternate['status'] = 'duplicate_alternate'
            alternate['in_game_verified'] = False
            duplicates.append({'path': alternate['path'], 'preferred_path': preferred['path'],
                               'target_id': target_id, 'reason': 'Prefer regular numeric photo mapping; PNG retained'})
    selected = {r['path']: r for r in mappings}
    corrected_ids = {r['target_id'] for r in mappings if r['status'] == 'mapped'}
    files, changes, removed_conflicts, untouched = {}, [], [], []
    for relative, config in configs.items():
        edits = []
        for original in config['rows']:
            row = selected.get(original['path'])
            if row and row['status'] == 'mapped':
                replacement = f"graphics/pictures/person/{original['prefix']}{row['target_id']}/portrait"
                if replacement != original['to']:
                    edits.append((*original['to_span'], replacement))
            elif row and row['status'] == 'duplicate_alternate':
                edits.append((*original['span'], ''))
            elif original['old_target_id'] in corrected_ids:
                edits.append((*original['span'], ''))
                removed_conflicts.append({'path': original['path'], 'original_to': original['to'],
                                          'reason': 'Original destination conflicts with a corrected photo'})
            else:
                untouched.append({'path': original['path'], 'original_to': original['to']})
        text = config['text']
        for start, end, replacement in sorted(edits, reverse=True):
            text = text[:start] + replacement + text[end:]
        updated = text.encode('utf-8')
        if config['bom']:
            updated = b'\xef\xbb\xbf' + updated
        mapping_spans(text)
        files[relative] = updated
        changes.append({'path': relative, 'before_sha256': sha(config['raw']),
                        'after_sha256': sha(updated), 'edits': len(edits),
                        'changed': updated != config['raw']})
    destinations = Counter()
    for relative in configs:
        for row in mapping_spans(files[relative].decode('utf-8-sig')):
            destinations[row['old_target_id']] += 1
    need(all(destinations[target] == 1 for target in corrected_ids), 'Corrected targets are not globally unique')
    need(len(mappings) + len(unresolved) == 963, 'Photo accounting mismatch')
    report = {'scope': 'current_save_calibrated_model', 'offset': offset,
              'source_xml_sha256': SOURCE_SHA256, 'caption_catalog_sha256': sha(catalog_bytes),
              'input_photos': len(catalog), 'photos_sha256_verified': len(catalog),
              'covered_photos': len(mappings), 'connections': len(corrected_ids),
              'in_game_verified_identities': len(anchors),
              'model_inferred_identities': len(corrected_ids - anchor_ids),
              'identity_limit': 'Four IDs were checked in game. Other connections follow a calibrated source-history and folder-position model; caption differences are review warnings.',
              'anchors': anchors, 'folder_models': list(models.values()),
              'unresolved': unresolved, 'duplicate_alternates': duplicates,
              'caption_difference_warnings': warnings, 'mappings': mappings,
              'configs': changes, 'removed_conflicting_original_rows': removed_conflicts,
              'untouched_original_rows': untouched,
              'original_map_rows': len(original_maps), 'output_map_rows': sum(destinations.values()),
              'all_output_target_ids_unique': len(destinations) == sum(destinations.values()),
              'contains_png': False, 'changes_save_data': False}
    files['FACE_REPAIR_REPORT.json'] = (json.dumps(report, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    files['FACE_SOURCE_EVIDENCE.json'] = (json.dumps(mappings, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    csv_buffer = io.StringIO(newline='')
    fields = ['path', 'caption', 'photo_sha256', 'source_name', 'source_object_high32', 'source_uid',
              'source_low32', 'target_id', 'method', 'status', 'in_game_verified',
              'source_creation_property', 'source_creation_record_index', 'source_name_property',
              'source_name_record_index', 'source_gender_record_index', 'source_latest_record_index']
    writer = csv.DictWriter(csv_buffer, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(mappings)
    files['FACE_SOURCE_EVIDENCE.csv'] = csv_buffer.getvalue().encode('utf-8-sig')
    guide_path = root / 'docs/full-face-fix.md'
    guide = guide_path.read_text(encoding='utf-8') if guide_path.exists() else (
        '현재 세이브용 얼굴 연결 수정본\n기존 celeb 폴더를 graphics 밖에 백업한 뒤 이 ZIP의 폴더를 celeb에 덮어쓰세요.\n'
        'FM26 환경설정에서 스킨 캐시를 비우고 스킨을 다시 불러오세요.\n'
        '확인된 4개 ID와 원본 XML 이력을 사용한 추정 모델입니다. 다른 세이브의 ID 일치를 보장하지 않습니다.\n'
        'PNG와 저장 데이터는 변경하지 않습니다. FACE_REPAIR_REPORT.json에서 경고와 적용 범위를 확인하세요.\n')
    files['적용방법.txt'] = guide.replace('\r\n', '\n').replace('\n', '\r\n').encode('utf-8-sig')
    output.parent.mkdir(parents=True, exist_ok=True)
    need(not output.resolve().is_relative_to(graphics.resolve()), 'Output cannot overwrite original graphics')
    need(output.resolve() not in {(root / 'downloads/fm26-unified-data.zip').resolve(),
                                  (root / 'data/face-catalog.json').resolve()},
         'Output cannot overwrite source data')
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, body in files.items():
            item = zipfile.ZipInfo(name, (2026, 10, 19, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = 0o100644 << 16
            archive.writestr(item, body)
    with zipfile.ZipFile(output) as archive:
        need(archive.testzip() is None and archive.namelist() == list(files), 'ZIP verification failed')
        need(all(archive.read(name) == body for name, body in files.items()), 'ZIP contents differ')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--graphics', type=Path, default=GRAPHICS)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = build(graphics=args.graphics, output=args.output)
    print(json.dumps({key: report[key] for key in ('scope', 'covered_photos', 'connections', 'offset', 'unresolved')}, ensure_ascii=False))
    print(args.output)


if __name__ == '__main__':
    main()
