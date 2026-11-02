# -*- coding: utf-8 -*-
"""app/dialogs/freq_dialog.py —— 频率表对话框。

规则（已确认）：
  * 4D 序列**没有确认过的频率表就不许重建**（由 ① 的 validate 硬拦，这里再兜一道）；
  * 来源按优先级自动找：上次确认过 → 继承旧结果 → 数据目录内的说明文档 → 手动；
  * **只扫输入数据目录内的文档**，项目文档不扫；
  * 解析结果一律显示「出处 + 置信度 + 全部数值」，并要用户勾选确认。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QGroupBox,
                               QHBoxLayout, QInputDialog, QLabel, QMenu,
                               QMessageBox, QPlainTextEdit, QPushButton,
                               QVBoxLayout, QWidget)

from detect import freq_doc as FD

QUICK = {
    'P / N 型：-12 : 0.1 : 12（241 个）': 'range:-12:0.1:12',
    'APT 型：-10 : 0.1 : 10（201 个）': 'range:-10:0.1:10',
    '7T：-10 : 0.2 : 10（101 个）': 'range:-10:0.2:10',
}


class FreqDialog(QDialog):
    def __init__(self, parent, *, seq_name: str, need: int, case: str,
                 case_dir: str = '', data_root: str = '', proj: str = '',
                 allow_extra: bool = True):
        super().__init__(parent)
        self.setWindowTitle('频率表（4D 必需）')
        self.setMinimumWidth(720)
        self.seq_name = seq_name
        self.need = int(need or 0)
        self.allow_extra = bool(allow_extra)
        self.exact = not self.allow_extra
        self.case = case
        self.case_dir = case_dir
        self.data_root = data_root
        self.proj = proj
        self.payload = None
        self._build()
        self._auto()

    # ------------------------------------------------------------------ UI
    def _build(self):
        v = QVBoxLayout(self)
        v.setSpacing(8)

        head = QLabel(f'用于 4D 序列：<b>{self.seq_name}</b>'
                      f'　每层 <b>{self.need or "?"}</b> 帧'
                      + ('　（频偏表可以少于帧数，多出的帧由重建按谱线物理判据自动识别并丢弃）'
                         if self.allow_extra else '　（频偏数必须等于此数）'))
        head.setWordWrap(True)
        v.addWidget(head)

        self.src_lab = QLabel('来源：正在自动查找…')
        self.src_lab.setObjectName('srcLabel')
        self.src_lab.setWordWrap(True)
        v.addWidget(self.src_lab)

        # 快捷动作
        row = QHBoxLayout()
        for text, fn in (('选择频率文档…', self._pick_doc),
                         ('从旧结果继承', self._do_inherit),
                         ('常用预设', None),
                         ('清空', self._do_clear)):
            b = QPushButton(text)
            if fn is None:
                m = QMenu(b)
                for k in QUICK:
                    m.addAction(k, lambda _=False, kk=k: self._do_preset(kk))
                b.setMenu(m)
            else:
                b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)

        box = QGroupBox('频率表（频偏；一行/逗号/空格都能填，也认 -12:0.1:12）')
        bv = QVBoxLayout(box)
        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText('例：-10 -8 -6 -5 …  或   -12:0.1:12')
        self.edit.setMinimumHeight(120)
        self.edit.textChanged.connect(self._validate)
        bv.addWidget(self.edit)
        v.addWidget(box)

        self.check_lab = QLabel('')
        self.check_lab.setWordWrap(True)
        self.check_lab.setObjectName('checkLabel')
        v.addWidget(self.check_lab)

        self.confirm = QCheckBox('我已核对以上频率无误（顺序与个数都对）')
        self.confirm.toggled.connect(self._validate)
        v.addWidget(self.confirm)

        self.note = QLabel('说明：频偏表取唯一值写进 mat；本表存到 <PROJ>\\01_重建\\_频率\\<病例>.json，'
                           '下次自动带出。若本序列每层帧数多于频偏数，多出的帧由重建按 z 谱 ±k '
                           '镜像对称自动识别为【额外帧】并丢弃——不会凭空补一个重复频偏。')
        if not self.allow_extra:
            self.note = QLabel('说明：w_offset 会写进 mat；本表存到 '
                               '<PROJ>\\01_重建\\_频率\\<病例>.json，下次自动带出。')
        self.note.setObjectName('muted')
        self.note.setWordWrap(True)
        v.addWidget(self.note)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.btn_ok = QPushButton('确定')
        self.btn_ok.setObjectName('primary')
        self.btn_ok.clicked.connect(self._accept)
        b = QPushButton('取消')
        b.clicked.connect(self.reject)
        btns.addWidget(self.btn_ok)
        btns.addWidget(b)
        v.addLayout(btns)

    # ------------------------------------------------------------- 取值/校验
    def values(self) -> list:
        out = []
        for ln in self.edit.toPlainText().splitlines():
            out += FD.parse_spec(ln)
        return out

    def _set_values(self, vals):
        self.edit.setPlainText(' '.join(f'{v:g}' for v in vals))

    def _validate(self):
        vals = self.values()
        s = FD.summarize(vals, self.need, exact=self.exact)
        lines = ['　'.join(s['msgs'])]
        if s['n']:
            head = s['unique'][:12]
            tail = '' if s['n_unique'] <= 12 else f' … {s["unique"][-3]:g}'
            lines.append('唯一频偏 = ' + ', '.join(f'{x:g}' for x in head) + tail +
                         f'（共 {s["n_unique"]} 个）')
        self.check_lab.setText('\n'.join(lines))
        self.check_lab.setStyleSheet('color:#166534;' if s['ok'] else 'color:#b91c1c;')
        self.btn_ok.setEnabled(bool(s['ok'] and self.confirm.isChecked()))
        ok_txt = '可以确定' if s['ok'] else '个数不对，不能确定'
        self.btn_ok.setToolTip(f'{ok_txt}；还需勾选「我已核对」')

    def _why_not_search(self):
        if not self.case_dir and not self.data_root:
            return '没拿到数据目录'

    def _pick_doc(self):
        """手动指定一份频率文档/协议文件，当场解析（.prot / .mat / txt / csv / json）。"""
        start = self.case_dir or self.data_root or ''
        p, _ = QFileDialog.getOpenFileName(
            self, '选择频率文档', start,
            '频率/协议 (*.prot *.mat *.txt *.csv *.json);;所有文件 (*)')
        if not p:
            return
        path = Path(p)
        suffix = path.suffix.lower()
        vals = []
        note = ''
        if suffix == '.prot':
            vals = FD.parse_prot(path)
            note = 'UIH 协议文件（.prot）'
        elif suffix == '.mat':
            vals = FD.load_freq_from_mat(path) or []
            note = '从 .mat 读 freq_seq / w_offset'
        else:
            text = FD.read_text(path)
            for ln in (text or '').splitlines():
                vals += FD.parse_spec(ln)
            note = '从文本文件解析'
        if len(vals) < 3:
            QMessageBox.warning(
                self, '读不出频率',
                f'这份文件里没能解析出频率表：\n{path.name}\n\n'
                f'请换一份文件，或直接在下面手动输入频率。\n'
                f'（手动输入支持：一行/逗号/空格分隔，或 -12:0.1:12）')
            self.src_lab.setText(f'来源：<b>指定了 {path.name}，但没解析出频率</b>')
            self._validate()
            return
        self._use(FD.Candidate(values=vals, source=str(path), line=0,
                               raw=f'{path.name}　[{note}]',
                               confidence='high', note=note),
                  f'你指定的文档')

    def _do_inherit(self):
        c = FD.inherit_freq(self.case_dir, self.proj, self.case)
        if not c:
            QMessageBox.information(self, '没找到', '病例目录/已有结果里没找到 freq.mat / w_offset.mat。')
            return
        self._use(c, '继承已有结果')

    def _do_preset(self, key):
        v = QUICK[key]
        if isinstance(v, str) and v.startswith('range:'):
            _, a, st, b = v.split(':')
            v = FD.expand_range(float(a), float(st), float(b))
        self._use(FD.Candidate(values=list(v), source=f'预设：{key}', confidence='low',
                               note='常用预设'), '常用预设')

    def _do_clear(self):
        self.edit.setPlainText('')
        self.src_lab.setText('来源：手动输入')

    def _use(self, cand: FD.Candidate, why: str):
        self._set_values(cand.values)
        conf = {'high': '高', 'mid': '中', 'low': '低'}[cand.confidence]
        where = ('预设' if cand.source.startswith('预设') else
                 f'{Path(cand.source).name}' + (f' 第 {cand.line} 行' if cand.line else ''))
        raw = f'<br><span style="color:#6b7280">原文：{cand.raw[:150]}</span>' if cand.raw else ''
        self.src_lab.setText(f'来源：<b>{why}</b>（{where}）　置信度：<b>{conf}</b>'
                             f'{("　" + cand.note) if cand.note else ""}{raw}')
        self.confirm.setChecked(False)
        self._validate()

    def _auto(self):
        """打开对话框时：只带出「本病例上次确认过的表」；**不自动检索任何文档**。"""
        c = FD.load_saved(self.proj, self.case)
        if c:
            self._use(c, '本病例上次确认过的表')
            return
        self.src_lab.setText(
            '来源：<b>未指定</b>　——　请点【选择频率文档…】指定一份'
            '（支持 .prot / .mat / txt / csv），或直接在下面手动输入频率。')
        self._validate()

    # ---------------------------------------------------------------- 确定
    def _accept(self):
        vals = self.values()
        s = FD.summarize(vals, self.need, exact=self.exact)
        if not s['ok']:
            if self.exact:
                tip = f'\n\n这个序列每层 {self.need} 帧，频率表必须是 {self.need} 个。'
            else:
                tip = (f'\n\n这个序列每层 {self.need} 帧；频偏表可以少（多出的帧会被自动丢弃），'
                       f'但**不能多于** {self.need} 个。')
            QMessageBox.warning(self, '个数不对', '\n'.join(s['msgs']) + tip)
            return
        if not self.confirm.isChecked():
            QMessageBox.warning(self, '还没确认', '请先勾选「我已核对以上频率无误」。')
            return
        self.payload = dict(values=vals, source=self.src_lab.text(),
                            note=f'need={self.need}')
        self.accept()
