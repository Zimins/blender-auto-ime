#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
ko_focus_switch — 앱 포커스에 따라 입력 소스를 바꾸는 OS 레벨 데몬 (macOS)

Blender 애드온이 아니라, macOS에서 백그라운드로 상주하며 '맨 앞 앱'을 감시한다.
규칙:
    · Blender가 맨 앞으로 오면  → 진입 직전 입력기를 기억해두고 '영문'으로 전환
                                 (그래야 G/R/S/X 단축키가 동작)
    · Blender에서 빠져나가면     → 기억해둔 '진입 전 입력기'로 복원
                                 (영문이었으면 영문, 한글이었으면 한글)
    · Blender 안에서 수동으로 한글로 바꿔 텍스트를 칠 때는 건드리지 않는다
                                 (포커스 변화가 없으면 데몬은 가만히 있음)

동작 원리:
    - 맨 앞 앱: AppKit 의 NSWorkspace.frontmostApplication 을 ctypes(Obj-C 런타임)로 폴링.
      → PyObjC 등 외부 패키지 불필요.
    - 입력 소스 전환: Carbon TIS API(TISSelectInputSource) 를 ctypes 로 직접 호출.
      (blender_ko_switch.py 의 백엔드와 동일. 최신 macOS에서 미반영 시 macism 으로 폴백)

사용:
    # 포그라운드 실행(로그를 눈으로 보며 테스트)
    python3 ko_focus_switch.py run

    # 입력 소스 ID 확인(영문/한글 ID 가 기본값과 다를 때)
    python3 ko_focus_switch.py list
    python3 ko_focus_switch.py current

    # 로그인 시 자동 실행되도록 LaunchAgent 설치 / 제거
    python3 ko_focus_switch.py install
    python3 ko_focus_switch.py uninstall

옵션:
    --english <id>     영문 입력 소스 ID (기본 com.apple.keylayout.ABC)
    --backend <b>      AUTO | CTYPES | MACISM (기본 AUTO)
    --interval <sec>   포커스 폴링 주기 (기본 0.3)
    --blender-bundle <id>   Blender 번들 ID (기본 org.blender.blender + org.blenderfoundation.blender)
    --macism <path>    macism 경로 (비우면 자동 탐색)
"""

import argparse
import ctypes
import ctypes.util
import os
import shutil
import subprocess
import sys
import time

IS_MACOS = sys.platform == "darwin"

DEFAULT_ENGLISH_ID = "com.apple.keylayout.ABC"
DEFAULT_KOREAN_ID = "com.apple.inputmethod.Korean.2SetKorean"
# Blender 빌드/배포에 따라 번들 ID가 둘 중 하나다 (둘 다 기본 매칭)
DEFAULT_BLENDER_BUNDLES = ["org.blender.blender", "org.blenderfoundation.blender"]
LAUNCH_LABEL = "dev.blender-ko.focus-switch"


# ───────────────────────────────────────────────────────────────────────────
# 동적 라이브러리 로더 (dyld 공유 캐시 대응: find_library 가 None 이어도 풀패스로 로드)
# ───────────────────────────────────────────────────────────────────────────
def _load(name, *fallbacks):
    path = ctypes.util.find_library(name)
    candidates = ([path] if path else []) + list(fallbacks)
    for c in candidates:
        if not c:
            continue
        try:
            return ctypes.CDLL(c)
        except OSError:
            continue
    return None


# ───────────────────────────────────────────────────────────────────────────
# Carbon TIS (Text Input Source) — 입력 소스 읽기/전환 (blender_ko_switch 와 동일 코어)
# ───────────────────────────────────────────────────────────────────────────
_tis = None


def _init_tis():
    global _tis
    if _tis is not None:
        return _tis
    if not IS_MACOS:
        _tis = False
        return _tis
    try:
        cf = _load(
            "CoreFoundation",
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation",
        )
        carbon = _load(
            "Carbon",
            "/System/Library/Frameworks/Carbon.framework/Carbon",
        )
        if cf is None or carbon is None:
            print("[KO_FOCUS] Carbon/CoreFoundation 로드 실패", file=sys.stderr)
            _tis = False
            return _tis

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
        # 현재 스레드 런루프를 잠깐 돌려 입력 소스 전환이 실제로 반영되게 한다(IME 우회)
        cf.CFRunLoopRunInMode.argtypes = [c_void_p, ctypes.c_double, c_bool]
        cf.CFRunLoopRunInMode.restype = c_int32

        carbon.TISCreateInputSourceList.argtypes = [c_void_p, c_bool]
        carbon.TISCreateInputSourceList.restype = c_void_p
        carbon.TISCopyCurrentKeyboardInputSource.argtypes = []
        carbon.TISCopyCurrentKeyboardInputSource.restype = c_void_p
        carbon.TISGetInputSourceProperty.argtypes = [c_void_p, c_void_p]
        carbon.TISGetInputSourceProperty.restype = c_void_p
        carbon.TISSelectInputSource.argtypes = [c_void_p]
        carbon.TISSelectInputSource.restype = c_int32

        prop_id = c_void_p.in_dll(carbon, "kTISPropertyInputSourceID")
        run_mode = c_void_p.in_dll(cf, "kCFRunLoopDefaultMode")

        _tis = {
            "cf": cf,
            "carbon": carbon,
            "kTISPropertyInputSourceID": prop_id,
            "kCFRunLoopDefaultMode": run_mode,
            "kCFStringEncodingUTF8": 0x08000100,
        }
        return _tis
    except Exception as ex:  # noqa: BLE001
        print("[KO_FOCUS] TIS 초기화 예외:", ex, file=sys.stderr)
        _tis = False
        return _tis


def _cfstring_to_str(t, cfstr):
    if not cfstr:
        return None
    cf = t["cf"]
    enc = t["kCFStringEncodingUTF8"]
    ptr = cf.CFStringGetCStringPtr(cfstr, enc)
    if ptr:
        try:
            return ptr.decode("utf-8")
        except Exception:  # noqa: BLE001
            pass
    buf = ctypes.create_string_buffer(1024)
    if cf.CFStringGetCString(cfstr, buf, 1024, enc):
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
    carbon, cf = t["carbon"], t["cf"]
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
    carbon, cf = t["carbon"], t["cf"]
    arr = carbon.TISCreateInputSourceList(None, True)
    if not arr:
        return []
    ids = []
    try:
        for i in range(cf.CFArrayGetCount(arr)):
            src = cf.CFArrayGetValueAtIndex(arr, i)
            if not src:
                continue
            sid = _source_id(t, src)
            if sid:
                ids.append(sid)
    finally:
        cf.CFRelease(arr)
    seen, out = set(), []
    for s in ids:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def tis_select(source_id):
    t = _init_tis()
    if not t:
        return False, "TIS 사용 불가"
    carbon, cf = t["carbon"], t["cf"]
    arr = carbon.TISCreateInputSourceList(None, True)
    if not arr:
        return False, "TISCreateInputSourceList NULL"
    try:
        for i in range(cf.CFArrayGetCount(arr)):
            src = cf.CFArrayGetValueAtIndex(arr, i)
            if not src:
                continue
            if _source_id(t, src) == source_id:
                status = carbon.TISSelectInputSource(src)
                return (True, "ok") if status == 0 else (False, "OSStatus={}".format(status))
        return False, "입력 소스 ID 없음: {}".format(source_id)
    finally:
        cf.CFRelease(arr)


# 런루프 펌프/바운스 설정 (run/install 시 인자로 덮어씀)
_cfg = {"english": DEFAULT_ENGLISH_ID, "settle": 0.15, "retries": 4}


def _pump(seconds):
    """현재 스레드 런루프를 seconds 만큼 돌려 입력 소스 전환이 commit 되게 한다.

    IME 전환은 TISSelectInputSource 만으로는 '다른 앱으로 갔다 돌아오기 전까진' 반영이
    안 되는 경우가 있다(macOS 26 CJK 레이스). 런루프를 잠깐 돌리면 그 사이 시스템이
    전환 알림을 처리할 기회를 얻어 반영되는 경우가 있다.
    """
    t = _init_tis()
    if not t:
        time.sleep(max(0.0, seconds))
        return
    t["cf"].CFRunLoopRunInMode(t["kCFRunLoopDefaultMode"], ctypes.c_double(seconds), False)


def tis_select_robust(source_id):
    """select + 런루프 펌프 + 반영 확인. 미반영이면 '영문 경유 bounce' 로 재시도.

    - 영문(키보드 레이아웃)으로의 전환은 보통 첫 시도에 바로 반영되어 즉시 반환.
    - 한글(IME)로의 전환이 미반영이면, 영문을 한 번 거쳤다가 다시 target 을 선택하는
      '이중 전환'을 settle/retries 만큼 반복해 commit 을 유도한다.
    """
    ok, msg = tis_select(source_id)
    if not ok:
        return False, msg
    _pump(_cfg["settle"])
    if tis_current_source_id() == source_id:
        return True, "ok"

    bounce = _cfg["english"]
    if bounce == source_id:
        # target 이 영문인데도 미반영인 희귀 케이스: 그냥 재시도만
        for i in range(_cfg["retries"]):
            tis_select(source_id)
            _pump(_cfg["settle"])
            if tis_current_source_id() == source_id:
                return True, "ok(retry{})".format(i + 1)
        return False, "미반영(robust)"

    for i in range(_cfg["retries"]):
        tis_select(bounce)        # 영문으로 한 번 빠졌다가
        _pump(0.05)
        tis_select(source_id)     # 다시 target 으로 — '이중 전환'
        _pump(_cfg["settle"])
        if tis_current_source_id() == source_id:
            return True, "ok(bounce{})".format(i + 1)
    return False, "미반영(robust {}회)".format(_cfg["retries"])


# ───────────────────────────────────────────────────────────────────────────
# macism CLI 백엔드 (선택)
# ───────────────────────────────────────────────────────────────────────────
def _find_macism(explicit=None):
    if explicit and os.path.exists(explicit):
        return explicit
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


def _is_ime(source_id):
    return "inputmethod" in (source_id or "")


def switch_to(source_id, backend="AUTO", macism_path=None):
    """입력 소스 전환. (성공여부, 사용백엔드/메시지)

    macOS 26 주의: TISSelectInputSource 는 영문(키보드 레이아웃) 전환은 견고하지만,
    한글(IME) 전환은 status 0 을 반환하고도 실제로는 안 바뀔 수 있다. 그래서
    (1) IME 대상은 AUTO 에서 macism 우선, (2) ctypes 전환 후 현재 소스를 다시 읽어
    '반영 확인', 미반영이면 macism 으로 폴백.
    """
    if not IS_MACOS:
        return False, "macOS 전용"
    errors = []

    def try_macism():
        path = _find_macism(macism_path)
        if not path:
            errors.append("macism 미발견")
            return False
        ok, msg = macism_select(path, source_id)
        if ok:
            return True
        errors.append(msg)
        return False

    def try_ctypes():
        ok, msg = tis_select_robust(source_id)
        if not ok:
            errors.append("ctypes(" + msg + ")")
            return False
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


# ───────────────────────────────────────────────────────────────────────────
# 맨 앞 앱 조회 — Obj-C 런타임을 ctypes 로 직접 호출 (PyObjC 불필요)
# ───────────────────────────────────────────────────────────────────────────
_objc = None


def _init_objc():
    global _objc
    if _objc is not None:
        return _objc
    if not IS_MACOS:
        _objc = False
        return _objc
    try:
        objc = _load("objc", "/usr/lib/libobjc.A.dylib", "/usr/lib/libobjc.dylib")
        # NSWorkspace 심볼이 등록되도록 AppKit 로드 (파일이 디스크에 없어도 dyld 캐시에서 로드됨)
        _load("AppKit", "/System/Library/Frameworks/AppKit.framework/AppKit")
        if objc is None:
            print("[KO_FOCUS] libobjc 로드 실패", file=sys.stderr)
            _objc = False
            return _objc
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        _objc = objc
        return _objc
    except Exception as ex:  # noqa: BLE001
        print("[KO_FOCUS] objc 초기화 예외:", ex, file=sys.stderr)
        _objc = False
        return _objc


def _msg(receiver, sel_name, restype=ctypes.c_void_p, argtypes=None, args=()):
    """[receiver selName:args] 를 objc_msgSend 로 호출."""
    objc = _objc
    if not receiver:
        return None
    sel = objc.sel_registerName(sel_name.encode("utf-8"))
    objc.objc_msgSend.restype = restype
    objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + list(argtypes or [])
    return objc.objc_msgSend(receiver, sel, *args)


def _nsstring(ptr):
    if not ptr:
        return None
    c = _msg(ptr, "UTF8String", restype=ctypes.c_char_p)
    if not c:
        return None
    try:
        return c.decode("utf-8")
    except Exception:  # noqa: BLE001
        return None


def frontmost_app():
    """(bundle_id, name) — 둘 다 실패 시 (None, None)."""
    objc = _init_objc()
    if not objc:
        return None, None
    try:
        NSWorkspace = objc.objc_getClass(b"NSWorkspace")
        if not NSWorkspace:
            return None, None
        ws = _msg(NSWorkspace, "sharedWorkspace")
        app = _msg(ws, "frontmostApplication")
        if not app:
            return None, None
        bid = _nsstring(_msg(app, "bundleIdentifier"))
        name = _nsstring(_msg(app, "localizedName"))
        return bid, name
    except Exception as ex:  # noqa: BLE001
        print("[KO_FOCUS] frontmost 조회 예외:", ex, file=sys.stderr)
        return None, None


# ───────────────────────────────────────────────────────────────────────────
# 메인 루프
# ───────────────────────────────────────────────────────────────────────────
def _log(msg):
    print("[KO_FOCUS] {}".format(msg), flush=True)


def _is_blender(bid, name, bundles, name_match):
    if bid and bid in bundles:
        return True
    if name and name.lower() == name_match:
        return True
    return False


class FocusWatcher:
    """맨 앞 앱을 감시해 Blender 진입/이탈 시 입력 소스를 전환하는 핵심 상태기계.

    플랫폼 무관 로직 + macOS 프리미티브(frontmost_app/tis_*/switch_to)에 의존한다.
    Windows 지원 시엔 그 프리미티브들만 백엔드로 갈아끼우면 된다(여기가 플랫폼 seam).

    tick() 을 주기적으로 호출: CLI 는 while 루프에서, 메뉴바 앱은 rumps.Timer 에서.
    on_event(msg) 콜백을 주면 전환이 일어날 때마다 메시지를 받아 UI 에 띄울 수 있다.
    """

    def __init__(self, english=DEFAULT_ENGLISH_ID, backend="AUTO", macism=None,
                 bundles=None, name_match="blender", settle=0.15, retries=4,
                 on_event=None):
        self.english = english
        self.backend = backend
        self.macism = macism
        self.bundles = set(bundles or DEFAULT_BLENDER_BUNDLES)
        self.name_match = (name_match or "blender").lower()
        self.on_event = on_event
        self.prev_source = None
        self.was_blender = None
        self.in_blender = False
        _cfg["english"] = english
        _cfg["settle"] = settle
        _cfg["retries"] = retries

    def _emit(self, msg):
        _log(msg)
        if self.on_event:
            try:
                self.on_event(msg)
            except Exception:  # noqa: BLE001
                pass
        return msg

    def tick(self):
        """한 번 폴링. 전환/이벤트가 있으면 메시지 문자열 반환, 없으면 None."""
        bid, name = frontmost_app()
        now = _is_blender(bid, name, self.bundles, self.name_match)
        self.in_blender = now
        msg = None
        if self.was_blender is None:
            if now:  # 시작 시 이미 Blender 가 앞이면 진입 처리
                self.prev_source = tis_current_source_id()
                msg = self._enter()
        elif now and not self.was_blender:
            self.prev_source = tis_current_source_id()
            msg = self._enter()
        elif self.was_blender and not now:
            msg = self._leave()
        self.was_blender = now
        return msg

    def _enter(self):
        if self.prev_source == self.english:
            return self._emit("→ Blender 진입: 이미 영문({}) 유지".format(self.prev_source))
        ok, via = switch_to(self.english, self.backend, self.macism)
        return self._emit("→ Blender 진입: 영문 전환 {} ({}) via={} [진입전={}]".format(
            "OK" if ok else "FAIL", self.english, via, self.prev_source))

    def _leave(self):
        if not self.prev_source:
            return self._emit("← Blender 이탈: 복원할 진입전 소스 없음 (그대로 둠)")
        cur = tis_current_source_id()
        if cur == self.prev_source:
            return self._emit("← Blender 이탈: 이미 {} 유지".format(self.prev_source))
        ok, via = switch_to(self.prev_source, self.backend, self.macism)
        return self._emit("← Blender 이탈: 복원 {} ({}) via={} [블렌더에서={}]".format(
            "OK" if ok else "FAIL", self.prev_source, via, cur))


def make_watcher(args, on_event=None):
    """argparse 네임스페이스로 FocusWatcher 생성 (CLI/메뉴바 공용)."""
    return FocusWatcher(
        english=args.english, backend=args.backend, macism=args.macism,
        bundles=set(args.blender_bundle), name_match=args.blender_name,
        settle=args.settle, retries=args.retries, on_event=on_event)


def run(args):
    if not IS_MACOS:
        _log("macOS 전용입니다.")
        return 1
    if not _init_objc():
        _log("AppKit/objc 초기화 실패 — 맨 앞 앱을 감지할 수 없습니다.")
        return 1

    interval = max(0.05, args.interval)
    w = make_watcher(args)
    _log("시작 — Blender={} 영문={} backend={} 주기={}s settle={}s retries={}".format(
        sorted(w.bundles), args.english, args.backend, interval, args.settle, args.retries))

    while True:
        try:
            w.tick()
        except KeyboardInterrupt:
            _log("종료 (Ctrl+C)")
            return 0
        except Exception as ex:  # noqa: BLE001
            _log("틱 오류: {!r}".format(ex))
        try:
            _pump(interval)  # sleep 이 아니라 런루프를 돌려야 frontmost 변화 알림이 들어온다
        except KeyboardInterrupt:
            _log("종료 (Ctrl+C)")
            return 0


# ───────────────────────────────────────────────────────────────────────────
# 보조 명령
# ───────────────────────────────────────────────────────────────────────────
def cmd_list(_args):
    ids = tis_list_source_ids()
    cur = tis_current_source_id()
    print("현재: {}".format(cur))
    print("--- 설치된 입력 소스 {}개 ---".format(len(ids)))
    for s in ids:
        print(("* " if s == cur else "  ") + s)
    return 0


def cmd_current(_args):
    print(tis_current_source_id())
    return 0


def cmd_watch(_args):
    """맨 앞 앱이 바뀔 때마다 (bundle_id, name) 출력 — Blender 감지/번들ID 확인용."""
    if not _init_objc():
        print("objc 초기화 실패 — 맨 앞 앱을 못 읽음")
        return 1
    print("맨 앞 앱 변화를 출력합니다. Blender로 갔다가 터미널로 돌아와 보세요. (Ctrl+C 종료)")
    print("--- 시작 ---", flush=True)
    last = object()
    while True:
        try:
            bid, name = frontmost_app()
            cur = (bid, name)
            if cur != last:
                print("frontmost  bundle={!r}  name={!r}".format(bid, name), flush=True)
                last = cur
            _pump(0.3)  # sleep 이 아니라 런루프를 돌려야 frontmost 변화 알림이 들어온다
        except KeyboardInterrupt:
            print("--- 종료 ---")
            return 0
        except Exception as ex:  # noqa: BLE001
            print("오류:", repr(ex), flush=True)
            time.sleep(0.3)


def cmd_selftest(args):
    """'한글 IME 로의 전환'이 robust 로직으로 실제 commit 되는지 측정. 끝나면 원래로 복원.

    현재 상태와 무관하게 시험할 수 있도록: (필요하면 먼저 영문으로 이동 →) target IME 로
    robust 전환 → 현재 소스를 다시 읽어 실제 반영됐는지 확인 → 원래 입력기로 복원.
    """
    _cfg["english"] = args.english
    _cfg["settle"] = args.settle
    _cfg["retries"] = args.retries
    target = args.korean

    orig = tis_current_source_id()
    print("현재 입력 소스:", orig)
    print("복원 시험 대상(IME):", target)
    if not orig:
        print("현재 입력 소스를 못 읽음 — 중단")
        return 1
    if target not in tis_list_source_ids():
        print("⚠ 대상 IME 가 설치 목록에 없음:", target)
        print("  'python3 ko_focus_switch.py list' 로 한글 ID 확인 후 --korean 으로 지정하세요.")
        return 1

    # 'IME 로 들어가는' 전환을 시험하려면 영문에서 출발해야 의미가 있다
    if orig == target:
        print("→ 현재가 대상과 같아 먼저 영문({})으로 이동...".format(args.english))
        switch_to(args.english, "CTYPES", None)
        _pump(args.settle)
    print("출발 소스(영문이어야 정확):", tis_current_source_id())

    print("← {} 로 robust 전환(into IME)...".format(target))
    okr, msg = tis_select_robust(target)
    cur = tis_current_source_id()
    committed = (cur == target)
    print("   {} ({}) 현재={}".format("OK" if committed else "FAIL", msg, cur))

    # 원래 입력기로 복원 (orig 가 IME 면 robust 포함된 AUTO 로)
    if tis_current_source_id() != orig:
        switch_to(orig, "AUTO", None)
        _pump(args.settle)
    fin = tis_current_source_id()
    print("최종: {}  (원래 {} 복원: {})".format(
        fin, orig, "성공" if fin == orig else "실패 — 한/영 키로 직접 복구"))
    print("==> 판정:", "robust 우회 성공 — 한글 IME commit 됨 (macism 불필요)" if committed
          else "robust 우회 실패 — 한글 IME 미반영 → macism 권장")
    return 0 if committed else 2


# ───────────────────────────────────────────────────────────────────────────
# LaunchAgent 설치/제거
# ───────────────────────────────────────────────────────────────────────────
def _plist_path():
    return os.path.expanduser("~/Library/LaunchAgents/{}.plist".format(LAUNCH_LABEL))


def _log_path():
    return os.path.expanduser("~/Library/Logs/{}.log".format(LAUNCH_LABEL))


def cmd_install(args):
    import plistlib

    plist_path = _plist_path()
    os.makedirs(os.path.dirname(plist_path), exist_ok=True)
    os.makedirs(os.path.dirname(_log_path()), exist_ok=True)

    prog = [sys.executable, os.path.abspath(__file__), "run",
            "--english", args.english,
            "--backend", args.backend,
            "--interval", str(args.interval),
            "--settle", str(args.settle),
            "--retries", str(args.retries),
            "--blender-name", args.blender_name]
    for b in args.blender_bundle:
        prog += ["--blender-bundle", b]
    if args.macism:
        prog += ["--macism", args.macism]

    data = {
        "Label": LAUNCH_LABEL,
        "ProgramArguments": prog,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Interactive",
        "StandardOutPath": _log_path(),
        "StandardErrorPath": _log_path(),
    }
    with open(plist_path, "wb") as f:
        plistlib.dump(data, f)

    uid = os.getuid()
    # 이미 떠 있으면 내렸다가 다시 부트스트랩
    subprocess.run(["launchctl", "bootout", "gui/{}/{}".format(uid, LAUNCH_LABEL)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    r = subprocess.run(["launchctl", "bootstrap", "gui/{}".format(uid), plist_path],
                       capture_output=True, text=True)
    print("plist 작성:", plist_path)
    print("로그:", _log_path())
    if r.returncode == 0:
        print("LaunchAgent 로드 완료 — 지금부터 상주합니다. (로그: tail -f '{}')".format(_log_path()))
    else:
        print("자동 로드 실패(rc={}). 수동으로:".format(r.returncode))
        print("  launchctl bootstrap gui/{} '{}'".format(uid, plist_path))
        if r.stderr.strip():
            print("  stderr:", r.stderr.strip())
    return 0


def cmd_uninstall(_args):
    plist_path = _plist_path()
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", "gui/{}/{}".format(uid, LAUNCH_LABEL)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if os.path.exists(plist_path):
        os.remove(plist_path)
        print("제거 완료:", plist_path)
    else:
        print("설치된 plist 없음:", plist_path)
    return 0


# ───────────────────────────────────────────────────────────────────────────
# 인자 파싱
# ───────────────────────────────────────────────────────────────────────────
def _add_common(p):
    p.add_argument("--english", default=DEFAULT_ENGLISH_ID)
    p.add_argument("--backend", default="AUTO", choices=["AUTO", "CTYPES", "MACISM"])
    p.add_argument("--interval", type=float, default=0.3)
    p.add_argument("--blender-bundle", action="append", default=None,
                   help="Blender 번들 ID (여러 번 지정 가능)")
    p.add_argument("--blender-name", default="Blender",
                   help="번들 ID 매칭 실패 시 사용할 앱 이름")
    p.add_argument("--macism", default=None)
    p.add_argument("--settle", type=float, default=0.15,
                   help="IME 전환 후 런루프 펌프/반영 대기(초). macOS 26은 0.15 권장")
    p.add_argument("--retries", type=int, default=4,
                   help="미반영 시 영문 경유 bounce 재시도 횟수")


def _finalize(args):
    if not args.blender_bundle:
        args.blender_bundle = list(DEFAULT_BLENDER_BUNDLES)
    return args


def main(argv):
    parser = argparse.ArgumentParser(prog="ko_focus_switch", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd")

    p_run = sub.add_parser("run", help="포그라운드 실행")
    _add_common(p_run)
    p_run.set_defaults(func=run)

    p_inst = sub.add_parser("install", help="LaunchAgent 설치(로그인 시 자동 실행)")
    _add_common(p_inst)
    p_inst.set_defaults(func=cmd_install)

    p_unin = sub.add_parser("uninstall", help="LaunchAgent 제거")
    p_unin.set_defaults(func=cmd_uninstall)

    p_list = sub.add_parser("list", help="설치된 입력 소스 ID 목록")
    p_list.set_defaults(func=cmd_list)

    p_cur = sub.add_parser("current", help="현재 입력 소스 ID 출력")
    p_cur.set_defaults(func=cmd_current)

    p_watch = sub.add_parser("watch", help="맨 앞 앱 변화 출력(Blender 번들ID 확인용)")
    p_watch.set_defaults(func=cmd_watch)

    p_test = sub.add_parser("selftest", help="robust IME 복원이 macism 없이 되는지 측정(끝나면 원복)")
    _add_common(p_test)
    p_test.add_argument("--korean", default=DEFAULT_KOREAN_ID,
                        help="commit 시험 대상 한글 IME ID (기본 2벌식)")
    p_test.set_defaults(func=cmd_selftest)

    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        parser.print_help()
        return 0
    if hasattr(args, "blender_bundle"):
        _finalize(args)
    return args.func(args) or 0


def cli_entry():
    """console_scripts 진입점 (pyproject [project.scripts])."""
    return main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
