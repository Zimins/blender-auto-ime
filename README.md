# blender-ko — 한글 IME에서도 Blender 단축키가 동작하게 (macOS)

Blender는 영문 입력 상태에서만 단축키(G, R, S, X …)가 잘 동작하고, **한글 입력 상태에서는
단축키가 먹지 않는** 문제가 있습니다. 이 저장소는 그 문제를 macOS에서 해결합니다.

> 대상 환경: **Blender 5.1.1 / macOS 26**, 한글 2벌식 입력기.

---

## TL;DR — 결론부터

처음에 떠올리기 쉬운 방식("`G`가 `ㅎ`를 만드니, `ㅎ`를 가로채서 `G` 단축키로 되돌리자")은
**macOS에서는 원리적으로 동작하지 않습니다.** Blender 소스를 분석해 확인한 사실:

1. **macOS Blender는 `event.type`을 "물리 키코드"에서 만듭니다** (`GHOST_SystemCocoa.mm`의
   `convertKey()`). 즉 물리 `G` 키를 누르면 한/영 상태와 상관없이 `event.type == 'G'`.
   → **이벤트가 키맵까지 도달하기만 하면 단축키는 이미 동작합니다.** 되돌릴 필요가 없습니다.
2. **IME는 텍스트 입력란을 편집할 때만 켜집니다.** 뷰포트에선 꺼져 있어야 정상이고,
   그러면 `G`가 그냥 동작해야 합니다.
3. **단축키가 깨지는 진짜 원인**은, 한글 IME가 켜진 상태에서 OS 입력 소스(2벌식)가
   맨키를 IME 조합용으로 **가로채/삼켜** Blender 키맵까지 닿지 못하게 만들기 때문입니다.
   (upstream 이슈 [#93421](https://projects.blender.org/blender/blender/issues/93421),
   [#130662](https://projects.blender.org/blender/blender/issues/130662) 계열)
4. **키가 IME에 삼켜지면 `interpretKeyEvents`로 라우팅되어, Blender가 아예 키 이벤트를
   발생시키지 않을 가능성이 높습니다.** 그러면 파이썬 모달 오퍼레이터도 그 키를 못 봅니다
   → **자모를 가로채 되돌리는 방식 자체가 불가능.**
5. 검증 결과 **자모-리매핑 방식의 기존 애드온은 존재하지 않습니다.** macOS에서 실제로
   통하는 유일한 기존 해법("Mac Language Switch", 유료/폐쇄소스)도 **OS 입력 소스를
   전환**하는 방식입니다.

### 그래서 올바른 해법

> **단축키를 쓰는 동안에는 OS 입력 소스를 영문(ASCII)으로 두고, 한글을 칠 때만 2벌식으로
> 바꾼다.** 이걸 Blender 애드온이 상황에 맞게 자동/반자동으로 해 줍니다.

이 방식은 **"한글 입력 유지"와 "단축키 동작"을 둘 다** 만족합니다. 핵심 트릭:
`Cmd`/`Ctrl` 조합 단축키는 IME가 가로채지 않으므로(Blender가 의도적으로 제외),
**한글로 키가 삼켜지는 상태에서도 `Cmd` 조합 핫키는 항상 동작**합니다 → 이걸 "영문 복구"
버튼으로 씁니다.

---

## 들어있는 것

| 파일 | 역할 |
|---|---|
| `ko_focus_switch.py` | **OS 레벨 데몬 (권장).** Blender 애드온이 아니라 macOS에 상주하며 '맨 앞 앱'을 감시 → Blender로 들어가면 영문, 나가면 원래 입력기로 복원. 외부 의존성 0. → ["OS 레벨" 섹션](#대안-blender-애드온이-아니라-os-레벨로-권장) |
| `blender_ko_diag.py` | **진단 애드온.** 한글 IME 상태에서 Blender가 실제로 받는 키 이벤트를 측정. **먼저 실행해서 당신 환경의 실제 동작을 확인**하세요. |
| `blender_ko_switch.py` | **애드온 방식.** 상황에 맞게 OS 입력 소스를 전환(영문↔한글). 외부 의존성 0(ctypes로 Carbon TIS 직접 호출), 선택적으로 `macism` CLI 사용. (OS 레벨 데몬을 쓰면 이건 끄세요) |
| `README.md` | 이 문서 |

---

## 1단계: 진단 (먼저 실행)

당신 기계(Blender 5.1.1 / macOS 26)에서 "한글 IME 켠 채 뷰포트에서 `G`를 누를 때
Blender가 실제로 무엇을 받는지"는 직접 찍어봐야 100% 확정됩니다.

1. Blender → `Edit > Preferences > Add-ons > Install from Disk…` 에서 `blender_ko_diag.py` 선택
   (또는 파일을 Blender 창에 드래그 앤 드롭). 체크박스 ON.
2. 3D 뷰포트 우측 사이드바(`N`) → **`KO Diag`** 탭 → **▶ 진단 시작**.
3. macOS 입력기를 **2벌식 한글**로 전환.
4. 뷰포트(아무것도 편집 안 하는 상태)에서 `G`, `R`, `S`, `X` 를 눌러본다.
5. 이어서 오브젝트를 `F2`로 이름변경 → **아무것도 안 치고** `Esc` → 뷰포트 클릭 → 다시 `G`.
6. **■ 진단 정지**. 로그 확인:
   - Blender `Text Editor` 상단 드롭다운에서 **`KO_DIAG_LOG`** 선택.
   - (터미널에서 Blender를 실행했다면 표준출력에도 동일 출력)
7. 로그를 그대로 복사해서 공유해 주세요.

### 로그 해석 가이드

- 뷰포트 `G`가 `type=G` 로 찍힘 → **단축키는 본래 동작.** 문제는 "텍스트칸 건드린 뒤
  깨지는" stale-IME 한정. 메인 애드온의 자동/핫키 전환으로 깔끔히 해결됩니다.
- 뷰포트 `G`가 `type=TEXTINPUT` 이고 `unicode='ㅎ'` 로 찍힘 → IME가 뷰포트에서도 키를
  조합으로 가져가는 상태. → 입력 소스 전환(이 애드온)이 정답.
- 뷰포트 `G`를 눌렀는데 **아무 줄도 안 찍힘** → 그 키는 파이썬에 아예 도달하지 않음
  (IME가 완전히 삼킴). → 자모-리매핑은 불가능이 확정. 입력 소스 전환이 유일한 해법.

> 진단 모달은 임시용입니다. 켜 두면 자동저장/undo가 억제될 수 있으니 측정 후 꼭 정지.

---

## 2단계: 메인 애드온 설치 & 설정

1. `blender_ko_switch.py` 를 위와 같은 방식으로 설치하고 체크박스 ON.
2. `Preferences > Add-ons` 에서 **이 애드온을 펼칩니다.**
3. **[입력 소스 목록 출력]** 클릭 → `Text Editor`의 `KO_INPUT_SOURCES` 에서 내 입력 소스 ID 확인.
   - 영문 예: `com.apple.keylayout.ABC` (또는 `…US`)
   - 한글 예: `com.apple.inputmethod.Korean.2SetKorean`
4. 기본값과 다르면: **원하는 입력기로 OS에서 바꾼 뒤** 각 줄의 **스포이드(현재값 저장)** 버튼을 눌러
   영문/한글 슬롯을 정확히 채웁니다.
5. 하단 **[영문][한글][토글]** 버튼으로 즉시 전환이 되는지 테스트.

### 사용법

| 상황 | 동작 |
|---|---|
| 3D **텍스트 오브젝트 편집** | 진입 시엔 입력기를 **그대로 둠**(원하는 입력기로 직접 타이핑) → 빠져나오면 **자동으로 영문** 복구. (진입 시 한글 자동전환을 원하면 Preferences에서 켤 수 있음) |
| 파일 로드/Blender 시작 | **자동으로 영문** (뷰포트가 단축키 친화 상태로 시작) |
| 뷰포트에서 단축키가 안 먹을 때 | **`Cmd+Option+E`** (강제 영문) → 즉시 복구 |
| 영문↔한글 빠른 토글 | **`Cmd+Option+K`** |
| `F2` 이름변경 / 검색창에 한글 입력 | macOS **한/영 키**로 한글 전환 후 입력. (이 칸들은 Blender가 포커스를 파이썬에 노출하지 않아 자동 감지 불가) |

> `Cmd` 조합 핫키는 IME가 키를 삼키는 상태에서도 동작합니다(IME 우회). 그래서 어떤
> 상태에 빠지든 `Cmd+Option+E` 한 번이면 항상 영문으로 빠져나올 수 있습니다.

### 왜 "자동 복구"나 "ㅎ→Grab 리매핑"을 넣지 않았나 (정직하게)

> **"ㅎ를 g랑 똑같이"는 왜 불가능한가:** 한글은 *조합형* 입력기라, 뷰포트에서 키를 눌러도
> Blender엔 자모(ㅎ)도 키종류(G)도 실리지 않는 **빈 이벤트**(`type='' unicode=''`)만 옵니다.
> 잡아서 되돌릴 정보 자체가 없습니다(진단 로그로 실측 확인). 그래서 키맵으로 `ㅎ→Grab`을
> 만드는 건 원천 불가능합니다.

"빈 이벤트가 오는 순간 = 한글이 키를 삼키는 중"임을 역이용해 **즉시 영문으로 자동 전환**하는
*자동 복구* 방식도 검토했지만, 다음 이유로 채택하지 않았습니다:

- **한글을 칠 의도와 구분이 안 됨.** `F2 이름변경/검색/N-패널`은 텍스트칸 포커스를 파이썬에
  노출하지 않고 `mode`도 그대로라, 자동 복구가 "한글을 치려던 입력"까지 영문으로 끌어내려
  바로 그 칸에서 한글 입력이 끊길 위험이 있습니다.
- **자동저장이 꺼짐.** 감지에 백그라운드 모달이 상주해야 하는데, 모달 오퍼레이터 특성상
  켜진 동안 Blender 자동저장이 비활성화될 수 있습니다.

그래서 이 애드온은 **뷰포트는 영문을 기본으로 두고, 한글은 칠 때만 전환**하는 결정적이고
안전한 방식을 택했습니다. 뷰포트에서 단축키가 안 먹으면 `Cmd+Option+E` 한 번으로 즉시 복구.

### 한계 (정직하게)

- **`F2` 이름변경 / 검색 팝업 / N-패널 텍스트칸**은 Blender가 "텍스트칸이 포커스됐다"는
  정보를 파이썬에 노출하지 않아 **자동 한/영 전환이 불가능**합니다. 이 칸에 한글을 칠 땐
  macOS 한/영 키로 직접 전환하세요(한국 사용자에겐 반사적인 동작). 입력이 끝나고 뷰포트로
  돌아오면 `Cmd+Option+E` 또는 토글로 영문 복귀.
- 완전 무결한 "한글/영문 자동 감지"는 애드온만으로는 불가능합니다(Blender API 제약). 위
  자동전환 + IME 우회 핫키 조합이 애드온으로 가능한 최선의 근사입니다.

---

## 전환 백엔드: ctypes vs macism

- **기본(AUTO/CTYPES)**: 순수 ctypes로 macOS Carbon **TIS API**(`TISSelectInputSource`)를
  직접 호출 → **외부 의존성 0.** Blender가 활성(frontmost) 상태에서 자기 입력 소스를 바꾸는
  용도이므로 대체로 잘 동작합니다.
- **macism(선택, 더 견고)**: 최신 macOS에서 단순 `TISSelectInputSource`가 "아이콘만 바뀌고
  실제 전환은 리포커스 전까지 안 되는" 케이스가 보고됩니다. 이를 우회하는 검증된 CLI:
  ```sh
  brew install laishulu/macism/macism
  ```
  설치 후 Preferences에서 `backend = MACISM`(또는 `AUTO`)로 두면 됩니다.

**`AUTO`(기본)의 똑똑한 동작**: 영문(ABC, 키보드 레이아웃)은 ctypes 전환이 견고하므로
ctypes를 먼저 쓰고, **한글(IME) 전환은 macism을 먼저** 시도합니다(설치돼 있으면). 또한
ctypes로 전환한 직후 현재 입력 소스를 다시 읽어 **실제 반영됐는지 확인**하고, 안 됐으면
자동으로 macism으로 폴백합니다. 즉 macism이 없어도 동작하고, 있으면 한글 전환이 더
견고해집니다.

---

## 대안: Blender 애드온이 아니라 OS 레벨로 (권장)

애드온은 Blender API 제약(텍스트칸 포커스를 파이썬에 안 알려줌) 때문에 자동 감지에 한계가
있습니다. 그래서 **이 저장소는 OS 레벨 데몬 `ko_focus_switch.py` 를 권장**합니다 — macOS에
상주하며 '맨 앞 앱'을 감시해서, 규칙대로 입력 소스를 바꿔 줍니다.

| 이벤트 | 동작 |
|---|---|
| Blender가 맨 앞으로 옴 | 진입 직전 입력기를 **기억**해두고 → **영문**으로 전환 |
| Blender에서 빠져나감 | 기억해둔 **진입 전 입력기로 복원** (영문이었으면 영문, 한글이었으면 한글) |
| Blender 안에서 수동으로 IME 전환(텍스트 입력) | **건드리지 않음** (포커스 변화가 없으면 가만히 있음) |

> **한글 전용이 아닙니다.** 데몬은 한글을 하드코딩하지 않고 *진입 직전에 쓰던 입력기*를
> 기억해 복원하므로, **일본어·중국어·베트남어 등 어떤 IME든** 그대로 동작합니다. Blender에서
> 강제할 '영문' 소스만 `--english` 로 지정하면 끝(기본 `com.apple.keylayout.ABC`). IME 반영
> 우회(런루프 펌프)도 CJK 공통 레이스를 잡는 것이라 한·일·중 IME에 모두 통합니다. 또한
> Blender 외 다른 앱(예: 게임/CAD)에 쓰고 싶으면 `--blender-bundle <앱 번들 ID>` 로 대상 앱만
> 바꾸면 그대로 범용 "앱 진입 시 영문" 도구가 됩니다.

**외부 의존성 0.** 맨 앞 앱은 `NSWorkspace`(ctypes로 Obj-C 런타임 직접 호출), 입력 소스
전환은 Carbon TIS API로 처리합니다. PyObjC도, macism도 필요 없습니다.

```sh
# 1) 먼저 포그라운드로 동작 확인 (로그를 보며 Blender ↔ 다른 앱 전환 테스트)
python3 ko_focus_switch.py run

# 2) 좋으면 로그인 시 자동 실행되도록 상주 설치 (LaunchAgent)
python3 ko_focus_switch.py install     # 끄기: uninstall
#   로그: tail -f ~/Library/Logs/dev.blender-ko.focus-switch.log

# 보조: 입력 소스 ID 목록 / 현재값 / 맨 앞 앱 확인
python3 ko_focus_switch.py list
python3 ko_focus_switch.py current
python3 ko_focus_switch.py watch       # Blender의 번들 ID 확인용
```

> **데몬을 쓰면 `blender_ko_switch.py` 애드온은 끄세요** (둘 다 켜면 중복 전환).

### 구현 메모 (macOS 26에서 겪은 함정 2가지)

1. **맨 앞 앱이 안 바뀌어 보이는 문제** — `NSWorkspace.frontmostApplication` 은 런루프로 오는
   알림으로 갱신된다. `time.sleep` 으로만 기다리면 알림을 못 받아 시작 시점 값에 멈춘다.
   → 폴링 대기를 `CFRunLoopRunInMode`(런루프 펌프)로 해야 한다.
2. **한글(IME)로 전환이 반영 안 되는 문제** — `TISSelectInputSource` 는 영문(키보드 레이아웃)
   전환은 견고하지만, IME 전환은 status 0 을 반환하고도 '다른 앱으로 갔다 오기 전까진' 실제로
   안 바뀐다(CJK 레이스). → 전환 직후 런루프를 잠깐(≈0.15s) 펌프해 commit 을 유도하면, **macism
   없이도** ctypes 만으로 반영된다. (그래도 안 되는 환경이면 `brew install laishulu/macism/macism`
   후 `--backend AUTO`/`MACISM`.)
3. **Blender 번들 ID** — 빌드에 따라 `org.blender.blender` 또는 `org.blenderfoundation.blender`.
   둘 다 기본 매칭한다. 다른 값이면 `watch` 로 확인 후 `run --blender-bundle <ID>`.

### 더 단순하게(범용 도구)

위 데몬 대신 앱별 입력 소스 고정 도구를 써도 됩니다(단, 3D 텍스트 편집까지 챙기진 못함):

- [Input Source Pro](https://github.com/runjuu/InputSourcePro) — 앱/웹사이트별 입력 소스 자동 전환
- [SwitchKey](https://github.com/itsuhane/SwitchKey) — 앱별 입력 소스 자동 활성

---

## 배포: 더블클릭 메뉴바 앱(.app) 만들기

비개발자도 쓰게 하려면 파이썬을 안 만지도록 **메뉴바 `.app`** 으로 묶습니다. 메뉴바 ⌨︎
아이콘에서 켜기/끄기·상태·로그인 시 자동 실행을 제어합니다.

| 파일 | 역할 |
|---|---|
| `menubar_app.py` | rumps 메뉴바 앱. 코어(`ko_focus_switch.FocusWatcher`)를 그대로 재사용 |
| `pyproject.toml` | 패키지 메타/진입점 (`pipx install` 용) |
| `packaging/KoFocusSwitch.spec` | PyInstaller 빌드 명세 (Dock 없는 `LSUIElement` 메뉴바 앱) |
| `packaging/build_macos.sh` | 로컬 빌드(격리 venv) |
| `.github/workflows/build-macos.yml` | macOS 러너에서 `.app` 자동 빌드 → 태그 푸시 시 릴리스 첨부 |

**로컬 빌드:**
```sh
bash packaging/build_macos.sh
#   → dist/KO Focus Switch.app  +  dist/KO-Focus-Switch-macos.zip
```
첫 실행은 서명/공증을 안 했으면 Gatekeeper 때문에 **우클릭 → 열기** 한 번만 거치면 됩니다.

**자동 빌드/배포(GitHub):** `git tag v0.2.1 && git push --tags` → Actions가 `.app` 을 빌드해
zip으로 릴리스에 첨부. 보안 경고까지 없애려면 Apple 개발자 계정으로 **코드서명 + 공증**을
CI에 추가하면 됩니다(현재는 미서명 = 우클릭-열기 방식).

> **개발자용(파이썬 보유):** `pipx install .` 후 `ko-focus-switch run` (CLI) /
> `ko-focus-switch-menubar` (메뉴바). 코어 CLI/데몬은 **외부 의존성 0**, 메뉴바 GUI만 `rumps` 사용.

### Windows는?

구조는 플랫폼 무관(`FocusWatcher`)이라, Windows는 프리미티브 3개만 갈아끼우면 됩니다
(맨 앞 앱 `GetForegroundWindow`→`blender.exe`, 입력기 전환 `IMM32`/`VK_HANGUL`, 자동실행
레지스트리/작업 스케줄러). 아직 미구현 — 추가 시 `backends/windows.py` 로 분리 예정.

---

## 동작 근거가 된 1차 출처

- Blender GHOST(macOS) IME 처리: `intern/ghost/intern/GHOST_WindowViewCocoa.hh`,
  `GHOST_SystemCocoa.mm` (`convertKey`, `isProcessedByIme`, `checkKeyCodeIsControlChar`)
- 텍스트 편집 시에만 IME 활성화: `source/blender/editors/interface/interface_handlers.cc`
  (`textedit_ime_begin/end` → `wm_window_IME_begin/end`)
- 단축키-깨짐 버그: 이슈 #93421, #93986, #100634, #130662, #128742
- Cmd/Ctrl 조합이 IME 우회: commit `8b44b756d85` / `7336af325937`
  ("Fix some shortcut keys not working on macOS with Japanese input")
- bpy API: `docs.blender.org/api/current` (`Event`, `Context.temp_override`,
  `KeyMapItem`, `bpy.app.timers`, `bpy.app.handlers`)
- 두벌식(KS X 5002) 매핑 참고(진단 해석용): `ko.wikipedia.org/wiki/두벌식_자판`

---

## 참고: 표준 두벌식(KS X 5002) 매핑

진단 로그에서 어떤 자모가 어떤 물리 키인지 해석할 때 참고하세요. (예: `ㅎ` → 물리 `G`)

```
물리키 → 자모 (기본)
q ㅂ  w ㅈ  e ㄷ  r ㄱ  t ㅅ  y ㅛ  u ㅕ  i ㅑ  o ㅐ  p ㅔ
a ㅁ  s ㄴ  d ㅇ  f ㄹ  g ㅎ  h ㅗ  j ㅓ  k ㅏ  l ㅣ
z ㅋ  x ㅌ  c ㅊ  v ㅍ  b ㅠ  n ㅜ  m ㅡ

Shift로 달라지는 7개만:
Q ㅃ  W ㅉ  E ㄸ  R ㄲ  T ㅆ  O ㅒ  P ㅖ
```
