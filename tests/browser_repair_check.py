"""Exercise the static repair page using original facepack data.

Requires the already-installed Playwright/Chromium; no dependency downloads.
Usage: python tests/browser_repair_check.py /path/to/celeb
"""
import csv
import functools
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
FOLDER = '2부리그/무소속,블리츠웨이,바로엔터,제이와이드'


def mappings(text):
    root = ET.fromstring(text)
    return [(row.get('from'), row.get('to')) for row in root.find("list[@id='maps']")]


def main():
    photos = Path(sys.argv[1]).resolve()
    original_path = photos / FOLDER / 'config.xml'
    original_bytes = original_path.read_bytes()
    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Quiet, directory=str(ROOT / 'web')))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with sync_playwright() as pw, tempfile.TemporaryDirectory() as temporary:
        browser = pw.chromium.launch(executable_path=shutil.which('chromium'), headless=True, args=['--no-sandbox'])
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        errors, network = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: network.append(request.url) if request.url.startswith(('http:', 'https:')) and not request.url.startswith(f'http://127.0.0.1:{server.server_port}/') else None)
        page.goto(f'http://127.0.0.1:{server.server_port}/repair.html')
        expect(page.locator('#reportedCases')).to_be_disabled()
        page.locator('#repairFolder').set_input_files(str(photos))
        expect(page.locator('#folderStatus')).to_contain_text('PNG 1010장 / config.xml 33개')
        expect(page.locator('#reportedCases')).to_be_enabled(timeout=30000)
        page.locator('#reportedCases').click()
        assert '2002043293,제시카' in page.locator('#repairRoster').input_value()
        assert '2002043294,이현주' in page.locator('#repairRoster').input_value()
        page.locator('#repairPreview').click()
        expect(page.locator('#repairResults')).to_be_visible(timeout=30000)
        expect(page.locator('#repairCounts')).to_contain_text('확인된 연결 2명')
        expect(page.locator('#repairCounts')).to_contain_text('충돌 매핑 제외 2개')
        expect(page.locator('#repairDownload')).to_be_disabled()
        assert page.locator('#repairRows tr').count() == 2
        page.locator('#repairReviewed').check()
        with page.expect_download() as event:
            page.locator('#repairDownload').click()
        archive = Path(temporary) / event.value.suggested_filename
        event.value.save_as(archive)
        with zipfile.ZipFile(archive) as zipped:
            assert zipped.testzip() is None
            assert len(zipped.namelist()) == 5, zipped.namelist()
            assert all(not name.lower().endswith(('.png', '.exe', '.cmd', '.ps1', '.py')) for name in zipped.namelist())
            patched = zipped.read(FOLDER + '/config.xml')
            rows = mappings(patched)
            assert ('2002043283', 'graphics/pictures/person/r-2002043293/portrait') in rows
            assert ('2002043284', 'graphics/pictures/person/r-2002043294/portrait') in rows
            assert not any(source in {'2002043293', '2002043294'} for source, _ in rows)
            original_rows = mappings(original_bytes)
            assert len(rows) == len(original_rows) - 2
            unaffected = [(source, target) for source, target in original_rows if source not in {'2002043283', '2002043284', '2002043293', '2002043294'}]
            assert all(row in rows for row in unaffected)
            unmapped = list(csv.DictReader(io.StringIO(zipped.read('UNMAPPED_PHOTOS.csv').decode('utf-8-sig'))))
            assert {Path(row['photo']).name for row in unmapped} == {'2002043293.png', '2002043294.png'}
            # Only two to-values and two conflicting record spans change.
            expected = original_bytes.decode('utf8').replace('from="2002043283" to="graphics/pictures/person/r-2002043283/portrait"', 'from="2002043283" to="graphics/pictures/person/r-2002043293/portrait"')
            expected = expected.replace('from="2002043284" to="graphics/pictures/person/r-2002043284/portrait"', 'from="2002043284" to="graphics/pictures/person/r-2002043294/portrait"')
            for source in ['2002043293', '2002043294']:
                expected = expected.replace(f'<record from="{source}" to="graphics/pictures/person/r-{source}/portrait" />', '')
            assert patched.decode('utf8') == expected
        assert original_path.read_bytes() == original_bytes
        # Any roster edit invalidates the prior output and requires a new review.
        page.locator('#repairRoster').fill('id,name\n2002043293,제시카\n2002043293,이현주')
        expect(page.locator('#repairResults')).to_be_hidden()
        page.locator('#repairPreview').click()
        expect(page.locator('#repairStatus')).to_contain_text('중복')
        expect(page.locator('#repairDownload')).to_be_disabled()
        # HTML imports are inert and Korean table headers are accepted.
        html = '<img src="https://example.invalid/never-load"><script>window.__unsafeImport = true</script><table><tr><th>고유 ID</th><th>이름</th></tr><tr><td>2002043294</td><td>이현주</td></tr></table>'
        page.locator('#repairRosterFile').set_input_files({'name': 'roster.html', 'mimeType': 'text/html', 'buffer': html.encode()})
        expect(page.locator('#repairStatus')).to_contain_text('1명의 ID·이름')
        assert page.evaluate('window.__unsafeImport') is None
        assert not errors, errors
        assert not network, network
        print(json.dumps({'page': 'Chromium, read-only loopback fixture server', 'file_url_test': 'blocked by managed Chromium administrator policy; Windows local opening not exercised', 'original_configs': 33, 'original_photos': 1010, 'verified_user_ids': 2, 'conflicting_records_removed': 2, 'other_images_reported_unmapped': 2, 'zip_crc': 'pass', 'unrelated_original_xml_bytes': 'preserved', 'original_files': 'unchanged', 'html_import': 'inert', 'external_network_requests': network, 'page_errors': errors}))
        browser.close()
    server.shutdown()
    server.server_close()


if __name__ == '__main__':
    main()
