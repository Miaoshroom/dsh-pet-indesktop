"""macOS 合成窗覆盖 Dock 区域，并禁止用户移动或拉伸窗口。"""
import os
import subprocess
import sys
import textwrap

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPointF, QRect, QRectF

import pet.overlay_shell as shell_mod
from tests.test_overlay_hosts_multiscreen import (
    FakeScreen, _cleanup, _install_screens, _make_shell,
)


@pytest.mark.parametrize("macos", [True, False])
@pytest.mark.parametrize("available", [
    (0, 30, 1512, 900), (0, 30, 1452, 952), (60, 30, 1452, 952),
])
def test_window_and_pet_share_screen_bounds(tmp_path, monkeypatch, macos, available):
    monkeypatch.setattr(shell_mod, "_IS_MACOS", macos)
    screen = FakeScreen((0, 0, 1512, 982), available)
    _install_screens(monkeypatch, screen)
    shell = _make_shell(tmp_path, screen, {"allow_cross_screen": False})
    try:
        expected = screen.geometry() if macos else screen.availableGeometry()
        assert shell.overlay.geometry() == expected
        shell.sprite.set_pos(QPointF(10000, 10000))
        body = shell.sprite.body_rect()
        assert body.x() + body.width() == expected.width()
        assert body.y() + body.height() == expected.height()
        # Dock 变化不应缩小 macOS 桌宠的活动区域。
        screen._avail = QRect(80, 30, 1432, 952)
        screen.availableGeometryChanged.emit(screen.availableGeometry())
        expected = screen.geometry() if macos else screen.availableGeometry()
        assert shell.overlay.geometry() == expected
        # 显示器几何变化时，锁定的窗口仍可由程序调整尺寸。
        screen._geo = QRect(-1280, 0, 1280, 800)
        screen._avail = QRect(-1280, 30, 1280, 710)
        screen.geometryChanged.emit(screen.geometry())
        expected = screen.geometry() if macos else screen.availableGeometry()
        assert shell.overlay.geometry() == expected
        assert shell.hosts()[0].bounds == QRect(0, 0, expected.width(), expected.height())
    finally:
        _cleanup(shell)


@pytest.mark.skipif(sys.platform != "darwin", reason="requires real Cocoa")
def test_native_overlay_is_full_screen_and_locked_after_show_and_rebuild():
    code = textwrap.dedent('''
        import ctypes
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtWidgets import QApplication
        from pet.overlay_window import OverlayWindow
        app = QApplication([])
        window = OverlayWindow()
        screen = app.primaryScreen()
        objc = ctypes.CDLL('/usr/lib/libobjc.A.dylib')
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        send = objc.objc_msgSend
        try:
            for on_top in (True, False, True):
                window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on_top)
                window.show()
                for _ in range(10):
                    app.processEvents()
                assert window.geometry() == screen.geometry(), (window.geometry(), screen.geometry())
                assert window.windowHandle().frameGeometry() == screen.geometry()
                assert window.minimumSize() == window.maximumSize() == screen.size()
                # 输入穿透变化也会让 Cocoa 重写原生窗口样式。
                window._press_global = QPoint(10, 10)
                window._input_controller.refresh()
                window._press_global = None
                window._input_controller.refresh()
                send.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
                send.restype = ctypes.c_void_p
                native = send(int(window.winId()), objc.sel_registerName(b'window'))
                send.restype = ctypes.c_bool
                assert not send(native, objc.sel_registerName(b'isMovable'))
                assert not send(native, objc.sel_registerName(b'isMovableByWindowBackground'))
                send.restype = ctypes.c_ulong
                assert not send(native, objc.sel_registerName(b'styleMask')) & 8
                before = window.geometry()
                window.resize(500, 500)
                app.processEvents()
                assert window.geometry() == before
                print('native locked:', on_top, window.geometry().getRect(), flush=True)
        finally:
            window.close()
    ''')
    env = {**os.environ, "QT_QPA_PLATFORM": "cocoa"}
    result = subprocess.run([sys.executable, "-c", code], env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    print(result.stdout, end="")
