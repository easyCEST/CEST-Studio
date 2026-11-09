# -*- coding: utf-8 -*-
"""app/dialogs/range_dialog.py —— 频偏“起始 / 步长 / 终点”输入框。

只干一件事：把三个数字变成一个频率序列，回写成一行的 `起点:步长:终点`
（与 MATLAB 冒号写法一致，也和 detect.freq_doc.expand_range 完全对齐）。

★ 为什么不做成直接在参数行里敲冒号：频偏一旦写错，所有峰的 ppm 定位整体偏掉，
  必须让用户看着「起始 / 步长 / 终点 / 共几个点」四个量都点一遍确认。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout,
                               QLabel, QPushButton, QVBoxLayout)

MAX_POINTS = 20000          # 和 detect.freq_doc.MAX_RANGE_LEN 一个量级，防手滑


class RangeDialog(QDialog):
    def __init__(self, parent=None, text: str = ''):
        super().__init__(parent)
        self.setWindowTitle('频偏：起始 / 步长 / 终点')
        self.setMinimumWidth(420)
        a, st, b = self._parse(text)
        self._build(a, st, b)

    # ---------------------------------------------------------------- 解析
    @staticmethod
    def _parse(text: str):
        """`-12:0.1:12` → (-12.0, 0.1, 12.0)；解析不了就用 -12/0.1/12 打底。"""
        t = str(text or '').strip().replace('：', ':').replace(' ', '')
        try:
            a, st, b = [float(x) for x in t.split(':')[:3]]
            if st > 0:
                return a, st, b
        except Exception:                                        # noqa: BLE001
            pass
        return -12.0, 0.1, 12.0

    # ------------------------------------------------------------------ UI
    def _build(self, a, st, b):
        v = QVBoxLayout(self)
        v.setSpacing(8)

        head = QLabel('填频偏序列的三个量，下面会实时显示一共几个点、首尾是多少。')
        head.setWordWrap(True)
        v.addWidget(head)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight
                               | Qt.AlignmentFlag.AlignVCenter)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(9)

        def spin(val, lo, hi, step):
            s = QDoubleSpinBox()
            s.setDecimals(4)
            s.setRange(lo, hi)
            s.setSingleStep(step)
            s.setValue(val)
            s.setMinimumWidth(140)
            return s

        self.sp_start = spin(a, -1000.0, 1000.0, 0.1)
        self.sp_step = spin(st, 0.0001, 100.0, 0.01)
        self.sp_end = spin(b, -1000.0, 1000.0, 0.1)
        form.addRow('起始（ppm）', self.sp_start)
        form.addRow('步长（ppm）', self.sp_step)
        form.addRow('终点（ppm）', self.sp_end)
        v.addLayout(form)

        self.lab = QLabel('')
        self.lab.setWordWrap(True)
        self.lab.setObjectName('checkLabel')
        v.addWidget(self.lab)

        bot = QHBoxLayout()
        bot.addStretch(1)
        self.btn_ok = QPushButton('确定')
        self.btn_ok.setObjectName('primary')
        self.btn_ok.clicked.connect(self.accept)
        b_no = QPushButton('取消')
        b_no.clicked.connect(self.reject)
        bot.addWidget(self.btn_ok)
        bot.addWidget(b_no)
        v.addLayout(bot)

        for s in (self.sp_start, self.sp_step, self.sp_end):
            s.valueChanged.connect(self._refresh)
        self._refresh()

    # ---------------------------------------------------------------- 数值
    def values(self) -> list:
        a = float(self.sp_start.value())
        st = float(self.sp_step.value())
        b = float(self.sp_end.value())
        if st <= 0:
            return []
        n = int(round((b - a) / st)) + 1
        if n < 1 or n > MAX_POINTS:
            return []
        return [round(a + i * st, 10) for i in range(n)]

    def spec(self) -> str:
        """回写成 `起点:步长:终点`（只保留有效数字，避免 -12.0000 这种噪音）。"""
        return '{}:{}:{}'.format(f'{self.sp_start.value():g}',
                                 f'{self.sp_step.value():g}',
                                 f'{self.sp_end.value():g}')

    def _refresh(self):
        vals = self.values()
        n = len(vals)
        if n == 0:
            self.lab.setText('⚠ 步长必须 > 0，且终点不能小于起点；点数也不许超过 '
                             f'{MAX_POINTS}。')
            self.lab.setStyleSheet('color:#b91c1c;')
            self.btn_ok.setEnabled(False)
            return
        head = ', '.join(f'{x:g}' for x in vals[:4])
        tail = '' if n <= 4 else f' … {vals[-1]:g}'
        self.lab.setText(f'共 <b>{n}</b> 个点：{head}{tail}')
        self.lab.setStyleSheet('color:#166534;')
        self.btn_ok.setEnabled(True)
