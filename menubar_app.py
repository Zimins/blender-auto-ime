#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
menubar_app — ko_focus_switch 의 메뉴바(상태바) 앱 래퍼 (macOS)

더블클릭으로 쓰는 GUI 버전. 메뉴바 아이콘에서 켜기/끄기·상태·자동실행을 제어한다.
핵심 전환 로직은 ko_focus_switch.FocusWatcher 를 그대로 재사용한다.

직접 실행(개발):  pip install rumps && python3 menubar_app.py
패키징(.app):     packaging/ 의 PyInstaller spec 사용 (rumps 포함 번들)

rumps 의 NSApplication 런루프가 떠 있으므로 frontmost 변화 알림이 자연히 들어온다.
폴링은 rumps.Timer 로 하고, 전환 시 IME commit 은 FocusWatcher 가 런루프를 잠깐
펌프해서 처리한다(ko_focus_switch._pump).
"""

import os
import subprocess
import sys

try:
    import rumps
except ImportError:  # noqa: BLE001
    sys.stderr.write(
        "rumps 가 필요합니다. 개발 실행: pip install rumps\n"
        "(패키징된 .app 에는 포함되어 있습니다)\n")
    raise

import ko_focus_switch as kfs

APP_NAME = "KO Focus Switch"
MENUBAR_LABEL = "dev.blender-ko.menubar"
POLL_INTERVAL = 0.3


def _friendly_source(sid):
    """입력 소스 ID 를 사람이 읽기 쉬운 라벨로."""
    if not sid:
        return "?"
    if sid == kfs.DEFAULT_ENGLISH_ID or sid.endswith(".ABC") or ".keylayout.US" in sid:
        return "영문"
    if "2SetKorean" in sid or sid.endswith(".Korean"):
        return "한글(2벌식)"
    if "inputmethod.Korean" in sid:
        return "한글"
    return sid.rsplit(".", 1)[-1]


# ───────────────────────────────────────────────────────────────────────────
# 로그인 시 자동 실행 (LaunchAgent — .app/소스 모두 대응)
# ───────────────────────────────────────────────────────────────────────────
def _agent_plist_path():
    return os.path.expanduser("~/Library/LaunchAgents/{}.plist".format(MENUBAR_LABEL))


def _program_args():
    # 패키징된 .app 이면 sys.executable 이 곧 앱 실행파일. 소스면 python + 이 스크립트.
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, os.path.abspath(__file__)]


def autostart_enabled():
    return os.path.exists(_agent_plist_path())


def autostart_set(enable):
    import plistlib

    path = _agent_plist_path()
    uid = os.getuid()
    if enable:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {
            "Label": MENUBAR_LABEL,
            "ProgramArguments": _program_args(),
            "RunAtLoad": True,
            "KeepAlive": True,
            "ProcessType": "Interactive",
        }
        with open(path, "wb") as f:
            plistlib.dump(data, f)
        subprocess.run(["launchctl", "bootout", "gui/{}/{}".format(uid, MENUBAR_LABEL)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["launchctl", "bootstrap", "gui/{}".format(uid), path],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.run(["launchctl", "bootout", "gui/{}/{}".format(uid, MENUBAR_LABEL)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.path.exists(path):
            os.remove(path)


# ───────────────────────────────────────────────────────────────────────────
# 메뉴바 앱
# ───────────────────────────────────────────────────────────────────────────
class KoFocusApp(rumps.App):
    def __init__(self):
        super().__init__(APP_NAME, title="⌨︎", quit_button=None)
        self.enabled = True
        self._ticking = False
        self.last_event = "대기 중…"

        kfs._init_objc()
        self.watcher = kfs.FocusWatcher(on_event=self._on_switch)

        self.m_status = rumps.MenuItem("상태: 시작 중…")
        self.m_status.set_callback(None)  # 비활성(정보 표시용)
        self.m_enabled = rumps.MenuItem("활성화", callback=self.toggle_enabled)
        self.m_enabled.state = True
        self.m_autostart = rumps.MenuItem("로그인 시 자동 실행", callback=self.toggle_autostart)
        self.m_autostart.state = autostart_enabled()

        self.menu = [
            self.m_status,
            None,
            self.m_enabled,
            self.m_autostart,
            None,
            rumps.MenuItem("Blender 진입→영문 / 이탈→원래대로", callback=None),
            None,
            rumps.MenuItem("종료", callback=self.quit_app),
        ]

        # 런루프 기반 폴링 — sleep 없이 rumps.Timer 로
        self.timer = rumps.Timer(self.on_tick, POLL_INTERVAL)
        self.timer.start()

    # --- 폴링 ---
    def on_tick(self, _timer):
        if self._ticking:
            return
        self._ticking = True
        try:
            if self.enabled:
                self.watcher.tick()
            self._refresh_status()
        except Exception as ex:  # noqa: BLE001
            self.m_status.title = "상태: 오류 {!r}".format(ex)
        finally:
            self._ticking = False

    def _refresh_status(self):
        cur = kfs.tis_current_source_id()
        _bid, name = kfs.frontmost_app()
        where = "Blender" if self.watcher.in_blender else (name or "?")
        on = "켜짐" if self.enabled else "꺼짐"
        self.m_status.title = "[{}] 맨앞: {} · 입력: {}".format(on, where, _friendly_source(cur))
        self.title = "⌨︎" if self.enabled else "⌨︎⏸"

    def _on_switch(self, msg):
        self.last_event = msg

    # --- 메뉴 동작 ---
    def toggle_enabled(self, sender):
        self.enabled = not self.enabled
        sender.state = self.enabled
        if self.enabled:
            # 다시 켤 때 stale 상태로 잘못된 전환을 막기 위해 상태 리셋
            self.watcher.was_blender = None
        self._refresh_status()

    def toggle_autostart(self, sender):
        try:
            new = not autostart_enabled()
            autostart_set(new)
            sender.state = autostart_enabled()
            rumps.notification(APP_NAME, "로그인 시 자동 실행",
                               "켜짐" if sender.state else "꺼짐")
        except Exception as ex:  # noqa: BLE001
            rumps.notification(APP_NAME, "자동 실행 설정 실패", repr(ex))

    def quit_app(self, _sender):
        rumps.quit_application()


def main():
    if sys.platform != "darwin":
        sys.stderr.write("이 메뉴바 앱은 macOS 전용입니다.\n")
        return 1
    KoFocusApp().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
