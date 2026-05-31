# SPDX-License-Identifier: MIT
"""
blender_ko_switch — 한글 IME에서도 Blender 단축키가 동작하게 하는 애드온 (macOS)

동작 원리 (Blender 소스 분석 기반):
    - macOS Blender는 event.type 을 '물리 키코드'에서 만든다. 즉 이벤트가 도달만 하면
      한/영 상태와 무관하게 G=Grab 등 단축키가 이미 동작한다.
    - 단축키가 깨지는 이유는, 한글 IME가 켜진 상태에서 OS 입력 소스(2벌식 한글)가
      맨키(G/R/S/X…)를 IME 조합용으로 가로채/삼켜서, Blender 키맵까지 도달하지 못하기
      때문이다 (upstream 이슈 #93421 / #130662 계열).
    - 따라서 올바른 해법은 'IME가 가로채지 못하게' 만드는 것 = 단축키를 쓰는 동안에는
      OS 입력 소스를 ASCII(영문, 예: ABC)로 두고, 한글을 칠 때만 2벌식으로 바꾸는 것.

이 애드온이 하는 일:
    1) 3D 텍스트 오브젝트 편집(EDIT_TEXT)에서 빠져나오면 자동으로 '영문'으로 전환해
       뷰포트 단축키를 즉시 복구. 진입 시엔 기본적으로 입력기를 건드리지 않아, 3D
       텍스트를 그때그때 원하는 입력기(영문/한글)로 직접 칠 수 있다. (진입 시 한글 자동
       전환을 원하면 Preferences 에서 켤 수 있음)
    2) IME를 우회하는 Cmd 조합 핫키 제공:
         · Cmd+Option+K : 영문/한글 토글
         · Cmd+Option+E : 강제 영문 (단축키가 안 먹을 때 한 방에 복구)
       (Cmd/Ctrl 조합은 IME가 가로채지 않으므로, 한글로 키가 삼켜지는 상태에서도 동작)
    3) 파일 로드/시작 시 영문으로 맞춰 뷰포트가 단축키 친화 상태로 시작.

입력 소스 전환 백엔드:
    - 기본은 순수 ctypes로 macOS Carbon TIS API(TISSelectInputSource) 호출 → 외부 의존성 0.
    - 더 견고한 전환이 필요하면(특히 최신 macOS) `macism` CLI 사용 가능:
        brew install laishulu/macism/macism
      설치 후 Preferences에서 backend=MACISM(또는 AUTO) 선택.

설치:
    Edit > Preferences > Add-ons > Install from Disk… 로 이 .py 선택 후 체크.
    (또는 .py 파일을 Blender 창에 드래그 앤 드롭)

먼저 할 일:
    Preferences > Add-ons > 이 애드온 펼치기 에서
      · [입력 소스 목록 출력] 으로 내 '한글' 소스 ID 확인 (예: com.apple.inputmethod.Korean.2SetKorean)
      · 영문/한글 ID가 기본값과 다르면, 해당 입력기로 바꾼 뒤 [현재값을 영문/한글으로 저장] 클릭.
"""

bl_info = {
    "name": "KO IME Shortcut Switch (한글 단축키)",
    "author": "blender-ko",
    "version": (0, 1, 1),
    "blender": (4, 0, 0),
    "location": "Preferences > Add-ons; View3D Cmd+Option+K/E",
    "description": "한글 IME에서도 Blender 단축키가 동작하도록 OS 입력 소스를 상황에 맞게 전환한다 (macOS)",
    "category": "System",
}

import sys
import os
import shutil
import subprocess
import time

import bpy

IS_MACOS = sys.platform == "darwin"

# 기본 입력 소스 ID (macOS)
DEFAULT_ENGLISH_ID = "com.apple.keylayout.ABC"
DEFAULT_KOREAN_ID = "com.apple.inputmethod.Korean.2SetKorean"

SOURCES_TEXT_NAME = "KO_INPUT_SOURCES"

# 마지막 전환 결과/오류를 UI에 보여주기 위한 모듈 상태
_status = {"last": "", "last_ok": True}
# [입력 소스 목록 출력]로 채워지는 캐시
_available_sources = []


# ───────────────────────────────────────────────────────────────────────────
# macOS Carbon TIS (Text Input Source) — 순수 ctypes 백엔드
# ───────────────────────────────────────────────────────────────────────────
_tis = None  # 초기화된 ctypes 핸들 묶음 (dict) 또는 False(실패)


def _init_tis():
    """Carbon/CoreFoundation 의 TIS API를 ctypes로 바인딩한다. 한 번만 수행."""
    global _tis
    if _tis is not None:
        return _tis
    if not IS_MACOS:
        _tis = False
        return _tis
    try:
        import ctypes
        import ctypes.util

        def _load(name, *fallbacks):
            path = ctypes.util.find_library(name)
            candidates = [path] + list(fallbacks) if path else list(fallbacks)
            for c in candidates:
                if not c:
                    continue
                try:
                    return ctypes.CDLL(c)
                except OSError:
                    continue
            return None

        cf = _load(
            "CoreFoundation",
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation",
        )
        carbon = _load(
            "Carbon",
            "/System/Library/Frameworks/Carbon.framework/Carbon",
        )
        if cf is None or carbon is None:
            print("[KO_SWITCH] Carbon/CoreFoundation 로드 실패")
            _tis = False
            return _tis

        # TIS 심볼이 Carbon에 없으면 HIToolbox 직접 로드 시도
        if not hasattr(carbon, "TISSelectInputSource"):
            hit = _load(
                "HIToolbox",
                "/System/Library/Frameworks/Carbon.framework/Frameworks/"
                "HIToolbox.framework/HIToolbox",
            )
            if hit is not None and hasattr(hit, "TISSelectInputSource"):
                carbon = hit

        c_void_p = ctypes.c_void_p
        c_long = ctypes.c_long
        c_int32 = ctypes.c_int32
        c_uint32 = ctypes.c_uint32
        c_bool = ctypes.c_bool
        c_char_p = ctypes.c_char_p

        # --- CoreFoundation prototypes ---
        cf.CFRelease.argtypes = [c_void_p]
        cf.CFRelease.restype = None
        cf.CFArrayGetCount.argtypes = [c_void_p]
        cf.CFArrayGetCount.restype = c_long
        cf.CFArrayGetValueAtIndex.argtypes = [c_void_p, c_long]
        cf.CFArrayGetValueAtIndex.restype = c_void_p
        cf.CFStringGetCStringPtr.argtypes = [c_void_p, c_uint32]
        cf.CFStringGetCStringPtr.restype = c_char_p
        cf.CFStringGetCString.argtypes = [c_void_p, c_char_p, c_long, c_uint32]
        cf.CFStringGetCString.restype = c_bool

        # --- TIS prototypes ---
        carbon.TISCreateInputSourceList.argtypes = [c_void_p, c_bool]
        carbon.TISCreateInputSourceList.restype = c_void_p
        carbon.TISCopyCurrentKeyboardInputSource.argtypes = []
        carbon.TISCopyCurrentKeyboardInputSource.restype = c_void_p
        carbon.TISGetInputSourceProperty.argtypes = [c_void_p, c_void_p]
        carbon.TISGetInputSourceProperty.restype = c_void_p
        carbon.TISSelectInputSource.argtypes = [c_void_p]
        carbon.TISSelectInputSource.restype = c_int32

        # 내보낸 CFStringRef 상수 (입력 소스 ID 프로퍼티 키)
        prop_id = c_void_p.in_dll(carbon, "kTISPropertyInputSourceID")

        _tis = {
            "ctypes": ctypes,
            "cf": cf,
            "carbon": carbon,
            "kTISPropertyInputSourceID": prop_id,
            "kCFStringEncodingUTF8": 0x08000100,
        }
        return _tis
    except Exception as ex:  # noqa: BLE001
        print("[KO_SWITCH] TIS 초기화 예외:", ex)
        _tis = False
        return _tis


def _cfstring_to_str(t, cfstr):
    if not cfstr:
        return None
    cf = t["cf"]
    enc = t["kCFStringEncodingUTF8"]
    ptr = cf.CFStringGetCStringPtr(cfstr, enc)
    if ptr:  # ptr 는 c_char_p → bytes
        try:
            return ptr.decode("utf-8")
        except Exception:  # noqa: BLE001
            pass
    ctypes = t["ctypes"]
    buf = ctypes.create_string_buffer(1024)
    ok = cf.CFStringGetCString(cfstr, buf, 1024, enc)
    if ok:
        try:
            return buf.value.decode("utf-8")
        except Exception:  # noqa: BLE001
            return None
    return None


def _source_id(t, src):
    cfstr = t["carbon"].TISGetInputSourceProperty(src, t["kTISPropertyInputSourceID"])
    return _cfstring_to_str(t, cfstr)


def tis_current_source_id():
    t = _init_tis()
    if not t:
        return None
    carbon = t["carbon"]
    cf = t["cf"]
    src = carbon.TISCopyCurrentKeyboardInputSource()
    if not src:
        return None
    try:
        return _source_id(t, src)
    finally:
        cf.CFRelease(src)


def tis_list_source_ids():
    t = _init_tis()
    if not t:
        return []
    carbon = t["carbon"]
    cf = t["cf"]
    arr = carbon.TISCreateInputSourceList(None, True)
    if not arr:
        return []
    ids = []
    try:
        n = cf.CFArrayGetCount(arr)
        for i in range(n):
            src = cf.CFArrayGetValueAtIndex(arr, i)
            if not src:
                continue
            sid = _source_id(t, src)
            if sid:
                ids.append(sid)
    finally:
        cf.CFRelease(arr)
    # 중복 제거(키보드+IME 변형) 하되 순서 유지
    seen = set()
    out = []
    for s in ids:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def tis_select(source_id):
    """주어진 ID의 입력 소스를 선택. (성공여부, 메시지)"""
    t = _init_tis()
    if not t:
        return False, "TIS 사용 불가(비 macOS 또는 초기화 실패)"
    carbon = t["carbon"]
    cf = t["cf"]
    arr = carbon.TISCreateInputSourceList(None, True)
    if not arr:
        return False, "TISCreateInputSourceList NULL"
    try:
        n = cf.CFArrayGetCount(arr)
        for i in range(n):
            src = cf.CFArrayGetValueAtIndex(arr, i)
            if not src:
                continue
            if _source_id(t, src) == source_id:
                status = carbon.TISSelectInputSource(src)
                if status == 0:
                    return True, "ok"
                return False, "OSStatus={}".format(status)
        return False, "입력 소스 ID 없음: {}".format(source_id)
    finally:
        cf.CFRelease(arr)


# ───────────────────────────────────────────────────────────────────────────
# macism CLI 백엔드 (선택)
# ───────────────────────────────────────────────────────────────────────────
def _macism_path(prefs):
    if prefs and prefs.macism_path:
        p = bpy.path.abspath(prefs.macism_path)
        if os.path.exists(p):
            return p
    for c in ("/opt/homebrew/bin/macism", "/usr/local/bin/macism", shutil.which("macism")):
        if c and os.path.exists(c):
            return c
    return None


def macism_select(path, source_id):
    try:
        subprocess.run([path, source_id], timeout=3, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True, "ok"
    except Exception as ex:  # noqa: BLE001
        return False, "macism 실패: {}".format(ex)


def macism_current(path):
    try:
        r = subprocess.run([path], timeout=3, check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        return r.stdout.decode("utf-8").strip()
    except Exception:  # noqa: BLE001
        return None


# ───────────────────────────────────────────────────────────────────────────
# 상위 전환 로직
# ───────────────────────────────────────────────────────────────────────────
def _get_prefs():
    # 레거시 설치는 __name__, 확장(extension) 설치는 __package__ 키를 쓴다.
    for key in (__name__, __package__):
        if not key:
            continue
        try:
            return bpy.context.preferences.addons[key].preferences
        except Exception:  # noqa: BLE001
            continue
    return None


def _is_ime(source_id):
    """입력기(IME) 대상인지 휴리스틱 판단. 한글 2벌식 등은 com.apple.inputmethod.* 형태."""
    return "inputmethod" in (source_id or "")


def switch_to(prefs, source_id):
    """backend 설정에 따라 입력 소스를 전환. (성공여부, 사용백엔드/메시지)

    macOS 26 주의: TISSelectInputSource 는 키보드 레이아웃(영문 ABC) 전환은 견고하지만,
    IME(한글 2벌식)로의 전환은 status 0(noErr)을 반환하고도 실제로는 안 바뀔 수 있다.
    그래서 (1) 한글(IME) 대상은 AUTO에서 macism 우선, (2) ctypes 전환 후에는 현재
    입력 소스를 다시 읽어 '반영 확인', 미반영이면 macism 으로 폴백한다.
    """
    if not IS_MACOS:
        return False, "macOS 전용"
    backend = prefs.backend if prefs else "AUTO"
    errors = []

    def try_macism():
        path = _macism_path(prefs)
        if not path:
            errors.append("macism 미발견(brew install laishulu/macism/macism)")
            return False
        ok, msg = macism_select(path, source_id)
        if ok:
            return True
        errors.append(msg)
        return False

    def try_ctypes():
        ok, msg = tis_select(source_id)
        if not ok:
            errors.append("ctypes(" + msg + ")")
            return False
        # 전환 반영 확인: 현재 소스를 읽어 일치/판단불가면 성공, 불일치면 실패 처리
        cur = tis_current_source_id()
        if cur is None or cur == source_id:
            return True
        errors.append("ctypes(미반영: 현재={})".format(cur))
        return False

    if backend == "MACISM":
        return (True, "macism") if try_macism() else (False, " | ".join(errors))
    if backend == "CTYPES":
        return (True, "ctypes") if try_ctypes() else (False, " | ".join(errors))

    # AUTO
    if _is_ime(source_id):
        if try_macism():
            return True, "macism"
        if try_ctypes():
            return True, "ctypes"
    else:
        if try_ctypes():
            return True, "ctypes"
        if try_macism():
            return True, "macism"
    return False, " | ".join(errors) if errors else "전환 실패"


def _record(ok, msg):
    _status["last_ok"] = ok
    _status["last"] = msg
    tag = "OK" if ok else "FAIL"
    print("[KO_SWITCH] {}: {}".format(tag, msg))


def switch_english(prefs=None):
    prefs = prefs or _get_prefs()
    if prefs is None:
        return False
    ok, msg = switch_to(prefs, prefs.english_source_id)
    _record(ok, "→영문 ({}) {}".format(prefs.english_source_id, msg))
    return ok


def switch_korean(prefs=None):
    prefs = prefs or _get_prefs()
    if prefs is None:
        return False
    ok, msg = switch_to(prefs, prefs.korean_source_id)
    _record(ok, "→한글 ({}) {}".format(prefs.korean_source_id, msg))
    return ok


def current_source_id(prefs=None):
    prefs = prefs or _get_prefs()
    if prefs and prefs.backend == "MACISM":
        path = _macism_path(prefs)
        if path:
            cur = macism_current(path)
            if cur:
                return cur
    return tis_current_source_id()


# ───────────────────────────────────────────────────────────────────────────
# EDIT_TEXT 자동 전환 워처 (타이머 — 모달 아님 → 자동저장/undo 억제 없음)
# ───────────────────────────────────────────────────────────────────────────
_watch = {"prev_mode": None}


def _poll_timer():
    prefs = _get_prefs()
    if prefs is None:
        return 0.5
    interval = max(0.05, prefs.poll_interval)
    try:
        mode = bpy.context.mode
    except Exception:  # noqa: BLE001 — 일부 틱에서 context 제한될 수 있음
        # 이 틱은 context 접근 불가 → prev_mode 손상 막기 위해 갱신하지 않고 다음 틱에
        return interval

    prev = _watch["prev_mode"]
    # 기능이 꺼져 있어도 prev_mode 는 항상 현실과 동기화해 둔다.
    # (그래야 기능을 켜는 순간 stale 상태로 잘못된 전환을 내리지 않는다)
    _watch["prev_mode"] = mode

    if IS_MACOS:
        if mode == "EDIT_TEXT" and prev != "EDIT_TEXT":
            # 진입: 기본은 입력기를 건드리지 않음(사용자가 원하는 입력기로 직접 타이핑)
            if prefs.auto_korean_on_text_edit:
                switch_korean(prefs)
        elif prev == "EDIT_TEXT" and mode != "EDIT_TEXT":
            # 이탈: 기본은 영문으로 복구해 뷰포트 단축키를 즉시 살림
            if prefs.auto_english_on_text_exit:
                switch_english(prefs)
    return interval


def _start_timer():
    if not bpy.app.timers.is_registered(_poll_timer):
        bpy.app.timers.register(_poll_timer, first_interval=0.5, persistent=True)


def _stop_timer():
    if bpy.app.timers.is_registered(_poll_timer):
        try:
            bpy.app.timers.unregister(_poll_timer)
        except Exception:  # noqa: BLE001
            pass


# ───────────────────────────────────────────────────────────────────────────
# 파일 로드 후 처리
# ───────────────────────────────────────────────────────────────────────────
@bpy.app.handlers.persistent
def _on_load_post(_dummy):
    """파일 로드 후: 워처 재가동 + (옵션) 영문 초기화."""
    _watch["prev_mode"] = None
    _start_timer()
    prefs = _get_prefs()
    if prefs and prefs.force_english_on_load:
        # 로드 직후 context가 제한될 수 있으니 한 틱 뒤에 전환.
        # prefs는 발화 시점에 다시 조회한다(리로드로 인한 stale RNA 참조 방지).
        def _deferred_english():
            p = _get_prefs()
            if p and p.force_english_on_load:
                switch_english(p)
            return None
        bpy.app.timers.register(_deferred_english, first_interval=0.3)


# ───────────────────────────────────────────────────────────────────────────
# 오퍼레이터
# ───────────────────────────────────────────────────────────────────────────
class BLENDERKO_OT_input_english(bpy.types.Operator):
    bl_idname = "blenderko.input_english"
    bl_label = "입력 소스: 영문"
    bl_description = "OS 입력 소스를 영문(ASCII)으로 전환 — 단축키가 안 먹을 때 복구용"

    def execute(self, context):
        ok = switch_english()
        self.report({"INFO"} if ok else {"WARNING"}, _status["last"])
        return {"FINISHED"}


class BLENDERKO_OT_input_korean(bpy.types.Operator):
    bl_idname = "blenderko.input_korean"
    bl_label = "입력 소스: 한글"
    bl_description = "OS 입력 소스를 2벌식 한글로 전환"

    def execute(self, context):
        ok = switch_korean()
        self.report({"INFO"} if ok else {"WARNING"}, _status["last"])
        return {"FINISHED"}


class BLENDERKO_OT_input_toggle(bpy.types.Operator):
    bl_idname = "blenderko.input_toggle"
    bl_label = "입력 소스: 영문/한글 토글"
    bl_description = "현재 입력 소스를 보고 영문↔한글 토글 (IME 우회 Cmd 조합 권장)"

    def execute(self, context):
        prefs = _get_prefs()
        if prefs is None:
            self.report({"WARNING"}, "애드온 환경설정을 찾을 수 없음")
            return {"CANCELLED"}
        cur = current_source_id(prefs)
        if cur == prefs.korean_source_id:
            ok = switch_english(prefs)
        else:
            ok = switch_korean(prefs)
        self.report({"INFO"} if ok else {"WARNING"}, _status["last"])
        return {"FINISHED"}


class BLENDERKO_OT_list_sources(bpy.types.Operator):
    bl_idname = "blenderko.list_sources"
    bl_label = "입력 소스 목록 출력"
    bl_description = "설치된 모든 입력 소스 ID를 콘솔과 텍스트 블록(KO_INPUT_SOURCES)에 출력"

    def execute(self, context):
        global _available_sources
        ids = tis_list_source_ids()
        _available_sources = ids
        cur = tis_current_source_id()
        txt = bpy.data.texts.get(SOURCES_TEXT_NAME) or bpy.data.texts.new(SOURCES_TEXT_NAME)
        txt.clear()
        txt.write("=== 설치된 입력 소스 ID 목록 ===\n")
        txt.write("현재 선택: {}\n\n".format(cur))
        for s in ids:
            mark = "  <- 현재" if s == cur else ""
            txt.write(s + mark + "\n")
        print("[KO_SWITCH] 입력 소스 {}개, 현재={}".format(len(ids), cur))
        for s in ids:
            print("    ", s, "(현재)" if s == cur else "")
        self.report({"INFO"}, "{}개 출력 — Text Editor 'KO_INPUT_SOURCES' 확인 (현재: {})".format(len(ids), cur))
        return {"FINISHED"}


class BLENDERKO_OT_capture_current(bpy.types.Operator):
    bl_idname = "blenderko.capture_current"
    bl_label = "현재 입력 소스를 저장"
    bl_description = "지금 선택된 입력 소스 ID를 영문 또는 한글 슬롯에 저장"

    slot: bpy.props.EnumProperty(
        items=[("ENGLISH", "영문", ""), ("KOREAN", "한글", "")],
        default="ENGLISH",
    )

    def execute(self, context):
        prefs = _get_prefs()
        if prefs is None:
            self.report({"WARNING"}, "애드온 환경설정을 찾을 수 없음")
            return {"CANCELLED"}
        cur = current_source_id(prefs)
        if not cur:
            self.report({"WARNING"}, "현재 입력 소스 ID를 읽지 못함")
            return {"CANCELLED"}
        if self.slot == "ENGLISH":
            prefs.english_source_id = cur
            self.report({"INFO"}, "영문 = {}".format(cur))
        else:
            prefs.korean_source_id = cur
            self.report({"INFO"}, "한글 = {}".format(cur))
        return {"FINISHED"}


# ───────────────────────────────────────────────────────────────────────────
# 환경설정
# ───────────────────────────────────────────────────────────────────────────
class BLENDERKO_Prefs(bpy.types.AddonPreferences):
    bl_idname = __name__

    backend: bpy.props.EnumProperty(
        name="전환 백엔드",
        items=[
            ("AUTO", "자동", "ctypes 먼저, 실패 시 macism"),
            ("CTYPES", "ctypes (외부 의존성 없음)", "Carbon TIS API 직접 호출"),
            ("MACISM", "macism CLI", "brew install laishulu/macism/macism 필요(최신 macOS에서 더 견고)"),
        ],
        default="AUTO",
    )
    english_source_id: bpy.props.StringProperty(
        name="영문 소스 ID", default=DEFAULT_ENGLISH_ID,
    )
    korean_source_id: bpy.props.StringProperty(
        name="한글 소스 ID", default=DEFAULT_KOREAN_ID,
    )
    macism_path: bpy.props.StringProperty(
        name="macism 경로", subtype="FILE_PATH", default="",
        description="비우면 /opt/homebrew/bin, /usr/local/bin, PATH 에서 자동 탐색",
    )
    auto_korean_on_text_edit: bpy.props.BoolProperty(
        name="3D 텍스트 편집 진입 시 한글 자동",
        default=False,
        description=("EDIT_TEXT 진입 시 한글로 자동 전환. 끄면(기본) 진입 시 입력 소스를 "
                     "건드리지 않아, 3D 텍스트를 그때그때 원하는 입력기(영문/한글)로 직접 칠 수 있다."),
    )
    auto_english_on_text_exit: bpy.props.BoolProperty(
        name="3D 텍스트 편집 이탈 시 영문 자동",
        default=True,
        description="EDIT_TEXT 에서 빠져나오면 영문으로 자동 전환해 뷰포트 단축키를 즉시 복구한다.",
    )
    force_english_on_load: bpy.props.BoolProperty(
        name="파일 로드/시작 시 영문으로 맞춤", default=True,
    )
    poll_interval: bpy.props.FloatProperty(
        name="감지 주기(초)", default=0.25, min=0.05, max=2.0,
    )

    def draw(self, context):
        layout = self.layout

        if not IS_MACOS:
            layout.label(text="이 애드온은 macOS 전용입니다.", icon="ERROR")
            return

        # 상태
        box = layout.box()
        row = box.row()
        row.label(text="상태", icon="INFO")
        t = _init_tis()
        row.label(text="ctypes TIS: {}".format("사용 가능" if t else "불가"))
        mac = _macism_path(self)
        box.label(text="macism: {}".format(mac if mac else "미설치 (brew install laishulu/macism/macism)"))
        if _status["last"]:
            box.label(text="최근: " + _status["last"],
                      icon="CHECKMARK" if _status["last_ok"] else "ERROR")

        # 백엔드
        layout.prop(self, "backend")

        # 소스 ID
        box = layout.box()
        box.label(text="입력 소스 ID", icon="KEYINGSET")
        box.operator("blenderko.list_sources", icon="PRESET")
        row = box.row(align=True)
        row.prop(self, "english_source_id", text="영문")
        op = row.operator("blenderko.capture_current", text="", icon="EYEDROPPER")
        op.slot = "ENGLISH"
        row = box.row(align=True)
        row.prop(self, "korean_source_id", text="한글")
        op = row.operator("blenderko.capture_current", text="", icon="EYEDROPPER")
        op.slot = "KOREAN"
        box.label(text="원하는 입력기로 OS에서 바꾼 뒤 스포이드(현재값 저장)를 누르면 정확히 잡힙니다.")
        if self.backend == "MACISM" or self.backend == "AUTO":
            box.prop(self, "macism_path")

        # 자동 전환
        box = layout.box()
        box.label(text="자동 전환", icon="RECOVER_LAST")
        box.prop(self, "auto_korean_on_text_edit")
        box.prop(self, "auto_english_on_text_exit")
        box.label(text="기본값: 3D 텍스트 진입 시엔 입력기를 그대로 두고(원하는 입력기로 직접),")
        box.label(text="빠져나올 때만 영문으로 복구해 뷰포트 단축키를 즉시 살립니다.")
        box.prop(self, "force_english_on_load")
        box.prop(self, "poll_interval")

        # 핫키 안내
        box = layout.box()
        box.label(text="단축키 (IME 우회 — 한글로 키가 삼켜져도 동작)", icon="EVENT_OS")
        box.label(text="Cmd+Option+K : 영문/한글 토글")
        box.label(text="Cmd+Option+E : 강제 영문(복구)")
        box.label(text="충돌 시 Keymap > Window 에서 'blenderko.*' 검색해 변경하세요.")

        # 즉시 테스트
        row = layout.row(align=True)
        row.operator("blenderko.input_english", icon="EVENT_E")
        row.operator("blenderko.input_korean", icon="EVENT_K")
        row.operator("blenderko.input_toggle", icon="ARROW_LEFTRIGHT")


# ───────────────────────────────────────────────────────────────────────────
# 키맵
# ───────────────────────────────────────────────────────────────────────────
_addon_keymaps = []


def _register_keymaps():
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if not kc:
        return
    km = kc.keymaps.new(name="Window", space_type="EMPTY")
    # Cmd(oskey)+Option(alt) 조합 → IME 우회. 충돌 적은 조합 사용.
    kmi = km.keymap_items.new("blenderko.input_toggle", type="K", value="PRESS",
                              oskey=True, alt=True)
    _addon_keymaps.append((km, kmi))
    kmi = km.keymap_items.new("blenderko.input_english", type="E", value="PRESS",
                              oskey=True, alt=True)
    _addon_keymaps.append((km, kmi))


def _unregister_keymaps():
    for km, kmi in _addon_keymaps:
        try:
            km.keymap_items.remove(kmi)
        except Exception:  # noqa: BLE001
            pass
    _addon_keymaps.clear()


# ───────────────────────────────────────────────────────────────────────────
# 등록
# ───────────────────────────────────────────────────────────────────────────
_classes = (
    BLENDERKO_Prefs,
    BLENDERKO_OT_input_english,
    BLENDERKO_OT_input_korean,
    BLENDERKO_OT_input_toggle,
    BLENDERKO_OT_list_sources,
    BLENDERKO_OT_capture_current,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    _register_keymaps()

    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)

    _watch["prev_mode"] = None
    _start_timer()

    # 시작 시 영문 정렬 — register 컨텍스트 제한 → 한 틱 뒤
    def _initial():
        prefs = _get_prefs()
        if prefs and prefs.force_english_on_load:
            switch_english(prefs)
        return None
    if IS_MACOS:
        bpy.app.timers.register(_initial, first_interval=0.5)


def unregister():
    _stop_timer()
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    _unregister_keymaps()
    for cls in reversed(_classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    register()
