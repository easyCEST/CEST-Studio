# -*- coding: utf-8 -*-
"""app/dialogs/import_scan_dialog.py —— ⓪ 扫描预览表（数据 / 掩模两用）。

扫完目录后把「可能符合要求的」列成一张表，逐行勾选、改病例名、补频偏。
只做展示与回填，不读像素、不加载数据（文件头已经在扫描阶段读过）。

数据模式列：勾选 / 病例名 / S0 / Z 谱 / 频偏 / 形状 / 状态
掩模模式列：勾选 / 病例名 / 器官掩模 / SNR 掩模 / ROI_total / 状态
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout)


COLS_DATA = ('勾选', '病例名', 'S0', 'Z 谱', '频偏', '形状', '状态')
COLS_MASK = ('勾选', '病例名', '器官掩模', 'SNR 掩模', 'ROI_total', '形状', '状态')


class ScanDialog(QDialog):
    """rows: [{case, dir, s0, z, w, b0, w_spec, shape, status, ok, masks, roi, note}]"""

    def __init__(self, parent=None, rows=None, mode='数据'):
        super().__init__(parent)
        self.mode = 'mask' if str(mode).startswith('掩模') else 'data'
        self.rows = [dict(r) for r in (rows or [])]
        self.setWindowTitle('扫描结果 —— 勾选要处理的条目')
        self.resize(1080, 560)
        self._build()

    # ------------------------------------------------------------------ UI
    def _build(self):
        v = QVBoxLayout(self)
        v.setSpacing(8)

        self.head = QLabel('')
        self.head.setWordWrap(True)
        v.addWidget(self.head)

        # 全局频偏（只对缺频偏的行有意义）
        self.gbar = QHBoxLayout()
        self.gbar.setSpacing(6)
        lab = QLabel('全局频偏：')
        self.gbar.addWidget(lab)
        self.lab_spec = QLabel('（未设置）')
        self.gbar.addWidget(self.lab_spec)
        self.b_set = QPushButton('设置…')
        self.b_set.setObjectName('pick')
        self.b_set.clicked.connect(self._set_range)
        self.gbar.addWidget(self.b_set)
        self.b_apply = QPushButton('应用到缺频偏的行')
        self.b_apply.clicked.connect(self._apply_range)
        self.gbar.addWidget(self.b_apply)
        self.b_all = QPushButton('全选可跑的行')
        self.b_all.clicked.connect(lambda: self._check_all(True))
        self.gbar.addWidget(self.b_all)
        self.b_none = QPushButton('全不选')
        self.b_none.clicked.connect(lambda: self._check_all(False))
        self.gbar.addWidget(self.b_none)
        self.gbar.addStretch(1)
        v.addLayout(self.gbar)

        cols = COLS_MASK if self.mode == 'mask' else COLS_DATA
        self.tb = QTableWidget(len(self.rows), len(cols))
        self.tb.setHorizontalHeaderLabels(list(cols))
        self.tb.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tb.verticalHeader().setVisible(False)
        hh = self.tb.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.tb.setColumnWidth(1, 230)
        v.addWidget(self.tb, 1)

        bot = QHBoxLayout()
        self.lab_cnt = QLabel('')
        bot.addWidget(self.lab_cnt)
        bot.addStretch(1)
        b_ok = QPushButton('确定（用这些行）')
        b_ok.setObjectName('primary')
        b_ok.clicked.connect(self.accept)
        b_no = QPushButton('取消')
        b_no.clicked.connect(self.reject)
        bot.addWidget(b_ok)
        bot.addWidget(b_no)
        v.addLayout(bot)

        self._fill()
        self._refresh_cnt()
        if self.mode == 'mask':
            for w in (self.b_set, self.b_apply):
                w.setVisible(False)
            self.lab_spec.setVisible(False)
            lab.setVisible(False)

    def _fill(self):
        for i, r in enumerate(self.rows):
            # 勾选
            cb = QCheckBox()
            cb.setChecked(bool(r.get('ok', False)))
            cb.toggled.connect(lambda *_: self._refresh_cnt())
            host = QLabel()
            hb = QHBoxLayout(host)
            hb.setContentsMargins(6, 0, 0, 0)
            hb.addWidget(cb)
            hb.addStretch(1)
            self.tb.setCellWidget(i, 0, host)
            r['_cb'] = cb

            # 病例名（可改）
            ed = QLineEdit(str(r.get('case', '')))
            ed.textChanged.connect(lambda t, rr=r: rr.__setitem__('case', t))
            self.tb.setCellWidget(i, 1, ed)
            r['_ed'] = ed

            if self.mode == 'mask':
                vals = [r.get('organ_name', '') or '—',
                        r.get('snr_name', '') or '—',
                        r.get('roi_name', '') or '—',
                        r.get('shape', ''), r.get('status', '')]
            else:
                vals = [Path_name(r.get('s0')), Path_name(r.get('z')),
                        r.get('w_spec', '') or '（待填）',
                        r.get('shape', ''), r.get('status', '')]
            for j, t in enumerate(vals, start=2):
                it = QTableWidgetItem(str(t))
                it.setFlags(Qt.ItemFlag.ItemIsEnabled)
                if j == 6:
                    st = str(t)
                    if st.startswith('就绪') or st.startswith('齐全'):
                        it.setForeground(Qt.GlobalColor.darkGreen)
                    else:
                        it.setForeground(Qt.GlobalColor.darkYellow)
                self.tb.setItem(i, j, it)

    # --------------------------------------------------------------- 频偏
    def _set_range(self):
        from app.dialogs.range_dialog import RangeDialog
        dlg = RangeDialog(self, self.lab_spec.text() if self.lab_spec.text() != '（未设置）' else '')
        if dlg.exec():
            self.lab_spec.setText(dlg.spec())

    def _apply_range(self):
        spec = self.lab_spec.text()
        if spec in ('', '（未设置）'):
            return
        n = 0
        for r in self.rows:
            if not r.get('w_spec'):
                r['w_spec'] = spec
                r['status'] = '就绪（频偏用全局）'
                if not r.get('ok'):
                    r['ok'] = True
                    r['_cb'].setChecked(True)
                n += 1
        self._fill()
        self._refresh_cnt()

    def _check_all(self, on):
        for r in self.rows:
            if on and not r.get('ok'):
                continue
            r['_cb'].setChecked(on)
        self._refresh_cnt()

    def _refresh_cnt(self):
        n = sum(1 for r in self.rows if r['_cb'].isChecked())
        bad = sum(1 for r in self.rows if not r.get('ok', False))
        self.lab_cnt.setText(f'共 {len(self.rows)} 行，勾选 {n} 行'
                             + (f'；{bad} 行缺前置（默认不勾）' if bad else ''))
        self.head.setText(
            ('勾选要提取掩模的病例。源目录只读，输出到 <输出根>\\00_掩模\\<病例>\\'
             '（Mask.mat + <部位>ROIMask.mat + ROI_total.mat …）。')
            if self.mode == 'mask' else
            ('勾选要重建的病例。S0 与 Z 谱按变量名认（不看文件名），嵌套目录已自动配对；'
             '缺 S0 的行默认不勾。'))

    # --------------------------------------------------------------- 结果
    def picked(self) -> list:
        out = []
        for r in self.rows:
            if not r['_cb'].isChecked():
                continue
            rr = {k: v for k, v in r.items() if not k.startswith('_')}
            rr['case'] = (r['_ed'].text() or rr.get('case', '')).strip()
            out.append(rr)
        return out


def Path_name(p) -> str:
    if not p:
        return '—'
    return str(p).replace('\\', '/').split('/')[-1]
