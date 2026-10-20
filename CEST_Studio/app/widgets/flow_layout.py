# -*- coding: utf-8 -*-
"""FlowLayout —— 会自动折行的横向布局（Qt 官方 flow layout 示例改写成 PySide6）。

用途：右栏「数据链」一排 stage 按钮。原先用 QHBoxLayout，一排 9 颗按钮的最小宽度
把整个右栏的最小宽度顶到 ~900 px，默认窗口（1480）里左右分隔条几乎没有可动余量，
看起来像「只有最大化才能拖动分隔条」。换成 FlowLayout 后，宽度不够就自动折行，
行布局的最小宽度 = 最宽的一颗按钮，右栏最小宽度大幅下降。
"""
from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QSizePolicy, QWidget


class FlowLayout(QLayout):
    def __init__(self, parent=None, margin=0, hspacing=-1, vspacing=-1):
        super().__init__(parent)
        self._items = []
        self._hspace = hspacing
        self._vspace = vspacing
        if margin >= 0:
            self.setContentsMargins(margin, margin, margin, margin)

    # ---- 基本接口 ----
    def addItem(self, item):        # noqa: N802
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):        # noqa: N802
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):        # noqa: N802
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self):    # noqa: N802
        return True

    def heightForWidth(self, width):  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):    # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):             # noqa: N802
        return self.minimumSize()

    def minimumSize(self):          # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    # ---- 折行算法 ----
    def _do_layout(self, rect, test_only):
        m = self.contentsMargins()
        eff = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y = eff.x(), eff.y()
        line_h = 0
        for item in self._items:
            hint = item.sizeHint()
            space_x = self._hspace if self._hspace >= 0 else 6
            space_y = self._vspace if self._vspace >= 0 else 2
            next_x = x + hint.width() + space_x
            if next_x - space_x > eff.right() and line_h > 0:
                x = eff.x()
                y = y + line_h + space_y
                next_x = x + hint.width() + space_x
                line_h = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y() + m.bottom()


class FlowWidget(QWidget):
    """自带 FlowLayout 的容器，按当前宽度把 minimumHeight 调成折行后所需高度。

    只靠 sizePolicy().setHeightForWidth(True) 在某些嵌套 VBox 里不够（父层仍用旧高度，
    折行后的按钮被裁掉），所以在 resizeEvent 里直接把最小高度顶上去。
    用法：w = FlowWidget(hspacing=4, vspacing=2)  → 往 w.flow() 里 addWidget。
    """

    def __init__(self, parent=None, hspacing=6, vspacing=2):
        super().__init__(parent)
        self._flow = FlowLayout(self, hspacing=hspacing, vspacing=vspacing)
        pol = self.sizePolicy()
        pol.setHeightForWidth(True)
        pol.setVerticalPolicy(QSizePolicy.Policy.Minimum)
        pol.setHorizontalPolicy(QSizePolicy.Policy.Preferred)
        self.setSizePolicy(pol)

    def flow(self):
        return self._flow

    def hasHeightForWidth(self):    # noqa: N802
        return True

    def heightForWidth(self, w):    # noqa: N802
        return self._flow.heightForWidth(w)

    def resizeEvent(self, ev):      # noqa: N802
        super().resizeEvent(ev)
        self.updateGeometry()
