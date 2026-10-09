# -*- coding: utf-8 -*-
"""app/widgets/drop_zone.py —— 「输入总目录」拖拽区：拖进来就识别，点一下也能选。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFileDialog, QFrame, QLabel, QVBoxLayout


class DropZone(QFrame):
    path_selected = Signal(str)          # 用户给出一个目录（拖拽或选择）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumHeight(84)
        self.setObjectName('dropZone')

        self.lab = QLabel('把「输入总目录」拖到这里，或点此选择')
        self.lab.setObjectName('dropText')
        self.lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay = QVBoxLayout(self)
        lay.addWidget(self.lab)
        lay.setContentsMargins(10, 10, 10, 10)

        self.setStyleSheet('')

    def mousePressEvent(self, e):
        d = QFileDialog.getExistingDirectory(self, '选择输入总目录')
        if d:
            self.path_selected.emit(d)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = e.mimeData().urls()
        if urls:
            p = urls[0].toLocalFile()
            if p:
                self.path_selected.emit(p)

    def set_text(self, s: str):
        self.lab.setText(str(s))
