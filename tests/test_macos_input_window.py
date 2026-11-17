"""全屏绘制层常驻穿透，小窗口接收输入并保持原交互坐标。"""
import os
import subprocess
import sys
import textwrap

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QMimeData, QObject, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QContextMenuEvent, QCursor, QDragEnterEvent, QDropEvent

from pet import overlay_shell as overlay_mod, platform_passthrough as passthrough
from tests.test_overlay_hosts_multiscreen import (
    FakeScreen, _cleanup, _hittable, _install_screens, _make_shell, _move, _press,
    _release, _right_press,
)


@pytest.fixture
def surface(tmp_path, monkeypatch):
    # 只替换平台选择和系统光标；窗口、事件分发与宠物交互均使用真实 Qt。
    monkeypatch.setattr(passthrough, "_use_macos_input_window", lambda _w: True, raising=False)
    monkeypatch.setattr(overlay_mod, "_IS_MACOS", True)
    screen = FakeScreen((0, 0, 1512, 982), (0, 33, 1512, 896))
    _install_screens(monkeypatch, screen)
    shell = _make_shell(tmp_path, screen, {"drag_physics": False})
    shell.sprite.set_pos(QPointF(500, 400))
    _hittable(shell.sprite)
    cursor = shell.sprite.rect().center()
    monkeypatch.setattr(QCursor, "pos", staticmethod(lambda: QPoint(cursor)))
    shell.overlay.show()
    controller = shell.overlay._input_controller
    controller.refresh()
    proxy = getattr(controller, "_input_window", None)
    try:
        assert proxy is not None, "macOS 需要独立的小输入窗口"
        yield shell, controller, proxy, cursor
    finally:
        _cleanup(shell)


def test_drawing_surface_stays_transparent_during_click_and_drag(surface):
    shell, controller, proxy, cursor = surface
    overlay = shell.overlay
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert proxy.isVisible()
    assert proxy.geometry() == shell.sprite.rect().translated(overlay.geometry().topLeft())
    assert proxy.width() < overlay.width()
    local = QPointF(proxy.mapFromGlobal(cursor))
    QCoreApplication.sendEvent(proxy, _press(local, QPointF(cursor)))
    end = QPointF(cursor) + QPointF(80, 30)
    QCoreApplication.sendEvent(proxy, _move(QPointF(proxy.mapFromGlobal(end.toPoint())), end))
    assert shell.sprite.dragging
    controller.refresh()
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    QCoreApplication.sendEvent(proxy, _release(QPointF(proxy.mapFromGlobal(end.toPoint())), end))
    assert not shell.sprite.dragging
    assert overlay._mouse_grab is None
    assert shell.sprite.rect().center().x() == pytest.approx(cursor.x() + 80, abs=2)


def test_manual_passthrough_hide_and_resume_close_the_input_surface(surface):
    shell, controller, proxy, _cursor = surface
    shell.overlay.mouse_through = True
    controller.refresh()
    assert not proxy.isVisible()
    shell.overlay.mouse_through = False
    controller.refresh()
    assert proxy.isVisible()
    shell.overlay.hide()
    assert not proxy.isVisible()
    shell.overlay.show()
    controller.refresh()
    assert proxy.isVisible()
    assert shell.overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


def test_slingshot_and_rotated_hit_region_use_the_original_overlay(surface, monkeypatch):
    shell, controller, proxy, cursor = surface
    local = QPointF(proxy.mapFromGlobal(cursor))
    QCoreApplication.sendEvent(proxy, _press(local, QPointF(cursor)))
    moved = QPointF(cursor) + QPointF(80, 0)
    QCoreApplication.sendEvent(proxy, _move(QPointF(proxy.mapFromGlobal(moved.toPoint())), moved))
    QCoreApplication.sendEvent(proxy, _right_press(QPointF(proxy.mapFromGlobal(moved.toPoint())), moved))
    assert shell.overlay.slingshot.aiming
    QCoreApplication.sendEvent(proxy, _release(QPointF(proxy.mapFromGlobal(moved.toPoint())), moved))
    assert not shell.overlay.slingshot.aiming
    assert shell.overlay._mouse_grab is None
    _hittable(shell.sprite)
    shell.sprite.set_throw_rotation(45)
    point = shell.sprite.rect().center()
    monkeypatch.setattr(QCursor, "pos", staticmethod(lambda: QPoint(point)))
    controller.refresh()
    assert proxy.geometry() == shell.sprite.paint_bounds().translated(shell.overlay.geometry().topLeft())


def test_proxy_drag_crosses_screens_and_releases_into_the_target_host(tmp_path, monkeypatch):
    monkeypatch.setattr(passthrough, "_use_macos_input_window", lambda _w: True)
    monkeypatch.setattr(overlay_mod, "_IS_MACOS", True)
    a = FakeScreen((0, 0, 1512, 982), (0, 33, 1512, 896), name="A")
    b = FakeScreen((1512, 0, 1512, 982), (1512, 33, 1512, 896), name="B")
    _install_screens(monkeypatch, a, b)
    shell = _make_shell(tmp_path, a, {"allow_cross_screen": True, "drag_physics": False})
    shell.sprite.set_pos(QPointF(1100, 600))
    _hittable(shell.sprite)
    cursor = shell.sprite.rect().center()
    monkeypatch.setattr(QCursor, "pos", staticmethod(lambda: QPoint(cursor)))
    try:
        for host in shell.hosts():
            host.overlay.show()
        controller = shell.overlay._input_controller
        controller.refresh()
        proxy = controller._input_window
        QCoreApplication.sendEvent(proxy, _press(QPointF(proxy.mapFromGlobal(cursor)), QPointF(cursor)))
        for _ in range(20):
            cursor += QPoint(40, 0)
            QCoreApplication.sendEvent(proxy, _move(QPointF(proxy.mapFromGlobal(cursor)), QPointF(cursor)))
            shell.driver.tick_sim(1 / 60)
            shell.driver.advance_overlays(1 / 60)
            controller.refresh()
        QCoreApplication.sendEvent(proxy, _release(QPointF(proxy.mapFromGlobal(cursor)), QPointF(cursor)))
        assert shell.host_for_sprite(shell.sprite).screen is b
        assert not shell.sprite.dragging
        controller.refresh()
        assert not proxy.isVisible()
        target = shell.host_for_sprite(shell.sprite).overlay
        _hittable(shell.sprite)
        target._input_controller.refresh()
        assert target._input_controller._input_window.isVisible(), (
            cursor, target.mapFromGlobal(cursor), shell.sprite.rect(),
            target.sprite_at(target.mapFromGlobal(cursor)))
        assert all(host.overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                   for host in shell.hosts())
    finally:
        _cleanup(shell)


def test_context_menu_and_file_drop_keep_overlay_coordinates(surface, tmp_path):
    shell, _controller, proxy, cursor = surface
    received = []

    class Events(QObject):
        def eventFilter(self, obj, event):
            if event.type() in (QEvent.Type.ContextMenu, QEvent.Type.DragEnter, QEvent.Type.Drop):
                pos = event.pos() if event.type() == QEvent.Type.ContextMenu else event.position().toPoint()
                received.append((event.type(), pos))
                event.accept()
                return True  # 菜单模态循环和文件写入不属于输入转发测试。
            return False

    events = Events(shell.overlay)
    shell.overlay.installEventFilter(events)
    class Feeding:
        def handle_drag_enter(self, event):
            received.append((event.type(), event.position().toPoint()))
            event.accept()

        def handle_drop(self, event):
            received.append((event.type(), event.position().toPoint()))
            event.accept()

    shell.overlay._feeding = Feeding()
    local = proxy.mapFromGlobal(cursor)
    QCoreApplication.sendEvent(proxy, QContextMenuEvent(QContextMenuEvent.Reason.Mouse, local, cursor))
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(tmp_path / "feed.txt"))])
    enter = QDragEnterEvent(local, Qt.DropAction.CopyAction, mime,
                           Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QCoreApplication.sendEvent(proxy, enter)
    drop = QDropEvent(QPointF(local), Qt.DropAction.CopyAction, mime,
                      Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QCoreApplication.sendEvent(proxy, drop)
    assert [kind for kind, pos in received] == [QEvent.Type.ContextMenu, QEvent.Type.DragEnter, QEvent.Type.Drop]
    assert all(pos == shell.overlay.mapFromGlobal(cursor) for kind, pos in received)
    assert enter.isAccepted() and drop.isAccepted()


@pytest.mark.skipif(sys.platform != "darwin", reason="需要真实 Cocoa")
def test_native_input_window_never_claims_the_screen_edge():
    code = textwrap.dedent('''
        import ctypes
        import shiboken6
        from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QPoint, QPointF, QRectF, QTimer, Qt
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QApplication
        from pet.overlay_window import OverlayWindow
        from pet.pet_sprite import PetSprite
        app = QApplication([])
        from tests.test_sprite_menu_facade import RichLibrary
        window = OverlayWindow()
        sprite = PetSprite(RichLibrary(), scale=0.5)
        window.add_sprite(sprite)
        sprite.set_bounds(QRectF(app.primaryScreen().geometry()))
        sprite.set_pos(QPointF(1100, 650))
        sprite.bind_clip('idle1')
        point = sprite.rect().center()
        QCursor.pos = staticmethod(lambda: point)
        window.show()
        for _ in range(10): app.processEvents()
        controller = window._input_controller
        controller.refresh()
        proxy = getattr(controller, '_input_window', None)
        assert proxy is not None, '缺少独立输入窗口'
        objc = ctypes.CDLL('/usr/lib/libobjc.A.dylib')
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.objc_getClass.restype = ctypes.c_void_p
        msg = objc.objc_msgSend
        def native(widget):
            msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            msg.restype = ctypes.c_void_p
            return msg(int(widget.winId()), objc.sel_registerName(b'window'))
        def number(widget):
            ptr = native(widget)
            msg.restype = ctypes.c_long
            return msg(ptr, objc.sel_registerName(b'windowNumber'))
        class Point(ctypes.Structure):
            _fields_ = [('x', ctypes.c_double), ('y', ctypes.c_double)]
        def hit(x, y):
            msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p, Point, ctypes.c_long]
            msg.restype = ctypes.c_long
            return msg(objc.objc_getClass(b'NSWindow'),
                       objc.sel_registerName(b'windowNumberAtPoint:belowWindowWithWindowNumber:'),
                       Point(x, app.primaryScreen().geometry().height()-y), 0)
        def wait(condition):
            # 窗口显示/隐藏提交给 WindowServer 是异步的，按原生命中结果同步。
            if condition(): return
            loop, timer, deadline = QEventLoop(), QTimer(), QTimer()
            timer.timeout.connect(lambda: loop.quit() if condition() else None)
            deadline.setSingleShot(True)
            deadline.timeout.connect(loop.quit)
            timer.start(10)
            deadline.start(5000)
            loop.exec()
            timer.stop(); deadline.stop()
            assert condition(), 'WindowServer 命中状态未收敛'
        try:
            drawing_number, input_number = number(window), number(proxy)
            assert hit(3, 490) not in (drawing_number, input_number)
            wait(lambda: hit(point.x(), point.y()) == input_number)
            ptr = native(window)
            msg.restype = ctypes.c_bool
            assert msg(ptr, objc.sel_registerName(b'ignoresMouseEvents'))
            window.mouse_through = True
            controller.refresh()
            assert not proxy.isVisible()
            wait(lambda: hit(point.x(), point.y()) not in (drawing_number, input_number))
            window.mouse_through = False
            for on_top in (False, True):
                window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on_top)
                window.show()
                for _ in range(10): app.processEvents()
                controller.refresh()
                ptr = native(window)
                msg.restype = ctypes.c_bool
                assert msg(ptr, objc.sel_registerName(b'ignoresMouseEvents'))
                input_ptr = native(proxy)
                msg.restype = ctypes.c_long
                assert msg(input_ptr, objc.sel_registerName(b'level')) == msg(ptr, objc.sel_registerName(b'level'))
            input_number, drawing_number = number(proxy), number(window)
            wait(lambda: hit(point.x(), point.y()) == input_number)
            assert hit(3, 490) not in (input_number, drawing_number)
            window.close()
            assert not proxy.isVisible()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            assert not shiboken6.isValid(proxy)
            print('native input split: screen edge free, pet interactive, drawing always ignored', flush=True)
        finally:
            if shiboken6.isValid(window): window.close()
    ''')
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            timeout=30, env={**os.environ, "QT_QPA_PLATFORM": "cocoa"})
    assert result.returncode == 0, result.stdout + result.stderr
    print(result.stdout, end="")
