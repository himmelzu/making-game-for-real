# 런타임 자동화의 출처와 미검증 범위

새 Python 구현은 이 저장소에서 작성한 코드이며, 아래 프로젝트의 소스·게임 DLL·Cheat Table 실행 파일을 배포에 복사하지 않습니다. 구조의 사실과 읽기 경로만 참고했습니다.

- [FMSuperScout 고정 커밋](https://github.com/mavarobli/FMSuperScout/tree/759b9cc35f5e156b01c901b2fa24c37903fad1b0)
- [fm26rte 고정 커밋](https://github.com/poi82999/fm26rte/tree/001cf00d1f1d509e5b6d440ebb1d27a45fb42726)

| 사실 | 출처 위치 |
| --- | --- |
| object UID +0x0C / random ID +0x10 / row ID +8 | scout Fields.cs, RTE CE enum objo |
| person은 player+0x288 또는 겸직+0x380 | vtable RTTI meta+4, scout Dumper/MemScan |
| 여성은 person+0x19의 bit0x10 | scout 현행 Fields.cs, RTE pero.Pfl1 |
| 전체 이름 +0x40, first/last/common +0x50/+0x58/+0x60 | RTE CE 이름 변경 루틴, scout 이름 읽기 |
| 생년월일 +0x88, 상위16비트 생년·하위9비트 연중일 | scout/CE 날짜 읽기 |
| CA/PA는 player+0x264/+0x266 | scout 현행 Fields.cs, RTE fields.rs |
| 능력치 고정 후보는 person+0x132의 bit0x20 | CE editable Person Details Freeze Attributes, Pfl2=306 |
| 현재 게임 날짜 AOB | FMCET.CT의 CURRENT GAME DATE, CETableDetails expectedModule=game_plugin.dll |

날짜 명령 패턴은 `0F B7 05 ?? ?? ?? ?? 66 41 33 87`이며 signed RIP-relative 변위를 읽습니다. target은 포인터 슬롯이 아니라 날짜 자체를 담은 4바이트 주소입니다. 정확히 한 패턴·모듈 내 주소·유효한 날짜·두 번 같은 값 조건을 모두 요구합니다.

20억 이상의 UID를 생성 선수 증거로 사용하지 않습니다. 원래 추가 연예인 선수도 20억대 ID를 사용하기 때문입니다. 0x18의 youth 비트도 생성 선수 확정 근거로 사용하지 않습니다. 최초 전체 기준 스캔과 나이·성별·미배정 여부로 후보만 만듭니다.

은퇴 날짜는 별도 retirement table의 row 기준 값입니다. 이를 person+0x0C에 쓰면 UID가 손상될 수 있어 사용하지 않습니다. 구단 은퇴 비트도 여성 비트와 같은 바이트에 있으며, 이것만 지운다고 전체 은퇴를 막을 수 없어 수정하지 않습니다.

이름 참조를 복사하는 방식은 원본 문자열의 공유 내용 자체를 덮어쓰지 않으려는 실험 설계입니다. 메모리 풀 소유권·refcount·직렬화·원본 선수 은퇴 후 수명은 확인하지 못했습니다. 그래서 완전 무인 자동 루프를 제공하지 않고 명시적인 후보 검토·일괄 적용을 둡니다. 게임 버전 번호 일치와 구조 검사도 실제 게임 저장·재로드 검증을 대체하지 않습니다.

Windows 프로세스 접근은 ReadProcessMemory/WriteProcessMemory뿐이며, 원격 코드 실행·DLL 주입·디버그 권한 강제 활성화는 사용하지 않습니다. 읽기 단계에는 쓰기 handle을 열지 않고, 실험적 적용을 명시한 뒤에만 필요한 쓰기 권한을 요청합니다.

배포에 동봉하는 Windows 실행 환경은 [Python 공식 CPython 3.14.8 embeddable x64](https://www.python.org/ftp/python/3.14.8/python-3.14.8-embed-amd64.zip)입니다. 공식 HTTPS에서 내려받은 archive SHA-256은 `a93abe456ab01bd96d7a085b3cdb6566b3063f4241360d114142fbdb07f0a310`입니다. `LICENSE.txt`를 그대로 포함하며 `python314._pth`만 로컬 앱 경로를 추가했습니다. FMRTE 라이선스나 별도 Python 설치가 필요한 배포는 아닙니다.
