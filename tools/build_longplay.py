#!/usr/bin/env python3
"""Deterministic, fail-closed patch of the inspected FM26 Editor XML.

Writes review XML only. Does not compile FMF or validate game rules.
Python 3.10+, standard library; source/outputs are never overwritten.
"""
from __future__ import annotations
import argparse
import collections
import csv
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

SOURCE_SHA256 = '837d7ab4aa077a909fee84ec33c341395f22fcbb6a14df2d293c5e247cab9fd6'
COUNTRIES = [
    (135, '대한민국'), (116, '일본'), (110, '중국'), (1435, '호주'),
    (765, '잉글랜드'), (769, '프랑스'), (771, '독일'), (796, '스페인'),
    (784, '네덜란드'), (797, '스웨덴'), (786, '노르웨이'), (764, '덴마크'),
    (776, '이탈리아'), (1651, '브라질'), (364, '캐나다'), (390, '미국'),
]
NATION_SOURCE = 'https://github.com/mavarobli/FMSuperScout/blob/main/app/nations.js'


def need(ok, message):
    if not ok:
        raise ValueError(message)


def value(node, key):
    child = node.find(f"*[@id='{key}']")
    need(child is not None, f'Missing field {key}')
    return child.get('value')


def prop(name):
    return str(int.from_bytes(name.encode('ascii'), 'big'))


def build(source: bytes, cohort: list[dict], asof: dt.date, spread: bool):
    need(hashlib.sha256(source).hexdigest() == SOURCE_SHA256, 'Input differs from inspected three-cup XML')
    need(len(cohort) == 771 and len({p['person_uid'] for p in cohort}) == 771, 'Unexpected cohort')
    before = ET.fromstring(source)
    db = before.find("list[@id='db_changes']")
    need(db is not None and len(db) == 54726, 'Unexpected DB count')
    # The source preserves the original editor indentation, permitting byte-local edits.
    text = source.decode('utf-8')
    matches = list(re.finditer(r'(?ms)^\t\t<record>\r?\n.*?^\t\t</record>', text[text.index('<list id="db_changes">'):]))
    db_start = text.index('<list id="db_changes">')
    need(len(matches) == len(db), 'Unexpected DB record formatting')
    latest = {}
    for i, record in enumerate(db):
        if value(record, 'database_table_type') == '1':
            latest[value(record, 'db_unique_id'), value(record, 'property')] = i
    replacements, report = [], []
    changed_records = {}
    for person in cohort:
        uid = person['person_uid']
        birthday = dt.date.fromisoformat(person['DOB_in_mod'])
        target_age = 18 + (int(uid) >> 32) % 5
        day = min(birthday.day, 28) if birthday.month == 2 else birthday.day
        year = asof.year - target_age - ((birthday.month, day) > (asof.month, asof.day))
        dob = dt.date(year, birthday.month, day)
        slot = int(person['proposed_country_slot'].rsplit('_', 1)[1]) - 1
        country_id, country = COUNTRIES[slot]
        for field in ['Pdob'] + (['Pnti'] if spread else []):
            index = latest.get((uid, prop(field)))
            need(index is not None, f'Missing {field} for {uid}')
            record, match = db[index], matches[index]
            old = record.find("*[@id='new_value']")
            need(old is not None, 'Missing new_value')
            if field == 'Pdob':
                need(old.tag == 'date', 'Unexpected Pdob type')
                new = ET.Element('date', {**old.attrib, 'year': str(dob.year), 'month': str(dob.month), 'day': str(dob.day)})
            else:
                need(old.tag == 'record' and value(old, 'DBID') == '774', 'Unexpected original nation')
                new = ET.Element('record', {'id': 'new_value'})
                ET.SubElement(new, 'large', {'id': 'Nnat', 'value': str(country_id * (2**32 + 1))})
                ET.SubElement(new, 'integer', {'id': 'DBID', 'value': str(country_id)})
            block = match.group()
            find = re.search(r'<date id="new_value"[^>]*?/>|<record id="new_value">.*?</record>', block, re.S)
            need(find is not None, 'Cannot locate exact new_value')
            new_text = ET.tostring(new, encoding='unicode').replace(' />', '/>')
            start = db_start + match.start() + find.start()
            end = db_start + match.start() + find.end()
            replacements.append((start, end, new_text))
            changed_records[index] = new
        report.append({'person_uid': uid, 'id_candidate_only': person['candidate_low32_id'], 'name': person['name'],
                       'old_dob': birthday.isoformat(), 'new_dob': dob.isoformat(), 'age_asof': target_age,
                       'role': person['role_bucket_from_raw_positions'], 'ca': person['CA_raw_PCAB'],
                       'nation_dbid': country_id if spread else 774, 'nation': country if spread else '원본 연예인 국가'})
    chunks, cursor = [], 0
    for start, end, replacement in sorted(replacements):
        need(start >= cursor, 'Overlapping replacements')
        chunks.extend((text[cursor:start], replacement))
        cursor = end
    chunks.append(text[cursor:])
    result = ''.join(chunks).encode('utf-8')
    after = ET.fromstring(result)
    updated = after.find("list[@id='db_changes']")
    need(len(updated) == len(db), 'DB record count changed')
    # Independently compare all changed records and every untouched record structurally.
    for i, (old_record, new_record) in enumerate(zip(db, updated)):
        if i in changed_records:
            old_record.remove(old_record.find("*[@id='new_value']"))
            new_record.remove(new_record.find("*[@id='new_value']"))
        need(ET.tostring(old_record) == ET.tostring(new_record), f'Unexpected DB change at {i}')
    # These two known properties are the sole edited fields; rules and metadata stay byte-identical.
    need(len(replacements) == 771 * (2 if spread else 1), 'Unexpected patch count')
    return result, report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path)
    p.add_argument('output_dir', type=Path)
    p.add_argument('--cohort', type=Path, default=Path(__file__).resolve().parents[1] / 'data/celebrity-cohort.csv')
    p.add_argument('--asof', type=dt.date.fromisoformat, default=dt.date(2025, 7, 1))
    args = p.parse_args()
    source = args.source.read_bytes()
    cohort = list(csv.DictReader(args.cohort.open(encoding='utf-8-sig', newline='')))
    results = {}
    for spread, name in [(False, 'longplay_young'), (True, 'longplay_world_experimental')]:
        xml, report = build(source, cohort, args.asof, spread)
        results[name + '.xml'] = xml
        results[name + '_changes.json'] = (json.dumps({'status': 'EDITOR_AND_GAME_UNVERIFIED', 'asof': args.asof.isoformat(),
            'source_sha256': SOURCE_SHA256, 'output_sha256': hashlib.sha256(xml).hexdigest(),
            'notes': ['Initial birthdate preset only; no aging/retirement freeze.', 'Primary nationality only; international rules, caps, club restrictions unchanged.'],
            'nation_reference': NATION_SOURCE, 'counts': dict(collections.Counter(p['nation'] for p in report)), 'players': report}, ensure_ascii=False, indent=2) + '\n').encode()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, content in results.items():
        target = args.output_dir / name
        need(not target.exists() or target.read_bytes() == content, f'Refusing to overwrite {target}')
    for name, content in results.items():
        (args.output_dir / name).write_bytes(content)
        print(name, len(content), hashlib.sha256(content).hexdigest())


if __name__ == '__main__':
    main()
