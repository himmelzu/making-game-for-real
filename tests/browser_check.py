"""Optional real-browser integration check, using locally supplied original PNGs.

Requires Playwright and Chromium; never downloads dependencies or edits game files.
Usage: python tests/browser_check.py /path/to/celeb
"""
import contextlib
import functools
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import xml.etree.ElementTree as ET
import zipfile
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def main():
    photos = Path(sys.argv[1]).resolve()
    catalog = json.loads((ROOT / 'data/identities.json').read_text())
    handler = functools.partial(Quiet, directory=str(ROOT / 'web'))
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p, tempfile.TemporaryDirectory() as tmp:
            browser = p.chromium.launch(executable_path=shutil.which('chromium'), headless=True, args=['--no-sandbox'])
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            url = f'http://127.0.0.1:{server.server_port}/index.html'
            page.goto(url)
            page.locator('#saveKey').fill('browser-test')
            page.locator('#roster').fill('id,name,club,age,gender,newgen,ca,pa,dob\n9000001,테스트A,SM,16,F,1,70,150,2009-01-01\n9000002,테스트B,YG,17,F,1,80,160,2008-02-03\n9000009,남자,SM,16,M,1,70,150,2009-01-01')
            page.locator('#photos').set_input_files(str(photos))
            assert '963장 선택' in page.locator('#photoStatus').inner_text()
            page.locator('#assign').click()
            assert '새 배정 2명' in page.locator('#actionStatus').inner_text()
            assert page.locator('#results tr').count() == 2
            with page.expect_download() as event:
                page.locator('#faceZip').click()
            download = event.value
            archive = Path(tmp) / download.suggested_filename
            download.save_as(archive)
            with zipfile.ZipFile(archive) as z:
                assert z.testzip() is None
                config = ET.fromstring(z.read('config.xml'))
                assert [x.get('to') for x in config.find("list[@id='maps']")] == ['graphics/pictures/person/r-9000001/portrait', 'graphics/pictures/person/r-9000002/portrait']
                assert [x.get('from') for x in config.find("list[@id='maps']")] == ['fm26_celeb_9000001', 'fm26_celeb_9000002']
                for i, identity in enumerate(catalog[:2], 1):
                    image = z.read(f'fm26_celeb_{9000000+i}.png')
                    assert hashlib.sha256(image).hexdigest() == identity['sha256']
                assert all('/' not in filename for filename in z.namelist())
            with page.expect_download() as event:
                page.locator('#saveState').click()
            state_file = Path(tmp) / 'state.json'
            event.value.save_as(state_file)
            state = json.loads(state_file.read_text())
            assert len(state['assignments']) == 2 and len(state['baseline']) == 2
            page.reload()
            page.locator('#saveKey').fill('browser-test')
            page.locator('#stateFile').set_input_files(str(state_file))
            page.locator('#stateStatus').wait_for(state='visible')
            assert '배정 2명' in page.locator('#stateStatus').inner_text()
            page.locator('#gameDate').fill('2026-07-01')
            page.locator('#roster').fill('id,name,club,age,gender,newgen,ca,pa,dob\n9000001,테스트A,다른팀,29,F,1,60,150,1997-01-01\n9000003,테스트C,새팀,16,F,1,75,160,2010-02-03')
            with page.expect_download() as event:
                page.locator('#maintenance').click()
            maintenance = Path(tmp) / 'maintenance.csv'
            event.value.save_as(maintenance)
            assert '2004-07-01' in maintenance.read_text(encoding='utf-8-sig')
            page.locator('#assign').click()
            assert '새 배정 1명' in page.locator('#actionStatus').inner_text()
            assert page.locator('#results tr').count() == 3
            # Exercise inert HTML table import and Korean aliases.
            html = '<table><tr><th>고유 ID</th><th>이름</th><th>나이</th><th>구단</th></tr><tr><td>9000010</td><td>홍길동</td><td>16</td><td>팀</td></tr></table>'
            page.locator('#rosterFile').set_input_files({'name': 'roster.html', 'mimeType': 'text/html', 'buffer': html.encode()})
            expect(page.locator('#rosterStatus')).to_have_text('1명 / 여성으로 표시 0명')
            # The simple helper now supports both explicit target kinds and
            # gives every disjoint actual ID a unique, flat asset basename.
            page.goto(f'http://127.0.0.1:{server.server_port}/face.html')
            page.locator('#photoFolder').set_input_files(str(photos))
            page.locator('#ids').fill('2002079023')
            page.locator('#makePreview').click()
            for mode, prefix in [('generated', 'r-'), ('database', '')]:
                page.locator('#targetType').select_option(mode)
                with page.expect_download() as event:
                    page.locator('#downloadZip').click()
                simple_archive = Path(tmp) / f'{mode}.zip'
                event.value.save_as(simple_archive)
                with zipfile.ZipFile(simple_archive) as zipped:
                    assert zipped.testzip() is None
                    assert set(zipped.namelist()) == {'config.xml', 'fm26_celeb_2002079023.png', 'READ_ME.txt'}
                    config = ET.fromstring(zipped.read('config.xml'))
                    mapping = list(config.find("list[@id='maps']"))[0]
                    assert mapping.get('from') == 'fm26_celeb_2002079023'
                    assert mapping.get('to') == f'graphics/pictures/person/{prefix}2002079023/portrait'
                    assert zipped.read('fm26_celeb_2002079023.png').startswith(b'\x89PNG\r\n\x1a\n')
                    extracted = Path(tmp) / f'{mode}_pack'
                    zipped.extractall(extracted)
                page.locator('#layoutFolder').set_input_files(str(extracted))
                expect(page.locator('#layoutStatus')).to_contain_text('경로 일치 1개')
                expect(page.locator('#layoutStatus')).to_contain_text('호환성 주의: 0개')
            assert not errors, errors
            print(json.dumps({'browser': 'Chromium', 'real_photos': 963, 'zip_crc': 'pass', 'photo_hash': 'pass', 'state_roundtrip': 'pass', 'club_change': 'pass', 'next_season': 'pass', 'maintenance': 'pass', 'html_import': 'pass', 'simple_face_both_target_kinds': 'pass', 'unique_flat_png_layout': 'pass', 'layout_checker': 'pass', 'page_errors': errors}))
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
