# -*- coding: utf-8 -*-
"""app/dialogs/clean_dialog.py —— 「清空旧结果」对话框：清掉某个模块输出目录里的旧东西。

★ 安全默认（一键路径）：**只删旧备份** `*.bak_<YYYYmmdd_HHMMSS>.<ext>`，
  保留最新一组产物（`voxel_LD.mat` / `0.7ut_img_interp.mat` / `ld_check.png` …）。
★ 想连最新产物一起清空，必须显式改选「整个病例子目录」→ 再二次确认（红字）。
★ 作用范围**只有** `PROJ\\<模块输出子目录>\\<病例>`（或它的全部病例子目录），
  绝不碰 ① 重建数据、⑧ 掩模、别的模块目录。
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QGroupBox, QHBoxLayout,
                               QLabel, QMessageBox, QPlainTextEdit, QPushButton,
                               QRadioButton, QVBoxLayout)

# 备份文件名：<原名>.bak_20261002_170150.<ext>
BAK_RE = re.compile(r'\.bak_\d{8}_\d{6}(\.[^.]+)?$', re.I)


def is_backup(name: str) -> bool:
    return bool(BAK_RE.search(str(name)))


def human_size(n) -> str:
    n = float(n)
    for u in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or u == 'GB':
            return f'{n:.0f} B' if u == 'B' else f'{n:.1f} {u}'
        n /= 1024
    return f'{n:.1f} GB'


def _scan(case_dir: Path):
    """→ (备份 list[(path,size)], 产物 list[(path,size)], 子目录 list[Path], 错误 str)"""
    backs, prods, subs = [], [], []
    err = ''
    try:
        for e in sorted(case_dir.iterdir(), key=lambda x: x.name):
            if e.is_dir():
                subs.append(e)
                continue
            try:
                sz = e.stat().st_size
            except OSError:
                sz = 0
            (backs if is_backup(e.name) else prods).append((e, sz))
    except OSError as ex:                                        # noqa: BLE001
        err = str(ex)
    return backs, prods, subs, err


class CleanDialog(QDialog):
    """范围（当前病例 / 所有病例）× 内容（只清备份 / 整个子目录清空）。"""

    def __init__(self, parent, base_dir, cur_case, label, log=None):
        super().__init__(parent)
        self.base = Path(base_dir)
        self.cur_case = str(cur_case or '')
        self.label = str(label)
        self.log = log or (lambda *_: None)
        self.n_del = 0
        self.freed = 0
        self._cases = []
        self.setWindowTitle(f'清空旧结果 —— {self.label}')
        self.setMinimumSize(780, 560)
        self._build()
        self._refresh()

    # ------------------------------------------------------------------ UI
    def _build(self):
        v = QVBoxLayout(self)
        v.setSpacing(8)

        head = QLabel(f'<b>清理对象：{self.label}</b><br>'
                      f'根目录：{self.base}')
        head.setWordWrap(True)
        v.addWidget(head)

        # 范围
        g1 = QGroupBox('1) 范围')
        h1 = QHBoxLayout(g1)
        h1.setSpacing(14)
        self.rb_cur = QRadioButton('当前病例')
        self.rb_all = QRadioButton('所有病例')
        self.rb_cur.setChecked(True)
        self.rb_cur.toggled.connect(self._refresh)
        h1.addWidget(self.rb_cur)
        h1.addWidget(self.rb_all)
        h1.addStretch(1)
        v.addWidget(g1)

        # 内容
        g2 = QGroupBox('2) 清理内容')
        vv = QVBoxLayout(g2)
        self.rb_bak = QRadioButton('只清旧备份（*.bak_<时间戳>）—— 保留最新一组产物　【推荐】')
        self.rb_wipe = QRadioButton('清空整个病例子目录（连最新产物一起删）')
        self.rb_bak.setChecked(True)
        self.rb_bak.toggled.connect(self._refresh)
        vv.addWidget(self.rb_bak)
        vv.addWidget(self.rb_wipe)
        self.warn = QLabel('')
        self.warn.setWordWrap(True)
        self.warn.setStyleSheet('color:#c0392b;')
        vv.addWidget(self.warn)
        v.addWidget(g2)

        # 清单
        self.listing = QPlainTextEdit()
        self.listing.setReadOnly(True)
        self.listing.setObjectName('result')
        v.addWidget(self.listing, 1)
        self.summary = QLabel('')
        self.summary.setWordWrap(True)
        v.addWidget(self.summary)

        bb = QDialogButtonBox()
        self.b_ok = QPushButton('确认清理')
        self.b_ok.setObjectName('primary')
        self.b_ok.clicked.connect(self._do_clean)
        b_no = QPushButton('取消')
        b_no.clicked.connect(self.reject)
        bb.addButton(self.b_ok, QDialogButtonBox.ButtonRole.AcceptRole)
        bb.addButton(b_no, QDialogButtonBox.ButtonRole.RejectRole)
        v.addWidget(bb)

    # -------------------------------------------------------------- 扫描刷新
    def _target_dirs(self):
        if self.rb_cur.isChecked() or not self._cases:
            return [self.base / self.cur_case] if self.cur_case else []
        return [self.base / c for c in self._cases]

    def _refresh(self):
        from detect import rules as R                          # 病例枚举口径统一
        try:
            self._cases = [e.name for e in R.iter_cases(self.base)]
        except OSError:
            self._cases = []
        if self.cur_case and self.cur_case not in self._cases:
            self._cases.insert(0, self.cur_case)
        self.rb_cur.setText(f'当前病例　{self.cur_case or "（没解析出病例）"}')
        self.rb_all.setText(f'所有病例（{len(self._cases)} 个）')
        self.rb_all.setEnabled(len(self._cases) > 1)

        wipe = self.rb_wipe.isChecked()
        self.warn.setText('⚠ 危险：会删掉**最新一组产物**（如 voxel_LD.mat / '
                          '0.7ut_img_interp.mat / ld_check.png），需要重新跑一遍才能再得到。'
                          if wipe else '')

        lines, n_bak, s_bak, n_all, s_all, n_wiped = [], 0, 0, 0, 0, 0
        for d in self._target_dirs():
            if not d.exists():
                lines.append(f'[目录不存在] {d}')
                continue
            backs, prods, subs, err = _scan(d)
            n_bak += len(backs)
            s_bak += sum(s for _f, s in backs)
            n_all += len(backs) + len(prods)
            s_all += sum(s for _f, s in backs) + sum(s for _f, s in prods)
            if wipe:
                n_wiped += len(backs) + len(prods)
            lines.append(f'【{d.name}】{len(prods)} 个产物 + {len(backs)} 个旧备份'
                         + (f' + {len(subs)} 个子目录（不动）' if subs else '')
                         + (f'　× {err}' if err else ''))
            for f, s in backs:
                lines.append(f'    备份  {human_size(s):>9}  {f.name}')
            for f, s in prods:
                lines.append(f'    产物  {human_size(s):>9}  {f.name}')
            for sub in subs:
                lines.append(f'    子目录（不动）  {sub.name}\\')
        self.listing.setPlainText('\n'.join(lines) if lines else '（没有可清理的目录）')

        if wipe:
            self.summary.setText(
                f'将删除 <b>{n_wiped}</b> 个文件，释放 <b>{human_size(s_all)}</b>'
                f'（含最新产物，不可撤销）')
        else:
            self.summary.setText(
                f'将删除 <b>{n_bak}</b> 个旧备份，释放 <b>{human_size(s_bak)}</b>；'
                f'保留 {n_all - n_bak} 个产物 / {human_size(s_all - s_bak)}'
                + ('' if n_bak else '　—— 没有旧备份可清（目录很干净）'))
        self.b_ok.setEnabled(bool(n_wiped if wipe else n_bak))

    # ---------------------------------------------------------------- 执行
    def _do_clean(self):
        wi = self.rb_wipe.isChecked()
        if wi:
            r = QMessageBox.question(
                self, '再确认一次',
                '真的要**清空整个病例子目录**吗？\n\n'
                '会删掉最新一组产物（voxel_LD.mat / 0.7ut_img_interp.mat / ld_check.png …），'
                '必须重跑一遍才能再得到。\n\n不可撤销。',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return

        errs = []
        for d in self._target_dirs():
            if not d.exists():
                continue
            # ★ 双保险：只允许动 base 的**直接**子目录
            try:
                if d.resolve().parent != self.base.resolve():
                    errs.append(f'跳过（不在本模块目录下）：{d}')
                    self.log(f'  ! 跳过（不在本模块目录下）：{d}')
                    continue
            except OSError:
                errs.append(f'跳过（路径异常）：{d}')
                self.log(f'  ! 跳过（路径异常）：{d}')
                continue
            if wi:
                try:
                    n = sum(1 for x in d.rglob('*') if x.is_file())
                    sz = sum(x.stat().st_size for x in d.rglob('*') if x.is_file())
                    shutil.rmtree(d)
                    self.n_del += n
                    self.freed += sz
                    self.log(f'  × 清空目录：{d}（{n} 个文件，{human_size(sz)}）')
                except OSError as e:                             # noqa: BLE001
                    errs.append(f'{d}: {e}')
                    self.log(f'  ! 清空失败：{d}：{e}')
            else:
                backs, _p, _s, _e = _scan(d)
                for f, sz in backs:
                    try:
                        f.unlink()
                        self.n_del += 1
                        self.freed += sz
                        self.log(f'  × 删除旧备份：{f.name}（{human_size(sz)}）')
                    except OSError as e:                         # noqa: BLE001
                        errs.append(f'{f.name}: {e}')
                        self.log(f'  ! 删不掉：{f.name}：{e}')

        msg = (f'已删除 {self.n_del} 个文件，释放 {human_size(self.freed)}。'
               + ('\n\n有 %d 项没删掉：\n%s' % (len(errs), '\n'.join(errs[:8]))
                  if errs else ''))
        QMessageBox.information(self, '清理完成', msg)
        self.accept()
