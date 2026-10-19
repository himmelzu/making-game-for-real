# 개발 환경과 재생성

현재 후속판은 `python tools/build_full_face_fix.py`로 현재 세이브용 config를 만든 뒤 `python tools/build_followup_release.py --face-configs downloads/fm26-face-current-save.zip`으로 묶습니다. 얼굴 보정 빌드는 검토한 원본 사진 폴더가 필요하며 PNG를 출력하지 않습니다. `tools/face_identity_index.py`는 인물의 과거 UID 변경 기록을 보존한 채 국적·생년월일 값만 일치시키며, 재정 변경과 함께 새 XML 하나에 적용합니다.

정적 HTML/JavaScript와 Python 표준 라이브러리만 사용합니다. 일반 도구 사용에는 Windows의 Edge/Chrome이면 됩니다. 상시 서버, npm 설치, 비밀키는 필요 없습니다.

v3 자동화 앱의 Windows 프로세스 어댑터는 `automation/fm26auto`에 있습니다. UI는 `automation/ui`이고, 실행 시에만 이 PC의 loopback 서버를 열어 브라우저와 통신합니다. 클라우드에서 실제 FM26 실행은 지원하지 않으며 모의 메모리와 로컬 API로 검사합니다. 실제 게임 저장·재로드 검증과 구분해야 합니다.

개발 검사는 Python 3.10 이상과 Node 18 이상에서 실행합니다.

```bash
bash .codex/setup.sh
```

v3 통합 UI 검사(Playwright/Chromium 설치된 개발 환경, 게임은 모의 메모리):

```bash
python tests/browser_automation_check.py
```

v3 실행 패키지는 Windows 보안의 `Trojan:Script/Sabsik.Fl.A!ml` 탐지 보고로 회수했습니다. `tools/build_automation_release.py`의 배포 생성 명령도 중단했습니다. 아래 모의 테스트나 공식 Python과의 해시 일치는 백신 통과를 증명하지 않습니다. [보안 조사 범위](security-review.md)

실행 파일이 없는 단일 XML 통합 자료를 재생성하려면:

```bash
python tools/build_unified_editor.py
```

이 명령은 보존된 v2의 누적 국적 변형을 읽어 XML 하나와 TXT 안내만 담은 ZIP을 만듭니다. 원본 v2를 바꾸지 않고, 실제 게임 검증을 수행하거나 자동화 실행 패키지를 만들지 않습니다.

원본 사진까지 확인하는 추가 검사:

```bash
FM26_FACE_TEST_SOURCE=/path/to/celeb node --test tests/*.cjs
python tests/browser_check.py /path/to/celeb
```

브라우저 검사는 Playwright와 Chromium이 이미 설치된 개발 환경에서 실행합니다. 검사용 임시 HTTP 서버는 종료할 때 함께 정리됩니다. 배포 도구는 서버를 요구하지 않습니다. Windows 파일 더블클릭 사용은 이 Linux 환경에서 직접 확인하지 못했습니다.

`tools/build_longplay.py`는 처음 배포한 컵 XML의 정확한 SHA-256과 771명 명단을 대상으로 시작 나이·국적 변형을 생성합니다. 알 수 없는 XML이나 변경된 파일은 거부하고 원본을 덮어쓰지 않습니다. 원본의 나이·주 국적 외의 필드는 구조와 바이트 변경 범위를 검사하며 보존합니다. 결과는 FM26 Editor로 가져올 XML이고, FMF 컴파일러가 아닙니다.

```bash
python tools/build_longplay.py cups.xml output
python tools/build_release.py '/path/to/ai update.fmf'
```

두 번째 명령은 기존 `downloads/fm26-review.zip`의 컵 XML, 현재 도구·문서, 사용자가 저장해 보낸 FMF로 새 통합판을 만듭니다. 출력 파일은 결정적인 ZIP 메타데이터를 사용하며 SHA-256과 CRC를 확인합니다. 빌드된 게임 실행 파일이나 제3자 DLL을 포함하지 않습니다.

확인한 외부 참고 자료:

- [FMSuperScout 국가 ID 표](https://github.com/mavarobli/FMSuperScout/blob/main/app/nations.js): 국적 16개 ID를 확인하는 참고. 기존 XML의 Nnat 참조 형식과 함께 사용했으며 실제 에디터 해석은 아직 미검증입니다.
- [FMSuperScout FMF 형식 조사](https://github.com/mavarobli/FMSuperScout/blob/main/docs/fmf-format.md): 공개 Zstandard 인덱스와 해독 불가능한 본문을 구분. 사용자 FMF에서도 인덱스만 확인했고 본문 변경에는 사용하지 않았습니다.
- [fm26rte](https://github.com/poi82999/fm26rte), [fmforge](https://github.com/KaliszS/fmforge): 실행 중 편집과 향후 생성 선수 형식을 조사했으나, 이 사용자 버전에서 검증할 수 없어 메모리 쓰기나 성별 미확인 미래 생성 규칙을 도구에 넣지 않았습니다. 해당 프로젝트의 소스·실행 파일을 복사해 배포하지 않습니다.

검증 결과는 도구와 파일의 정합성 범위입니다. FM26 Editor 규칙 시험, 게임 속 얼굴 표시, 국가대표 대회·다음 시즌 컵·장기 노화 동작을 대신하지 않습니다.
