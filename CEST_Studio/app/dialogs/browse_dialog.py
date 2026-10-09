# -*- coding: utf-8 -*-
"""app/dialogs/browse_dialog.py —— 「目录预览」：看文件夹结构 + 勾选序列。

就三件事：
  1. 打开 = 输入总目录的第一级文件夹（= 病例）；**双击进入**，有「← 上一级」；
  2. 进到里面列出含 .dcm 的文件夹：`名称 / 帧数 / 标签 / 编号 / 维度`，**勾选**要用的；
  3. 点【补齐同组】自动把「同前缀 + 编号相邻」的 S0/Ssat/B0Ma 勾上；
     缺件会明确告诉你缺哪个，并问"只重建已有的吗"。

返回给 ①：{'case','case_dir','prefix','seqs':{tag:路径},'missing':[...]}
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QHeaderView,
                               QLabel, QMessageBox, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from detect import series
from detect.series import DEFAULT_TAGS as TAGS, parse_name

GREY = QBrush(QColor('#9aa4b2'))
GREEN = QBrush(QColor('#15803d'))
RED = QBrush(QColor('#b91c1c'))


class BrowseDialog(QDialog):
    def __init__(self, parent=None, data_root='', case=None, seq_base=None,
                 slice_max=None, sel_tags=()):
        super().__init__(parent)
        self.setWindowTitle('目录预览 —— 选择序列')
        self.resize(820, 600)
        self.data_root = Path(data_root)
        self.seq_base = seq_base or ''
        self.slice_max = slice_max
        self.cur = self.data_root
        self.rows = []
        self.sel = None
        self._sel_tags = set(sel_tags)
        self._filling = False
        self._build()
        self._go(self.data_root)

    # ------------------------------------------------------------------ UI
    def _build(self):
        v = QVBoxLayout(self)
        v.setSpacing(6)

        top = QHBoxLayout()
        self.bread = QLabel('')
        self.bread.setObjectName('srcLabel')
        top.addWidget(self.bread, 1)
        b_up = QPushButton('← 上一级')
        b_up.clicked.connect(self._up)
        b_rf = QPushButton('刷新')
        b_rf.clicked.connect(lambda: self._go(self.cur))
        top.addWidget(b_up)
        top.addWidget(b_rf)
        v.addLayout(top)

        self.table = QTableWidget()
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setAlternatingRowColors(True)
        self.table.cellDoubleClicked.connect(self._on_dbl)
        self.table.itemChanged.connect(self._on_check)
        v.addWidget(self.table, 1)

        self.res_lab = QLabel('')
        self.res_lab.setWordWrap(True)
        self.res_lab.setObjectName('srcLabel')
        v.addWidget(self.res_lab)

        btns = QHBoxLayout()
        btns.setSpacing(6)
        for text, fn, primary in (('补齐同组', self._complete_group, False),
                                  ('分析维度', self._analyze_dims, False),
                                  ('清空勾选', self._clear, False),
                                  ('确定', self._accept, True),
                                  ('取消', self.reject, False)):
            b = QPushButton(text)
            if primary:
                b.setObjectName('primary')
            b.clicked.connect(fn)
            btns.addWidget(b)
        btns.addStretch(1)
        v.addLayout(btns)

    # ------------------------------------------------------------- 导航
    def _go(self, d: Path):
        if not d.is_dir():
            d = d.parent
        self.cur = d
        self._populate()
        try:
            rel = d.resolve().relative_to(self.data_root.resolve())
            self.bread.setText(' / '.join(rel.parts) or '（输入总目录）')
        except ValueError:
            self.bread.setText(str(d))

    def _up(self):
        if self.cur.resolve() != self.data_root.resolve():
            self._go(self.cur.parent)

    def _on_dbl(self, row, col):
        r = self.rows[row]
        if r.get('n_dir'):
            self._go(Path(r['path']))

    # ------------------------------------------------------------- 填充
    def _populate(self):
        self._filling = True
        self.rows = []
        infos = []
        try:
            children = sorted((e for e in self.cur.iterdir() if e.is_dir()),
                              key=lambda p: p.name)
        except OSError:
            children = []
        for e in children:
            n_dcm, n_dir = 0, 0
            try:
                for x in e.iterdir():
                    if x.is_dir():
                        n_dir += 1
                    elif x.suffix.lower() == '.dcm':
                        n_dcm += 1
            except OSError:
                pass
            infos.append(dict(name=e.name, path=str(e), n_dcm=n_dcm, n_dir=n_dir))

        seq_level = any(i['n_dcm'] for i in infos)
        if seq_level:
            self.table.setColumnCount(6)
            self.table.setHorizontalHeaderLabels(
                ['勾选', '名称', '帧数', '标签', '编号', '维度'])
            self.table.setRowCount(len(infos))
            for i, info in enumerate(infos):
                p = parse_name(info['name'])
                pre, tag, num = (p if p else ('', '', -1))
                known = tag in TAGS
                chk = QTableWidgetItem('')
                chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                chk.setCheckState(Qt.CheckState.Checked if (
                    known and tag in self._sel_tags) else Qt.CheckState.Unchecked)
                self.table.setItem(i, 0, chk)
                self.table.setItem(i, 1, QTableWidgetItem(info['name']))
                self.table.setItem(i, 2, QTableWidgetItem(str(info['n_dcm'])))
                it3 = QTableWidgetItem(tag if known else '其它')
                if not known:
                    it3.setForeground(GREY)
                    it3.setToolTip('不认识的命名 → 勾选后按【单序列】重建，只出一个 3D')
                self.table.setItem(i, 3, it3)
                self.table.setItem(i, 4, QTableWidgetItem(str(num) if num != -1 else '—'))
                it5 = QTableWidgetItem('—')
                it5.setForeground(GREY)
                self.table.setItem(i, 5, it5)
                self.rows.append(dict(name=info['name'], path=info['path'],
                                      n_dcm=info['n_dcm'], n_dir=info['n_dir'],
                                      prefix=pre, tag=tag, num=num, known=known,
                                      dim=None))
        else:
            self.table.setColumnCount(3)
            self.table.setHorizontalHeaderLabels(['名称', '子文件夹数', '说明'])
            self.table.setRowCount(len(infos))
            for i, info in enumerate(infos):
                self.table.setItem(i, 0, QTableWidgetItem(info['name']))
                self.table.setItem(i, 1, QTableWidgetItem(str(info['n_dir'])))
                self.table.setItem(i, 2, QTableWidgetItem('双击进入'))
                self.rows.append(dict(name=info['name'], path=info['path'],
                                      n_dcm=0, n_dir=info['n_dir'], prefix='',
                                      tag='', num=-1, known=False, dim=None))
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self.table.resizeColumnsToContents()
        self._filling = False
        if seq_level:
            self._reanalyze()
        else:
            self.res_lab.setText('双击文件夹进去（第一级是病例）；进去后勾选要用的序列。')

    # ------------------------------------------------------------- 选择
    def _checked(self):
        out = []
        for i, r in enumerate(self.rows):
            it = self.table.item(i, 0)
            if it is not None and it.checkState() == Qt.CheckState.Checked:
                out.append(r)
        return out

    def _on_check(self, item):
        if self._filling or item.column() != 0:
            return
        self._reanalyze()

    def _clear(self):
        self._filling = True
        for i, r in enumerate(self.rows):
            it = self.table.item(i, 0)
            if it is not None and r['known']:
                it.setCheckState(Qt.CheckState.Unchecked)
        self._filling = False
        self._reanalyze()

    # ------------------------------------------------------------- 分析
    def _anchor_prefix(self, ck):
        """用**已勾选**的行决定看哪个前缀；没勾选时退回 seq_base。"""
        ps = sorted({r['prefix'] for r in ck if r['prefix']})
        if not ps:
            return self.seq_base
        return self.seq_base if self.seq_base in ps else ps[0]

    def _combo_of(self, prefix):
        nums = {}
        for r in self.rows:
            if r['known'] and r['prefix'] == prefix:
                nums.setdefault(r['tag'], []).append(r['num'])
        return series._combos(nums, TAGS)

    def _reanalyze(self):
        ck = self._checked()
        if not ck:
            self.res_lab.setText('勾选一个或多个序列文件夹；不确定就点【补齐同组】自动找齐。')
            return
        ck_known = [r for r in ck if r['known']]
        ck_other = [r for r in ck if not r['known']]
        if not ck_known:
            lines = ['将按【单序列】重建（只出一个 3D）：'
                     + '、'.join(r['name'] for r in ck_other),
                     '不需要频率表；输出 <病例>\\<文件夹名>.mat（zdata 形状 (X, Y, 层)）。']
            if len(ck_other) > 1:
                lines.append('（勾了多个，会各建一份 3D）')
            self.res_lab.setText('\n'.join(lines))
            return
        prefix = self._anchor_prefix(ck_known)
        combos, missing = self._combo_of(prefix)
        lines = []
        if ck_other:
            lines.append('另按【单序列】重建 3D：'
                         + '、'.join(r['name'] for r in ck_other))
        if combos:
            ns = combos[0]
            alls = [f'{t}_{n}' for t, n in zip(TAGS, ns)]
            sel = {(r['tag'], r['num']) for r in ck_known if r['prefix'] == prefix}
            lines.append(f'同前缀「{prefix}」编号相邻组合：{alls}')
            if all((t, n) in sel for t, n in zip(TAGS, ns)):
                lines.append('√ 正好是完整一组 → 可以重建。')
            else:
                lines.append('还差：' + '、'.join(
                    f'{t}_{n}' for t, n in zip(TAGS, ns) if (t, n) not in sel)
                    + '（点【补齐同组】自动勾上）')
            if len(combos) > 1:
                lines.append('还有别的候选组合：'
                             + '、'.join(str(list(c)) for c in combos[1:4]))
        else:
            lines.append(f'前缀「{prefix}」下凑不齐「编号相邻」的 S0/Ssat/B0Ma。')
            if missing:
                lines.append('缺：' + '、'.join(missing))
            lines.append('可以只重建勾选的这几个（缺 S0 无法归一化、缺 Ssat 没有频率维）。')
        n_other = len([r for r in self.rows if not r['known']])
        if n_other:
            lines.append(f'另有 {n_other} 个文件夹不是 S0/Ssat/B0Ma 命名，不参与。')
        self.res_lab.setText('\n'.join(lines))

    def _complete_group(self):
        ck = [r for r in self._checked() if r['known']]
        if not ck:
            QMessageBox.information(self, '无需补齐',
                                    '勾选的都不是 S0/Ssat/B0Ma 命名 → 会按【单序列】各建一份 3D，不用补齐。')
            return
        prefix = self._anchor_prefix(ck)
        combos, missing = self._combo_of(prefix)
        if not combos:
            QMessageBox.warning(self, '凑不齐',
                                f'前缀「{prefix}」下没有「编号相邻」的三件套。\n'
                                f'缺：{missing or "（编号不相邻）"}')
            return
        want = set(zip(TAGS, combos[0]))
        self._filling = True
        for i, r in enumerate(self.rows):
            if r['known'] and r['prefix'] == prefix:
                on = (r['tag'], r['num']) in want
                self.table.item(i, 0).setCheckState(
                    Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self._filling = False
        self._analyze_dims()
        self._reanalyze()

    def _analyze_dims(self):
        ck = self._checked()
        if not ck:
            return
        self.res_lab.setText('正在读 DICOM 标签（判定维度）…')
        self.setEnabled(False)
        want = {r['path'] for r in ck}
        for i, r in enumerate(self.rows):
            if r['path'] not in want:
                continue
            dim = series.scan_dims(r['path'], slice_max=self.slice_max)
            r['dim'] = dim
            it = self.table.item(i, 5)
            it.setText(dim.text if not dim.error else dim.error)
            it.setForeground(GREEN if dim.ndim == '3D' else
                             (RED if dim.ndim == '4D' else GREY))
        self.table.resizeColumnsToContents()
        self.setEnabled(True)
        self._reanalyze()

    # ------------------------------------------------------------- 返回
    def _accept(self):
        ck_all = self._checked()
        if not ck_all:
            QMessageBox.warning(self, '没勾选', '请先勾选一个或多个文件夹。')
            return
        ck = [r for r in ck_all if r['known']]
        single = [r['path'] for r in ck_all if not r['known']]
        prefix = self._anchor_prefix(ck) if ck else ''
        seqs = {}
        for r in sorted(ck, key=lambda x: 0 if x['prefix'] == prefix else 1):
            if r['prefix'] == prefix:
                seqs.setdefault(r['tag'], r['path'])
        missing = [t for t in TAGS if t not in seqs]
        if missing and seqs:
            ret = QMessageBox.question(
                self, '缺件',
                '缺：' + '、'.join(missing) + '\n\n只重建你勾选的这几个吗？\n'
                '（缺 S0 → 无法归一化；缺 Ssat → 没有频率维）',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ret != QMessageBox.StandardButton.Yes:
                return
        if not self.data_root.resolve() == Path(self.cur).resolve():
            rel = Path(self.cur).resolve().relative_to(self.data_root.resolve())
            case = rel.parts[0]
        else:
            case = Path(self.cur).name
        self.sel = dict(case=case, case_dir=str(self.cur), prefix=prefix,
                        seqs=seqs, single=single, missing=missing,
                        n_unmatched=len([r for r in self.rows if not r['known']]))
        self.accept()

    def selection(self):
        return self.sel
