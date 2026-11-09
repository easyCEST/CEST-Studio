# -*- coding: utf-8 -*-
"""app/widgets/autosize.py —— 让「自动换行的状态标签」在被裁之前自己撑够高度。

背景（实测）：面板里的 `QLabel(wordWrap=True)` 放在 `QScrollArea(widgetResizable=True)`
里时，**heightForWidth 不被采纳** → 布局给它少一行的高度 → 状态文字被截掉半行
（④ 4 行只给 3 行、② 2 行只给 1 行；`SetMinimumSize` 也救不了）。

用法：别用 `label.setText(txt)`，改成 `fit_label(label, txt)`。
"""
from __future__ import annotations

from PySide6.QtCore import Qt


def fit_label(label, text, ref_width: int = 420, extra: int = 4) -> None:
    """setText + 把高度**定死**（min=max=按参考宽度算出的高度）。

    为什么定死：QScrollArea(widgetResizable) 用「当前宽度下的最小高度」估算，而
    wordWrap 的 QLabel 会随宽度变高 → 估算值与实际需要来回打架，最后把内容压掉
    一行（②④⑧ 实测都被截）。固定参考宽度 = 左栏最小宽度下的可用宽度（约 420），
    高度就不随宽度变，估算稳定。
    """
    text = str(text)
    label.setText(text)
    fm = label.fontMetrics()
    r = fm.boundingRect(0, 0, int(ref_width), 100000,
                        int(Qt.TextFlag.TextWordWrap), text)
    h = int(r.height()) + int(extra)
    label.setMinimumHeight(h)
    label.setMaximumHeight(h)
