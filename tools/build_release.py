#!/usr/bin/env python3
"""Build the delivery ZIP from the inspected inputs, without network or FMF compilation."""
from __future__ import annotations
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import tempfile
import zipfile
import datetime as dt
from build_longplay import build

ROOT = Path(__file__).resolve().parents[1]
FMF_SHA = '54225f7fea315c3a0b48ee64ab7794964bd5c398af0f4feeaf96d436e0120caf'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('user_saved_fmf', type=Path)
    args = parser.parse_args()
    fmf = args.user_saved_fmf.read_bytes()
    if hashlib.sha256(fmf).hexdigest() != FMF_SHA:
        raise ValueError('FMF is not the unchanged user-saved ai update.fmf')
    with zipfile.ZipFile(ROOT / 'downloads/fm26-review.zip') as old:
        cups = old.read('cups_test.xml')
    with (ROOT / 'data/celebrity-cohort.csv').open(encoding='utf-8-sig', newline='') as file:
        cohort = list(csv.DictReader(file))
    files = {'base/ai_update_user_saved.fmf': fmf, 'editor/cups.xml': cups}
    for spread, name in [(False, 'longplay_young'), (True, 'longplay_world_experimental')]:
        xml, report = build(cups, cohort, dt.date(2025, 7, 1), spread)
        files[f'editor/{name}.xml'] = xml
        files[f'reports/{name}_players.json'] = (json.dumps(report, ensure_ascii=False, indent=2) + '\n').encode()
    for folder in ['web', 'docs', 'data', 'templates']:
        for path in sorted((ROOT / folder).rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts:
                files[path.relative_to(ROOT).as_posix()] = path.read_bytes()
    files['START_HERE.txt'] = ('FM26 장기 커리어 도우미 v2\n\n'
        '1. ZIP 전체 압축을 풉니다.\n2. docs/start-here.md를 읽습니다(메모장으로도 열 수 있습니다).\n'
        '3. web/index.html을 Edge/Chrome으로 여세요. 기존 celeb 사진 폴더를 선택합니다.\n\n'
        'base의 FMF는 보내주신 ai update.fmf 그대로입니다. 새 기능을 컴파일한 FMF가 아닙니다.\n'
        '새 XML은 FM26 Editor에서 검증 후 별도의 FMF로 저장해야 합니다.\n'
        '도구는 얼굴팩 ZIP과 이름·나이·능력치 수정 목록을 만듭니다. 게임 자동 이름 변경·노화 중지·자동 연예인 생성은 없습니다.\n'
        '사진 재사용 시 필수 추가 사진은 0장입니다. 기본 이름·사진 목록은 자막을 확인한 118장입니다.\n'
        '사진과 EXE는 이 ZIP에 포함하지 않았습니다. 기존 얼굴팩을 그대로 사용하세요.\n').encode('utf-8-sig')
    manifest = {'version': 2, 'fmf_status': 'UNCHANGED_USER_SAVED_FILE', 'xml_status': 'NEW_VARIANTS_REQUIRE_EDITOR_VALIDATION',
                'runtime_automatic_names_aging_regens': False,
                'files': {name: {'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()} for name, body in sorted(files.items())}}
    files['MANIFEST.json'] = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, body in sorted(files.items()):
            item = zipfile.ZipInfo(name, date_time=(2026, 10, 17, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = 0o100644 << 16
            archive.writestr(item, body)
    data = output.getvalue()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if archive.testzip() is not None:
            raise ValueError('ZIP CRC failed')
        for name, metadata in manifest['files'].items():
            if hashlib.sha256(archive.read(name)).hexdigest() != metadata['sha256']:
                raise ValueError('Manifest verification failed: ' + name)
    target = ROOT / 'downloads/fm26-longplay-v2.zip'
    # Replace only this reproducible generated artifact; inputs stay unchanged.
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.fm26-build-', delete=False) as tmp:
        tmp.write(data)
        temporary = Path(tmp.name)
    temporary.replace(target)
    digest = hashlib.sha256(data).hexdigest()
    (target.parent / 'fm26-longplay-v2.sha256').write_text(digest + '  ' + target.name + '\n')
    print(json.dumps({'file': str(target), 'bytes': len(data), 'sha256': digest, 'files': len(files)}))


if __name__ == '__main__':
    main()
