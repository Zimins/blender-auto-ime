# SPDX-License-Identifier: MIT
"""
blender_ko_diag — 한글 IME 단축키 진단 애드온 (macOS)

목적:
    한글 IME가 켜진 상태에서 키를 누를 때, Blender의 파이썬 이벤트 레이어가
    "실제로 무엇을 받는지"를 측정한다. 이 결과가 메인 해법(blender_ko_switch)의
    동작 전제를 확정한다.

확인하려는 핵심 질문 (Blender 소스 분석 기반):
    1) 한글 IME ON + 뷰포트에서 물리 G 키 → event.type 이 'G' 로 도착하는가?
       (도착하면: 단축키는 이미 동작해야 함. 문제는 'stale IME' 한정)
    2) '단축키가 안 먹는' 상태에서 그 키가 파이썬 modal() 에 도달이라도 하는가?
       (도착 안 하면: 자모-리매핑 방식은 원천 불가 → OS 입력소스 전환이 정답)
    3) event.unicode 에 자모(예: 'ㅎ')가 실리는가, 아니면 라틴 'g' 인가, 빈 문자열인가?

사용법:
    1) 이 파일을 Blender 5.x 에 설치 (Edit > Preferences > Add-ons > Install from Disk
       또는 .py 파일을 Blender 창에 드래그 앤 드롭) 후 체크박스 ON.
    2) 3D 뷰포트 우측 사이드바(N) > 'KO Diag' 탭 열기.
    3) [진단 시작] 클릭.
    4) macOS 입력기를 '2벌식 한글'로 전환.
    5) 뷰포트(아무 것도 선택/편집 안 한 상태)에서 G, R, S, X 를 눌러본다.
    6) 그다음 오브젝트를 F2로 이름변경 → 아무것도 안 치고 Esc → 뷰포트 클릭 → 다시 G.
    7) [진단 정지] 클릭 후, 로그를 확인:
       - Blender 내부: Text Editor 에서 'KO_DIAG_LOG' 텍스트 블록
       - (터미널에서 Blender 실행 시) 표준출력에도 같은 내용 출력
    8) 로그를 그대로 복사해서 알려주면, 메인 애드온 동작을 당신 환경에 맞게 확정한다.

주의:
    - 진단 모달은 임시 사용용이다. 켜두면 Blender의 자동저장/undo push가
      억제될 수 있으니(모달 오퍼레이터 일반 특성), 측정 후 반드시 [진단 정지].
    - Esc 한 번으로도 진단을 멈출 수 있게 했다(측정 중 Esc는 로그에만 기록되고
      두 번째 Esc 또는 정지 버튼으로 종료).
"""

bl_info = {
    "name": "KO IME Diagnostic (한글 단축키 진단)",
    "author": "blender-ko",
    "version": (0, 1, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar (N) > KO Diag",
    "description": "한글 IME 상태에서 Blender가 실제로 받는 키 이벤트를 측정한다 (macOS 진단용)",
    "category": "Development",
}

import bpy
import time

LOG_TEXT_NAME = "KO_DIAG_LOG"

# 로깅에서 제외할(노이즈) 이벤트 타입
_SKIP_TYPES = {
    "MOUSEMOVE", "INBETWEEN_MOUSEMOVE", "TIMER", "TIMER_REPORT",
    "NONE", "MOUSEROTATE", "MOUSEPAN", "MOUSEZOOM",
    "TRACKPADPAN", "TRACKPADZOOM",
}


def _get_log_text():
    txt = bpy.data.texts.get(LOG_TEXT_NAME)
    if txt is None:
        txt = bpy.data.texts.new(LOG_TEXT_NAME)
    return txt


def _codepoints(s):
    """문자열 s의 각 문자를 U+XXXX 로 표기."""
    if not s:
        return "(empty)"
    return " ".join("U+{:04X}".format(ord(c)) for c in s)


def _format_event(event, mode):
    """단일 이벤트를 한 줄 로그로 포맷."""
    uni = event.unicode
    asc = event.ascii
    mods = []
    if event.shift:
        mods.append("Shift")
    if event.ctrl:
        mods.append("Ctrl")
    if event.alt:
        mods.append("Alt")
    if event.oskey:
        mods.append("Cmd")
    mod_s = "+".join(mods) if mods else "-"
    return (
        "type={type:<14} value={value:<8} "
        "unicode={uni!r:<8} [{ucp}]  ascii={asc!r:<5}  mods={mods:<18} mode={mode}"
    ).format(
        type=event.type,
        value=event.value,
        uni=uni,
        ucp=_codepoints(uni),
        asc=asc,
        mods=mod_s,
        mode=mode,
    )


class BLENDERKO_OT_diagnostic(bpy.types.Operator):
    bl_idname = "blenderko.diagnostic"
    bl_label = "KO IME 진단 시작/정지"
    bl_description = "한글 IME 상태에서 도착하는 키 이벤트를 측정한다"
    bl_options = {"REGISTER"}

    _esc_seen_at = 0.0

    def invoke(self, context, event):
        wm = context.window_manager
        if wm.blenderko_diag_running:
            # 이미 실행 중이면 토글로 정지 신호
            wm.blenderko_diag_running = False
            self.report({"INFO"}, "KO 진단 정지 요청됨")
            return {"FINISHED"}

        wm.blenderko_diag_running = True
        self._esc_seen_at = 0.0

        # 로그 초기화
        txt = _get_log_text()
        txt.clear()
        header = (
            "=== KO IME Diagnostic 시작 ===\n"
            "macOS 입력기를 '2벌식 한글'로 바꾼 뒤, 뷰포트에서 G/R/S/X 등을 눌러보세요.\n"
            "그다음 F2 이름변경→아무것도 안 치고 Esc→뷰포트 클릭→다시 G 도 테스트.\n"
            "정지: 사이드바 [진단 정지] 또는 Esc 두 번.\n"
            "각 줄: type(물리키 식별자) / unicode(실제 문자) / mods(수정키) / mode\n"
            "----------------------------------------------------------------\n"
        )
        txt.write(header)
        print("[KO_DIAG]", header.replace("\n", "\n[KO_DIAG] "))

        wm.modal_handler_add(self)
        # 화면 갱신 유도
        for area in context.screen.areas:
            area.tag_redraw()
        self.report({"INFO"}, "KO 진단 시작 — 키를 눌러 측정")
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        wm = context.window_manager

        # 외부(사이드바 버튼)에서 정지 신호
        if not wm.blenderko_diag_running:
            self._finish(context, "정지 버튼")
            return {"FINISHED"}

        et = event.type

        # Esc 두 번으로 종료 (한 번째는 로그만 남김)
        if et == "ESC" and event.value == "PRESS":
            now = time.time()
            line = _format_event(event, getattr(context, "mode", "?"))
            self._emit(line + "   <-- ESC")
            if now - self._esc_seen_at < 1.5:
                wm.blenderko_diag_running = False
                self._finish(context, "Esc x2")
                return {"FINISHED"}
            self._esc_seen_at = now
            return {"PASS_THROUGH"}

        # 노이즈 제외하고 모두 기록
        if et not in _SKIP_TYPES:
            mode = getattr(context, "mode", "?")
            line = _format_event(event, mode)
            self._emit(line)

        # 절대 이벤트를 소비하지 않는다 → Blender 정상 동작 유지
        return {"PASS_THROUGH"}

    def _emit(self, line):
        try:
            _get_log_text().write(line + "\n")
        except Exception as ex:  # noqa: BLE001
            print("[KO_DIAG] (text write 실패)", ex)
        print("[KO_DIAG]", line)

    def _finish(self, context, why):
        footer = "----------------------------------------------------------------\n" \
                 "=== KO IME Diagnostic 종료 ({}) ===\n".format(why)
        try:
            _get_log_text().write(footer)
        except Exception:  # noqa: BLE001
            pass
        print("[KO_DIAG]", footer)
        try:
            for area in context.screen.areas:
                area.tag_redraw()
        except Exception:  # noqa: BLE001
            pass
        self.report({"INFO"}, "KO 진단 종료 — Text Editor의 'KO_DIAG_LOG' 확인")


class BLENDERKO_PT_diag_panel(bpy.types.Panel):
    bl_label = "KO IME 진단"
    bl_idname = "BLENDERKO_PT_diag_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "KO Diag"

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        running = wm.blenderko_diag_running

        col = layout.column(align=True)
        if running:
            col.alert = True
            col.operator("blenderko.diagnostic", text="■ 진단 정지", icon="SNAP_FACE")
            col.label(text="측정 중… 키를 눌러보세요", icon="REC")
        else:
            col.operator("blenderko.diagnostic", text="▶ 진단 시작", icon="PLAY")

        box = layout.box()
        box.label(text="측정 순서", icon="INFO")
        col = box.column(align=True)
        col.label(text="1) macOS 입력기 → 2벌식 한글")
        col.label(text="2) 뷰포트에서 G/R/S/X 누르기")
        col.label(text="3) F2 이름변경→Esc→뷰포트→G")
        col.label(text="4) 정지 후 로그 확인")

        layout.separator()
        layout.label(text="로그 위치:")
        col = layout.column(align=True)
        col.label(text="Text Editor → 'KO_DIAG_LOG'", icon="TEXT")
        op = layout.operator("blenderko.diag_open_log", text="로그 텍스트 열기/생성", icon="WORDWRAP_ON")


class BLENDERKO_OT_diag_open_log(bpy.types.Operator):
    bl_idname = "blenderko.diag_open_log"
    bl_label = "KO 진단 로그 열기"
    bl_description = "KO_DIAG_LOG 텍스트 블록을 생성/보장한다 (Text Editor에서 선택해 확인)"

    def execute(self, context):
        _get_log_text()
        self.report({"INFO"}, "Text Editor 상단 드롭다운에서 'KO_DIAG_LOG' 선택")
        return {"FINISHED"}


_classes = (
    BLENDERKO_OT_diagnostic,
    BLENDERKO_OT_diag_open_log,
    BLENDERKO_PT_diag_panel,
)


def register():
    bpy.types.WindowManager.blenderko_diag_running = bpy.props.BoolProperty(
        name="KO Diag Running", default=False
    )
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:  # noqa: BLE001
            pass
    try:
        del bpy.types.WindowManager.blenderko_diag_running
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    register()
