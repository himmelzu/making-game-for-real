#!/usr/bin/env python3
"""Package original helper code and an unchanged official embeddable Python.

No code from reference editors, game DLLs, private player data or photographs
are redistributed. Python's license is preserved beside its executable.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VENDOR_URL = 'https://www.python.org/ftp/python/3.14.8/python-3.14.8-embed-amd64.zip'
VENDOR_SHA = 'a93abe456ab01bd96d7a085b3cdb6566b3063f4241360d114142fbdb07f0a310'


def build(vendor_path):
    vendor = Path(vendor_path).read_bytes()
    if hashlib.sha256(vendor).hexdigest() != VENDOR_SHA:
        raise ValueError('The runtime is not the pinned official Python 3.14.8 archive')
    files = {}
    source_files = list((ROOT / 'automation/fm26auto').glob('*.py'))
    source_files += [ROOT / 'automation/main.py']
    source_files += [ROOT / 'automation/ui' / n for n in ['index.html', 'app.css', 'app.js']]
    source_files += [ROOT / 'data' / n for n in ['identities.json', 'identities.csv', 'celebrity-cohort.csv']]
    for path in sorted(source_files):
        files[path.relative_to(ROOT).as_posix()] = path.read_bytes()
    for name in ['docs/automation.md', 'docs/automation-research.md', 'docs/automation-verification.json']:
        files[name] = (ROOT / name).read_bytes()
    with zipfile.ZipFile(io.BytesIO(vendor)) as archive:
        if archive.testzip() is not None:
            raise ValueError('Official runtime ZIP CRC failed')
        for name in archive.namelist():
            if '/' in name or '\\' in name or name in {'.', '..'}:
                raise ValueError('Unexpected runtime archive path')
            files['runtime/' + name] = archive.read(name)
    files['runtime/python314._pth'] = b'python314.zip\r\n.\r\n../automation\r\n'
    files['Start.cmd'] = (ROOT / 'automation/launcher.cmd').read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
    files['START_HERE.txt'] = ('FM26 연예인 자동화 실험판 v3\n\n'
        '1. ZIP 전체를 풀고, 먼저 docs/automation.md를 읽어 주세요.\n'
        '2. FM26에서 별도 사본 세이브를 불러오고 일시정지합니다.\n'
        '3. Start.cmd를 실행하면 브라우저 화면이 열립니다. Python을 따로 설치하지 않아도 됩니다.\n'
        '4. 기존 celeb 사진 폴더와 FM26 graphics 폴더, 커리어 이름을 지정하고 게임을 검사합니다.\n'
        '5. 첫 등록은 읽기 기록만 합니다. 현재 유소년을 바꾸려면 미리보기의 기존 유소년 포함 옵션을 사용하세요.\n'
        '6. 다음 유소년 입단 때에는 다시 검사→대상 확인→일괄 적용합니다. CSV나 선수별 에디터 입력은 필요 없습니다.\n\n'
        '실제 FM26에서 이름 변경 후 저장·재로드를 검증하지 못한 실험판입니다.\n'
        '이름은 이미 DB에 있는 연예인 이름 참조를 재사용합니다. 이름 문자열의 수명과 영속화는 미검증입니다.\n'
        '지원 버전/구조를 확인하지 못하면 쓰기를 막습니다. 현재 허용 후보 game_plugin.dll 파일 버전: 26.3.0.0 / 26.3.2.0.\n'
        '능력치 고정 플래그·28세 이상→22세 나이 조정도 실험적 기능입니다. 이미 확정된 은퇴의 취소나 전체 은퇴 방지는 보장하지 않습니다.\n'
        '국적·컵 규칙·리그는 이 도구가 바꾸지 않습니다. 기존 FMF에 추가해 사용하는 프로그램입니다.\n'
        '자동화 기록은 LOCALAPPDATA\\FM26CelebrityHelper에 보관하며 외부로 전송하지 않습니다.\n'
        '새 세이브나 과거 세이브 분기는 새 커리어 이름을 써 주세요. 동일 DB의 다른 세이브를 완전히 자동 식별하지 못합니다.\n').encode('utf-8-sig')
    files['VENDOR.json'] = (json.dumps({'name': 'CPython Windows x64 embeddable', 'version': '3.14.8', 'official_url': VENDOR_URL, 'archive_sha256': VENDOR_SHA, 'license': 'runtime/LICENSE.txt', 'modified_files': ['runtime/python314._pth']}, indent=2) + '\n').encode()
    manifest = {name: {'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()} for name, body in sorted(files.items())}
    files['MANIFEST.json'] = (json.dumps({'version': '3-beta', 'status': 'LIVE_FM26_UNVERIFIED', 'files': manifest}, ensure_ascii=False, indent=2) + '\n').encode()
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        for name, body in sorted(files.items()):
            info = zipfile.ZipInfo(name, (2026, 10, 18, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, body, compresslevel=9)
    output = data.getvalue()
    with zipfile.ZipFile(io.BytesIO(output)) as archive:
        if archive.testzip() is not None:
            raise ValueError('Delivery ZIP CRC failed')
        for name, entry in manifest.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != entry['sha256']:
                raise ValueError('Manifest failed: ' + name)
    return output, len(files)


def main():
    raise SystemExit(
        'Distribution withdrawn after Windows Security detection '
        'Trojan:Script/Sabsik.Fl.A!ml. See docs/security-review.md. '
        'Release generation is disabled pending investigation.'
    )


if __name__ == '__main__':
    main()
