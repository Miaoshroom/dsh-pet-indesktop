"""macOS 小输入窗：全屏合成窗只绘制，原交互逻辑仍由合成窗处理。"""
from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import (
    QContextMenuEvent, QCursor, QDragEnterEvent, QDragMoveEvent, QDropEvent,
    QMouseEvent, QWheelEvent,
)
from PySide6.QtWidgets import QWidget

from .platform_mac import (
    _keep_macos_tool_window_visible, _mac_lock_overlay_window,
    _mac_no_window_shadow_flag, _mac_order_input_window, _mac_set_hides_on_deactivate,
    _mac_set_window_all_spaces,
)


class MacOSInputWindow(QWidget):
    """每张合成窗至多一张小输入窗，仅在命中宠物或持有鼠标抓取时显示。"""

    def __init__(self, overlay) -> None:
        super().__init__(overlay, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowDoesNotAcceptFocus
                         | _mac_no_window_shadow_flag())
        self._overlay = overlay
        self._on_top = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        _keep_macos_tool_window_visible(self)

    def sync(self, through: bool) -> None:
        """复用原逐像素判定；抓取期间保留输入窗，松手后再按光标收口。"""
        overlay = self._overlay
        grab = overlay._mouse_grab
        if (not overlay.isVisible() or overlay.mouse_through
                or (through and grab is None)):
            self.hide()
            return
        sprite = grab or overlay.sprite_at(overlay.mapFromGlobal(QCursor.pos()))
        if sprite is None:
            self.hide()
            return
        rect = QRect(sprite.paint_bounds()).translated(overlay.geometry().topLeft())
        rect = rect.intersected(overlay.geometry())
        if rect.isEmpty():
            # 拖出本屏时 Qt 仍将抓取事件送回原窗口，不移动它到别屏或关闭抓取。
            if grab is None:
                self.hide()
            return
        on_top = bool(overlay.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        if on_top != self._on_top:
            self._on_top = on_top
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on_top)
        if self.size() != rect.size():
            self.setFixedSize(rect.size())
        if self.geometry() != rect:
            self.setGeometry(rect)
        if not self.isVisible():
            self.show()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        _mac_set_hides_on_deactivate(int(self.winId()))
        _mac_set_window_all_spaces(int(self.winId()))
        _mac_lock_overlay_window(int(self.winId()))
        _mac_order_input_window(int(self.winId()), int(self._overlay.winId()))

    def event(self, event) -> bool:
        """将位置换回原合成窗坐标，交给既有点击、拖拽、弹弓、菜单与投喂入口。"""
        kind = event.type()
        mouse = (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
                 QEvent.Type.MouseButtonDblClick, QEvent.Type.MouseMove)
        drops = (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop)
        if kind not in (*mouse, *drops, QEvent.Type.ContextMenu, QEvent.Type.Wheel,
                        QEvent.Type.DragLeave):
            return super().event(event)
        overlay = self._overlay
        if kind in mouse:
            global_pos = event.globalPosition()
            local = global_pos - QPointF(overlay.mapToGlobal(QPoint()))
            forwarded = QMouseEvent(kind, local, local, global_pos, event.button(),
                                    event.buttons(), event.modifiers(), event.pointingDevice())
            forwarded.setTimestamp(event.timestamp())
        elif kind == QEvent.Type.ContextMenu:
            forwarded = QContextMenuEvent(event.reason(), overlay.mapFromGlobal(event.globalPos()),
                                          event.globalPos(), event.modifiers())
        elif kind == QEvent.Type.Wheel:
            local = event.globalPosition() - QPointF(overlay.mapToGlobal(QPoint()))
            forwarded = QWheelEvent(local, event.globalPosition(), event.pixelDelta(),
                                    event.angleDelta(), event.buttons(), event.modifiers(),
                                    event.phase(), event.inverted(), event.source(), event.pointingDevice())
        elif kind == QEvent.Type.DragLeave:
            forwarded = QEvent(kind)
        else:
            local = (event.position() + QPointF(self.mapToGlobal(QPoint()))
                     - QPointF(overlay.mapToGlobal(QPoint())))
            cls = {QEvent.Type.DragEnter: QDragEnterEvent,
                   QEvent.Type.DragMove: QDragMoveEvent, QEvent.Type.Drop: QDropEvent}[kind]
            # 进入/移动使用 QPoint 以兼容项目支持的 Qt 6.5；Drop 保留小数坐标。
            forwarded = cls(local if kind == QEvent.Type.Drop else local.toPoint(),
                            event.possibleActions(), event.mimeData(),
                            event.buttons(), event.modifiers())
        forwarded.setAccepted(False)
        if kind in drops or kind == QEvent.Type.DragLeave:
            # QApplication 会按落点重选拖放接收窗，重发给父窗会再次选中本窗。
            # 直接调用原入口，继续复用投喂命中、接受动作和写入逻辑。
            handler = {QEvent.Type.DragEnter: overlay.dragEnterEvent,
                       QEvent.Type.DragMove: overlay.dragMoveEvent,
                       QEvent.Type.Drop: overlay.dropEvent,
                       QEvent.Type.DragLeave: overlay.dragLeaveEvent}[kind]
            handler(forwarded)
        else:
            QCoreApplication.sendEvent(overlay, forwarded)
        if kind in drops:
            event.setDropAction(forwarded.dropAction())
        event.setAccepted(forwarded.isAccepted())
        return True
