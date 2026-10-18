"""Actual UI + actual local server + simulated process. Not a live-game test."""
from pathlib import Path
import shutil
import sys
import threading
from playwright.sync_api import sync_playwright, expect

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_automation_backend import BackendTests, MAIN


def main():
    fixture = BackendTests()
    fixture.setUp()
    server = MAIN.create_server(fixture.app, Path(__file__).resolve().parents[1] / 'automation/ui', 'browser-test-key')
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=shutil.which('chromium'), headless=True, args=['--no-sandbox'])
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{server.server_port}/#browser-test-key')
            expect(page.locator('#scan')).to_be_enabled()
            page.locator('#scan').click()
            expect(page.locator('#players-count')).to_have_text('12')
            expect(page.locator('#baseline')).to_be_enabled()
            page.locator('#baseline').click()
            expect(page.locator('#state-summary')).to_contain_text('보호 선수 6명')
            assert fixture.game.writes == []
            youth = fixture.game.add(9000001, 'Generated Girl')
            page.locator('#game-date').fill('2035-07-01')
            page.locator('#save-settings').click()
            expect(page.locator('#scan')).to_be_enabled()
            page.locator('#scan').click()
            expect(page.locator('#players-count')).to_have_text('13')
            expect(page.locator('#preview')).to_be_enabled()
            page.locator('#preview').click()
            expect(page.locator('#candidate-list .candidate')).to_have_count(1)
            page.locator('#select-all').click()
            page.locator('#paused').check()
            page.locator('#experimental-ack').check()
            expect(page.locator('#apply')).to_be_enabled()
            page.locator('#apply').click()
            expect(page.locator('#last-result')).to_contain_text('신규 1명')
            assert fixture.game.current(youth)['name'] != 'Generated Girl'
            assert fixture.game.current(youth)['full_name'] != 'Generated Girl'
            assert (fixture.graphics / 'fm26_celebrity_auto/config.xml').exists()
            expect(page.locator('#preview')).to_be_disabled()
            page.locator('#paused').check()
            page.locator('#experimental-ack').check()
            expect(page.locator('#undo')).to_be_enabled()
            page.locator('#undo').click()
            expect(page.locator('#last-result')).to_contain_text('되돌렸습니다')
            assert fixture.game.current(youth)['name'] == 'Generated Girl'
            assert fixture.app.state()['assignments'] == {}
            assert not errors, errors
            browser.close()
            print('PASS: real UI/local API/first baseline/new youth/4 names/portrait pack/maintenance/stale scan/undo; simulated game memory only')
    finally:
        server.shutdown()
        server.server_close()
        fixture.doCleanups()


if __name__ == '__main__':
    main()
