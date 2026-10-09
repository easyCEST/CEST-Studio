# -*- coding: utf-8 -*-
"""app/dialogs/params_dialog.py —— 通用「高级参数…」对话框。

用途：模块里 `Field(..., advanced=True, group='洛伦兹拟合')` 的参数不占主表单，
      统一收进这个对话框（按 group 分组 + 预设 + 校验 + 存/载预设）。

★ 编辑的是**页面上的同一批控件**（通过 getter/setter 读写 page.widgets），
  所以 `page.params()` / `settings.json` / 运行都天然一致，不存在两套值。
"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)


def _is_num(kind):
    return kind in ('int', 'float')


class ParamsDialog(QDialog):
    def __init__(self, parent, module, fields, getter, setter,
                 all_params=None, proj=None, title='高级参数'):
        super().__init__(parent)
        self.setWindowTitle(f'{module.badge} {module.title} —— {title}')
        self.resize(560, 660)
        self.mod = module
        self.fields = list(fields)
        self.getter = getter            # key -> 值（str / bool）
        self.setter = setter            # (key, 值)
        self.all_params = all_params    # () -> dict（整页参数，用于校验）
        self.proj = proj
        self.widgets = {}
        self._build()

    # ------------------------------------------------------------------ UI
    def _mk(self, f):
        if f.kind == 'bool':
            w = QCheckBox()
            return w
        if f.kind == 'choice':
            w = QComboBox()
            w.addItems([str(c) for c in f.choices])
            return w
        w = QLineEdit()
        return w

    def _set_w(self, f, val):
        w = self.widgets[f.key]
        if isinstance(w, QCheckBox):
            w.setChecked(bool(val))
        elif isinstance(w, QComboBox):
            t = str(val)
            if t in [w.itemText(i) for i in range(w.count())]:
                w.setCurrentText(t)
        else:
            w.setText('' if val is None else str(val))

    def _val(self, f):
        w = self.widgets[f.key]
        if isinstance(w, QCheckBox):
            return w.isChecked()
        if isinstance(w, QComboBox):
            return w.currentText()
        return w.text()

    def _build(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # ---- 预设 / 工具行 ----
        head = QHBoxLayout()
        head.setSpacing(6)
        b_def = QPushButton('恢复默认')
        b_def.clicked.connect(self._reset_defaults)
        head.addWidget(b_def)
        b_load = QPushButton('载入预设…')
        b_load.clicked.connect(self._load_preset)
        b_save = QPushButton('保存为预设…')
        b_save.clicked.connect(self._save_preset)
        head.addWidget(b_load)
        head.addWidget(b_save)
        head.addStretch(1)
        root.addLayout(head)

        presets = self.mod.presets() or {}
        if presets:
            row = QHBoxLayout()
            row.setSpacing(6)
            row.addWidget(QLabel('预设：'))
            for name, vals in presets.items():
                b = QPushButton(name)
                b.setObjectName('pick')
                b.clicked.connect(lambda _=False, n=name, v=vals: self._apply_preset(n, v))
                row.addWidget(b)
            row.addStretch(1)
            root.addLayout(row)

        # ---- 分组表单（可滚动） ----
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        box = QWidget()
        bv = QVBoxLayout(box)
        bv.setSpacing(8)
        groups = {}
        for f in self.fields:
            groups.setdefault(getattr(f, 'group', '') or '其他', []).append(f)
        for gname, flds in groups.items():
            g = QGroupBox(gname)
            form = QFormLayout(g)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignRight
                                   | Qt.AlignmentFlag.AlignVCenter)
            form.setHorizontalSpacing(10)
            form.setVerticalSpacing(7)
            for f in flds:
                w = self._mk(f)
                self.widgets[f.key] = w
                if not isinstance(w, QLineEdit):
                    w.setToolTip(f.tip or '')
                else:
                    w.setToolTip(f.tip or '')
                    w.setPlaceholderText('' if f.default is not None else '（空=默认）')
                if isinstance(w, QComboBox):
                    pass
                form.addRow(f.label, w)
            bv.addWidget(g)
        bv.addStretch(1)
        scroll.setWidget(box)
        root.addWidget(scroll, 1)

        self.lab = QLabel('')
        self.lab.setObjectName('muted')
        self.lab.setWordWrap(True)
        root.addWidget(self.lab)

        bot = QHBoxLayout()
        bot.addStretch(1)
        b_ok = QPushButton('确定')
        b_ok.setObjectName('primary')
        b_ok.clicked.connect(self._ok)
        b_no = QPushButton('取消')
        b_no.clicked.connect(self.reject)
        bot.addWidget(b_ok)
        bot.addWidget(b_no)
        root.addLayout(bot)

        # 初始：从页面读当前值
        for f in self.fields:
            v = self.getter(f.key)
            if f.kind == 'bool':
                self._set_w(f, bool(v) if not isinstance(v, str) else
                            str(v).strip().lower() in ('1', 'true', 'yes', '是', 'on'))
            else:
                self._set_w(f, v)
        self._refresh_hint()

    # ------------------------------------------------------------------ 预设
    def _apply_preset(self, name, vals):
        if not vals:
            self._reset_defaults()
            return
        for k, v in vals.items():
            if k not in self.widgets:
                continue
            f = next((x for x in self.fields if x.key == k), None)
            if f is not None:
                self._set_w(f, v)
        self._refresh_hint(f'已套用预设「{name}」')

    def _reset_defaults(self):
        for f in self.fields:
            self._set_w(f, f.default)
        self._refresh_hint('已恢复默认值')

    def _preset_dir(self):
        if not self.proj:
            return None
        d = Path(self.proj) / '_预设'
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _save_preset(self):
        d = self._preset_dir()
        if d is None:
            QMessageBox.information(self, '不能保存', '还没选输出总目录（PROJ）。')
            return
        name, ok = QInputDialog.getText(self, '保存为预设', '预设名（如 我的LD设置）：')
        if not ok or not name.strip():
            return
        data = {f.key: self._val(f) for f in self.fields}
        f = d / f'{self.mod.id}_{name.strip()}.json'
        try:
            f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        except OSError as e:                                     # noqa: BLE001
            QMessageBox.warning(self, '保存失败', str(e))
            return
        self._refresh_hint(f'已保存预设：{f}')

    def _load_preset(self):
        d = self._preset_dir()
        if d is None or not list(d.glob(f'{self.mod.id}_*.json')):
            QMessageBox.information(self, '没有预设',
                                    f'还没存过预设（{d}）。')
            return
        names = sorted(x.name for x in d.glob(f'{self.mod.id}_*.json'))
        name, ok = QInputDialog.getItem(self, '载入预设', '选一个：', names, 0, False)
        if not ok or not name:
            return
        try:
            data = json.loads((d / name).read_text(encoding='utf-8'))
        except Exception as e:                                   # noqa: BLE001
            QMessageBox.warning(self, '载入失败', str(e))
            return
        for k, v in data.items():
            f = next((x for x in self.fields if x.key == k), None)
            if f is not None:
                self._set_w(f, v)
        self._refresh_hint(f'已载入预设：{name}')

    # ------------------------------------------------------------------ 校验
    def _errors(self):
        errs = []
        for f in self.fields:
            v = self._val(f)
            if _is_num(f.kind):
                t = str(v).strip()
                if t == '':
                    continue
                try:
                    float(t)
                except ValueError:
                    errs.append(f'{f.label}：「{t}」不是数字')
        return errs

    def _refresh_hint(self, extra=''):
        p = self.all_params() if callable(self.all_params) else {}
        try:
            errs = self.mod.validate(p, type('C', (), {'proj': self.proj})())
        except Exception as e:                                   # noqa: BLE001
            errs = [f'校验时出错：{e}']
        msg = '；'.join(errs[:3]) if errs else '参数看起来没问题。'
        self.lab.setText((extra + '　' if extra else '') + msg)

    def _ok(self):
        errs = self._errors()
        if errs:
            QMessageBox.warning(self, '有参数不对', '\n'.join('· ' + e for e in errs))
            return
        # 写回页面控件
        for f in self.fields:
            self.setter(f.key, self._val(f))
        # 整页校验（门禁等）
        p = self.all_params() if callable(self.all_params) else {}
        try:
            errs = self.mod.validate(p, type('C', (), {'proj': self.proj})())
        except Exception as e:                                   # noqa: BLE001
            errs = [f'{type(e).__name__}: {e}']
        if errs:
            r = QMessageBox.warning(
                self, '参数有问题', '\n'.join('· ' + e for e in errs)
                + '\n\n要照现在这样关掉吗？（跑到一半会报错）',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return
        self.accept()
