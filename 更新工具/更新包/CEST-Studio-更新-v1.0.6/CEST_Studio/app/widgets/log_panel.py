# -*- coding: utf-8 -*-
"""app/widgets/log_panel.py —— 全局日志面板（只读，行数多了自动截断）。"""
from __future__ import annotations

from PySide6.QtWidgets import QPlainTextEdit

MAX_BLOCKS = 3000


class LogPanel(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('log')
        self.setReadOnly(True)
        self.setMaximumBlockCount(MAX_BLOCKS)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

    def append_line(self, s: str):
        self.appendPlainText(str(s))
        # 自动滚到底
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())
