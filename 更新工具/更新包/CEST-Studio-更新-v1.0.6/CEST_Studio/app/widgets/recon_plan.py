# -*- coding: utf-8 -*-
"""app/widgets/recon_plan.py —— ① 页的「序列与频率」面板。

只负责显示：病例选择 / 每个序列的 帧数·层数 / 维度(3D|4D) / 频率状态 + 提示。
逻辑（怎么匹配、需要几个频率、拦不拦）全在 app/modules/m1_recon.py 里。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QGroupBox,
                               QHBoxLayout, QLabel, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

GREEN = QBrush(QColor('#15803d'))
RED = QBrush(QColor('#b91c1c'))
GREY = QBrush(QColor('#6b7280'))


class ReconPlanPanel(QGroupBox):
    def __init__(self, parent=None):
        super().__init__('序列与频率　（3D 不需要频率；4D 必须先给定并确认频率）', parent)
        self.on_case_change = None      # callable(case)
        self.on_edit_freq = None        # callable(plan)
        self.on_rescan = None           # callable()
        self.on_browse = None           # callable(plan)
        self._plan = None
        self._syncing = False

        v = QVBoxLayout(self)
        v.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(QLabel('病例：'))
        self.case_box = QComboBox()
        self.case_box.setMinimumWidth(120)
        self.case_box.currentTextChanged.connect(self._case_changed)
        top.addWidget(self.case_box)
        self.scan_lab = QLabel('')
        self.scan_lab.setObjectName('muted')
        top.addWidget(self.scan_lab)
        top.addStretch(1)
        self.btn_browse = QPushButton('浏览 / 选择序列…')
        self.btn_browse.clicked.connect(self._browse)
        self.btn_freq = QPushButton('频率表…')
        self.btn_freq.setObjectName('primary')
        self.btn_freq.clicked.connect(self._edit_freq)
        b = QPushButton('重新分析')
        b.clicked.connect(lambda: self.on_rescan and self.on_rescan())
        top.addWidget(self.btn_browse)
        top.addWidget(self.btn_freq)
        top.addWidget(b)
        v.addLayout(top)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['序列', '文件夹', '帧数/层数', '维度', '频率'])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self.table)

        self.hint = QLabel('')
        self.hint.setWordWrap(True)
        v.addWidget(self.hint)

    # ---------------------------------------------------------------- 交互
    def _case_changed(self, c):
        if self._syncing or not c:
            return
        if self.on_case_change:
            self.on_case_change(c)

    def _edit_freq(self):
        if self.on_edit_freq and self._plan:
            self.on_edit_freq(self._plan)

    def _browse(self):
        if self.on_browse and self._plan:
            self.on_browse(self._plan)

    # ---------------------------------------------------------------- 渲染
    def render(self, plan):
        self._plan = plan
        rows = plan.get('rows') or []

        self._syncing = True
        self.case_box.clear()
        self.case_box.addItems([c for c in (plan.get('cases') or []) if c])
        self.case_box.setCurrentText(plan.get('case', ''))
        self._syncing = False
        self.case_box.setEnabled(plan.get('kind') == 'dicom_uih')
        self.scan_lab.setText('（'+('手动指定' if plan.get('manual') else '自动匹配')+
                              ('·已读 DICOM 标签' if plan.get('scanned') else '')+'）')

        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            cells = [r.get('tag', ''), r.get('name', ''), r.get('size_txt', ''),
                     r.get('ndim', ''), r.get('freq_txt', '')]
            for j, txt in enumerate(cells):
                it = QTableWidgetItem(str(txt))
                if j == 3:
                    it.setForeground(GREEN if txt == '3D' else (RED if txt == '4D' else GREY))
                if j == 4:
                    it.setForeground(GREY if '不需要' in txt
                                     else (GREEN if txt.startswith('√') else RED))
                self.table.setItem(i, j, it)
        self.table.resizeColumnsToContents()
        self.table.setFixedHeight(max(2, len(rows)) * 26 + 30)

        self.btn_freq.setEnabled(bool(plan.get('has_4d')))
        self.btn_freq.setToolTip('' if plan.get('has_4d')
                                 else '本病例没有 4D 序列 → 不需要频率表')
        self.hint.setText('\n'.join(plan.get('messages') or []))
        self.hint.setStyleSheet('color:#b91c1c;' if plan.get('blocking') else 'color:#6b7280;')
