# -*- coding: utf-8 -*-
"""app/pages/base_page.py —— 通用模块页：由 module.schema 自动生成五段界面。

    [输入] 输入摘要 + 重新识别
    [参数] schema 自动表单（路径/算法/输出），带默认值
    [输出] PROJ 下的输出目录 + 「将生成的文件」预览
    [运行] [开始] [取消] 进度条 + 当前步骤（运行中锁参数）
    [结果] 运行日志 + 预览图 + [打开输出目录]
"""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QProgressBar, QPushButton,
                               QScrollArea, QSplitter, QTextEdit, QVBoxLayout,
                               QWidget)

from detect import chain

from jobs.worker import JobThread


# 输入根是「单病例 / 单个结果目录」的识别类型（这些走老办法：module.validate 把关）
SINGLE_KINDS = ('recon_mat', 'recon_root', 'img_mat', 'roi_mat', 'post_mat')


def _to_float(s):
    try:
        return float(str(s).strip())
    except Exception:                       # noqa: BLE001
        return None


class ModulePage(QWidget):
    def __init__(self, module, window):
        super().__init__()
        self.module = module
        self.window = window
        self.thread = None
        self._log_file = None
        self._last_png = None
        self.widgets = {}
        self.dyn_widgets = {}   # 动态下拉框：key -> QComboBox
        self._beat = QTimer(self)      # 运行中每秒心跳：步骤 + 已用秒数
        self._beat.setInterval(1000)
        self._beat.timeout.connect(self._on_beat)
        self._t_run = 0.0
        self._step_base = ''
        self._chain_case = ''        # 病例下拉里选的病例
        self._chain_ready = True     # 数据链是否齐（不齐 → 【开始】置灰）
        self._chain_res = None
        self._case_combo_active = False
        self._build()

    # ------------------------------------------------------------------ UI
    def _build(self):
        self.setObjectName('pageRoot')
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(10)
        title = QLabel(f'{self.module.badge}  {self.module.title}')
        title.setObjectName('pageTitle')
        head.addWidget(title)
        self.in_summary = QLabel('')
        self.in_summary.setObjectName('muted')
        head.addWidget(self.in_summary, 1)
        b_re = QPushButton('重新识别')
        b_re.clicked.connect(lambda: self.window.re_detect())
        head.addWidget(b_re, 0)
        root.addLayout(head)

        # ---- 数据链：本页输入根 → 自动解析（病例 / 数据 / 掩模）----
        # 数据链块加在**右栏**顶部（见下面 rrv）；放左栏会把模块面板压掉一行
        self.chain_box = QWidget()
        self.chain_box.setObjectName('chainBox')
        self.chain_box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        cvb = QVBoxLayout(self.chain_box)
        cvb.setContentsMargins(6, 4, 6, 4)
        cvb.setSpacing(2)
        self.root_label = QLabel('输入根：（未设置）')
        self.root_label.setWordWrap(True)
        cvb.addWidget(self.root_label)
        crow = QHBoxLayout()
        crow.setSpacing(6)
        self.lab_case = QLabel('病例：')
        self.case_combo = QComboBox()
        self.case_combo.setMinimumWidth(150)
        self.case_combo.currentTextChanged.connect(self._on_case_changed)
        self.lab_res = QLabel('')
        self.lab_res.setWordWrap(True)
        crow.addWidget(self.lab_case)
        crow.addWidget(self.case_combo)
        crow.addWidget(self.lab_res, 1)
        cvb.addLayout(crow)
        self.miss_label = QLabel('')
        self.miss_label.setWordWrap(True)
        cvb.addWidget(self.miss_label)
        srow = QHBoxLayout()
        srow.setSpacing(4)
        self.lab_stage = QLabel('数据链：')
        srow.addWidget(self.lab_stage)
        self.stage_btns = {}
        for _mid in chain.STAGE_ORDER:
            _b = QPushButton(chain.STAGE_NAME[_mid])
            _b.setObjectName('stageBtn')
            _b.setFlat(True)
            _b.setCursor(Qt.CursorShape.PointingHandCursor)
            _b.setToolTip('点一下跳到该模块')
            _b.clicked.connect(lambda _=False, k=_mid: self.window.goto(k))
            srow.addWidget(_b)
            self.stage_btns[_mid] = _b
        srow.addStretch(1)
        cvb.addLayout(srow)
        # （chain_box 已改加到右栏，这里不再加）

        split = QSplitter(Qt.Orientation.Horizontal)

        # ---- 左：输入 / 参数（可滚动）+ 输出 / 运行（常驻底部） ----
        left = QWidget()
        left.setObjectName('pageLeft')
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 4, 0)
        lv.setSpacing(8)

        top_box = QWidget()
        tv = QVBoxLayout(top_box)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.setSpacing(6)

        # [模块自定义面板]（如 ① 的「序列与频率」）
        self._extra = None
        try:
            self._extra = self.module.extra_panel(self)
        except Exception as e:                                    # noqa: BLE001
            print(f'[extra_panel] {self.module.id} 失败：{e}')
        if self._extra is not None:
            tv.addWidget(self._extra)

        # [参数]
        g_p = QGroupBox('参数')
        form = QFormLayout(g_p)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(9)
        form.setContentsMargins(4, 4, 4, 4)
        for f in self.module.schema:
            w = self._make_widget(f)
            self.widgets[f.key] = w
            if f.tip:
                w.setToolTip(f.tip)
            form.addRow(f.label, w)
        # 记下每行的「标签 + 控件」，以便按识别类型 / 图类型显隐
        self._rows = {}
        for f in self.module.schema:
            w = self.widgets[f.key]
            self._rows[f.key] = (form.labelForField(w), w)
            if f.key == 'mode' and isinstance(w, QComboBox):
                w.currentTextChanged.connect(
                    lambda *_a: self._apply_visibility(self.window.det))
        # 高级参数（Field.advanced=True）默认收进【高级参数…】对话框，不占主表单
        self._adv_fields = [f for f in self.module.schema
                            if getattr(f, 'advanced', False)]
        if self._adv_fields:
            b_adv = QPushButton(f'高级参数…（{len(self._adv_fields)} 项）')
            b_adv.setObjectName('pick')
            b_adv.setToolTip('拟合区间 / lb / ub / par0 / 网格 / 归一化… 都在这里改，带预设')
            b_adv.clicked.connect(self._open_adv_params)
            form.addRow('', b_adv)
        self._apply_visibility(getattr(self.window, 'det', None))
        g_p.setMinimumWidth(468)
        tv.addWidget(g_p)
        # 影响「计划」的参数一变就重新分析
        for _k in ('src', 'cases', 'case', 'seq_base', 'slice_max', 'm0_tpi'):
            _w = self.widgets.get(_k)
            if _w is None:
                continue
            if hasattr(_w, '_edit'):
                _w._edit.editingFinished.connect(self.refresh_extra)
            elif isinstance(_w, QComboBox):
                _w.currentTextChanged.connect(lambda *_a: self.refresh_extra())
            elif isinstance(_w, QLineEdit):
                _w.editingFinished.connect(self.refresh_extra)
        # ★ 任一参数一变 → 实时刷「将生成…」预览（只拼字符串，不做重活）。
        #   不接的话会报旧值：例如 ② 把「图类型」从 APTW 改成 ST，下面仍写着 APTW 3_5ppm。
        for _f in self.module.schema:
            _w = self.widgets.get(_f.key)
            if _w is None:
                continue
            if isinstance(_w, QCheckBox):
                _w.toggled.connect(self._on_any_param)
            elif isinstance(_w, QComboBox):
                _w.currentTextChanged.connect(self._on_any_param)
            elif hasattr(_w, '_edit'):
                _w._edit.textChanged.connect(self._on_any_param)
            elif isinstance(_w, QLineEdit):
                _w.textChanged.connect(self._on_any_param)
        tv.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(190)
        scroll.setWidget(top_box)
        lv.addWidget(scroll, 1)

        # [输出 + 运行]（常驻底部，不滚动）
        g_ro = QGroupBox('输出与运行')
        rv = QVBoxLayout(g_ro)
        rv.setSpacing(5)
        self.out_label = QLabel('（未选输出总目录）')
        self.out_label.setObjectName('outLabel')
        self.out_label.setWordWrap(True)
        self.out_preview = QLabel('')
        self.out_preview.setObjectName('outPreview')
        self.out_preview.setWordWrap(True)
        rv.addWidget(self.out_label)
        rv.addWidget(self.out_preview)
        hb = QHBoxLayout()
        hb.setSpacing(8)
        self.btn_run = QPushButton('开始')
        self.btn_run.setObjectName('primary')
        self.btn_run.clicked.connect(self._on_run)
        self.btn_cancel = QPushButton('取消')
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._on_cancel)
        hb.addWidget(self.btn_run)
        hb.addWidget(self.btn_cancel)
        hb.addStretch(1)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.step_label = QLabel('就绪')
        self.step_label.setObjectName('stepLabel')
        rv.addLayout(hb)
        rv.addWidget(self.bar)
        rv.addWidget(self.step_label)
        lv.addWidget(g_ro, 0)

        split.addWidget(left)

        # ---- 右：结果 ----
        right = QWidget()
        right.setObjectName('pageRight')
        rrv = QVBoxLayout(right)
        # ★ 数据链块放右栏顶部：左栏纵向空间紧，放左边会把 ④/②/⑧ 面板的状态行压掉
        rrv.addWidget(self.chain_box)
        rrv.setContentsMargins(4, 0, 0, 0)
        rrv.setSpacing(6)
        res_title = QLabel('结果')
        res_title.setObjectName('sectionTitle')
        rrv.addWidget(res_title)
        self.result = QTextEdit()
        self.result.setObjectName('result')
        self.result.setReadOnly(True)
        rrv.addWidget(self.result, 3)

        self.preview = QLabel('（预览图会显示在这里）')
        self.preview.setObjectName('preview')
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(240)
        rrv.addWidget(self.preview, 4)

        self.btn_open = QPushButton('打开输出目录')
        self.btn_open.clicked.connect(self._open_out)
        rrv.addWidget(self.btn_open, alignment=Qt.AlignmentFlag.AlignLeft)
        split.addWidget(right)

        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 5)
        root.addWidget(split)

    def _make_widget(self, f):
        if f.kind == 'bool':
            w = QCheckBox()
            w.setChecked(bool(f.default))
            return w
        if f.kind == 'choice':
            w = QComboBox()
            w.addItems([str(c) for c in f.choices])
            if f.default is not None and str(f.default) in [str(c) for c in f.choices]:
                w.setCurrentText(str(f.default))
            if getattr(f, 'dyn', False):
                self.dyn_widgets[f.key] = w
            return w
        if f.kind in ('dir', 'file'):
            box = QWidget()
            h = QHBoxLayout(box)
            h.setContentsMargins(0, 0, 0, 0)
            e = QLineEdit('' if f.default is None else str(f.default))
            b = QPushButton('…')
            b.setObjectName('pick')
            b.setFixedWidth(32)
            b.clicked.connect(lambda _=False, ed=e, k=f.kind: self._pick(ed, k))
            h.addWidget(e, 1)
            h.addWidget(b)
            box._edit = e                                       # 便于取值
            return box
        # text / int / float / cases
        w = QLineEdit('' if f.default is None else str(f.default))
        if f.kind == 'cases':
            w.setPlaceholderText('如 c1,c2,c3,h1,h2,h3,hp1,hp2,hp3')
        return w

    def _pick(self, edit, kind):
        if kind == 'dir':
            p = QFileDialog.getExistingDirectory(self, '选择目录', edit.text() or '')
        else:
            p, _ = QFileDialog.getOpenFileName(self, '选择文件', edit.text() or '',
                                               'MAT (*.mat);;所有文件 (*)')
        if p:
            edit.setText(p)

    # --------------------------------------------------------------- 取值
    def _get(self, key):
        w = self.widgets[key]
        if isinstance(w, QCheckBox):
            return w.isChecked()
        if isinstance(w, QComboBox):
            return w.currentText()
        if hasattr(w, '_edit'):
            return w._edit.text()
        return w.text()

    def _set(self, key, val):
        w = self.widgets.get(key)
        if w is None:
            return
        if isinstance(w, QCheckBox):
            w.setChecked(bool(val))
        elif isinstance(w, QComboBox):
            if str(val) in [w.itemText(i) for i in range(w.count())]:
                w.setCurrentText(str(val))
        elif hasattr(w, '_edit'):
            w._edit.setText('' if val is None else str(val))
        else:
            w.setText('' if val is None else str(val))

    def params(self) -> dict:
        p = {}
        for f in self.module.schema:
            v = self._get(f.key)
            if f.kind == 'int':
                p[f.key] = int(_to_float(v)) if str(v).strip() not in ('', 'None') else None
            elif f.kind == 'float':
                p[f.key] = _to_float(v)
                if f.key in ('caxis_lo', 'caxis_hi') and p[f.key] is None:
                    p[f.key] = ''
            else:
                p[f.key] = v
        return p

    # ------------------------------------------------------- 识别/预览刷新
    def attach(self, det, proj):
        """识别结果或输出目录变化时刷新本页。"""
        if det is not None:
            self.in_summary.setText(f'输入：{det.label} · {Path(det.path).name}')
        sug = self.module.suggest(det, proj)
        for k, v in (sug or {}).items():
            if k in self.widgets:
                self._set(k, v)
        self.refresh_chain(det, proj)
        self._apply_visibility(det)
        self.refresh_extra()
        self.refresh_preview(proj)
        self._refresh_dyn(proj)

    def reset(self):
        """初始化：参数回默认值、清空文本栏与预览图，回到"还没选目录"的样子。"""
        self.in_summary.setText('')
        for f in self.module.schema:
            w = self.widgets.get(f.key)
            if w is None:
                continue
            if isinstance(w, QCheckBox):
                w.setChecked(bool(f.default))
            elif isinstance(w, QComboBox):
                if f.default is not None and str(f.default) in [w.itemText(i)
                                                                 for i in range(w.count())]:
                    w.setCurrentText(str(f.default))
                elif w.count():
                    w.setCurrentIndex(0)
            elif hasattr(w, '_edit'):
                w._edit.setText('' if f.default is None else str(f.default))
            else:
                w.setText('' if f.default is None else str(f.default))
        self.result.clear()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.step_label.setText('就绪')
        self._last_png = None
        self.preview.clear()
        self.preview.setText('（预览图会显示在这里）')
        self._chain_case = ''
        self._case_combo_active = False
        self.refresh_chain(None, '')
        self._apply_visibility(None)
        self.refresh_extra()
        self.refresh_preview('')

    def refresh_extra(self):
        """刷新模块自定义面板（① 的「序列与频率」等）。"""
        if getattr(self, '_extra', None) is None:
            return
        try:
            self.module.refresh_extra(self._extra, self.window.det,
                                      self.window.proj(), self.params())
        except Exception as e:                                    # noqa: BLE001
            self.window.log(f'[面板刷新失败] {type(e).__name__}: {e}')

    def _apply_visibility(self, det):
        """按识别类型（Field.kinds）和图类型（Field.modes）只显示相关参数。"""
        kind = getattr(det, 'kind', None)
        mode = (str(self._get('mode')).upper() if 'mode' in self.widgets else '')
        for f in self.module.schema:
            keep = True
            if getattr(f, 'kinds', ()) and kind:
                keep = kind in f.kinds
            if keep and getattr(f, 'modes', ()):
                keep = mode in [str(m).upper() for m in f.modes]
            si = getattr(f, 'show_if', ())
            if keep and si and len(si) == 2:
                keep = str(self._get(si[0]) or '') in [str(v) for v in si[1]]
            if keep and f.key == 'case' and getattr(self, '_case_combo_active', False):
                keep = False          # 病例已由数据链下拉框接管
            if getattr(f, 'advanced', False):      # 高级参数只在对话框里改
                keep = False
            lab, w = self._rows.get(f.key, (None, None))
            for x in (lab, w):
                if x is not None:
                    x.setVisible(keep)

    def _open_adv_params(self):
        """打开「高级参数…」对话框（编辑的是本页同一批控件 → 参数/设置天然一致）。"""
        from app.dialogs.params_dialog import ParamsDialog
        proj = self.window.proj()
        dlg = ParamsDialog(self, self.module, self._adv_fields,
                           getter=self._get, setter=self._set,
                           all_params=self.params, proj=proj)
        if dlg.exec():
            self.refresh_extra()
            self.refresh_preview(proj)

    def refresh_preview(self, proj):
        p = self.params()
        d = self.module.out_dir_for(p, proj)
        self.out_label.setText(f'输出目录：{d}' if d else '（未选输出总目录）')
        files = self.module.preview_files(p, proj) if d else []
        self.out_preview.setText('将生成：\n' + '\n'.join('  ' + x for x in files)
                                 if files else '')
        self._out_dir = d

    # --------------------------------------------------------------- 运行
    def _set_running(self, running: bool):
        self.btn_run.setEnabled((not running) and getattr(self, '_chain_ready', True))
        self.btn_cancel.setEnabled(running)
        for w in self.widgets.values():
            if hasattr(w, '_edit'):
                w._edit.setEnabled(not running)
                w.setEnabled(not running)
            else:
                w.setEnabled(not running)

    def _on_run(self):
        proj = self.window.proj()
        p = self.params()
        if not getattr(self, '_chain_ready', True):
            res = self._chain_res
            miss = list(res.missing) if res is not None else ['数据链不完整']
            QMessageBox.warning(self, '数据链不完整',
                                '\n'.join('· ' + m for m in miss)
                                + '\n\n（缺什么就先去对应模块补上；'
                                  '本软件不会自动生成掩模 / 自动估参数。）')
            return
        errs = self.module.validate(p, self.window.job_ctx(proj))
        if errs:
            QMessageBox.warning(self, '参数有问题', '\n'.join('· ' + e for e in errs))
            return
        if not proj:
            QMessageBox.warning(self, '缺少输出总目录', '请先在顶部选择「输出总目录」（PROJ）。')
            return

        self.result.clear()
        self.bar.setValue(0)
        self.step_label.setText('启动…')
        self._step_base = '启动…'
        self._t_run = time.time()
        self._beat.start()             # ★ 每秒刷一次，运行中永远不会像死掉
        self._set_running(True)

        # 日志落 PROJ\logs\<模块>_<时间戳>.log
        try:
            logs = Path(proj) / '日志'
            logs.mkdir(parents=True, exist_ok=True)
            f = logs / f'{self.module.id}_{time.strftime("%Y%m%d_%H%M%S")}.log'
            self._log_file = open(f, 'w', encoding='utf-8')
        except Exception:                                            # noqa: BLE001
            self._log_file = None

        self.thread = JobThread(self.module, p, proj=proj, det=self.window.det, parent=self)
        self.thread.sig_log.connect(self._on_log)
        self.thread.sig_progress.connect(self._on_progress)
        self.thread.sig_done.connect(self._on_done)
        self.thread.sig_fail.connect(self._on_fail)
        self.thread.start()

    def _on_cancel(self):
        if self.thread:
            self.thread.cancel()
            self.step_label.setText('正在取消…（当前这一步跑完就停）')

    def _on_log(self, s):
        self.result.append(s)
        self.window.log(s)
        if self._log_file:
            try:
                self._log_file.write(s + '\n')
                self._log_file.flush()
            except Exception:                                        # noqa: BLE001
                pass
        if s.startswith('  ') or s.startswith('['):
            self.step_label.setText(s.strip()[:80])

    def _on_progress(self, cur, total, text):
        if total:
            self.bar.setRange(0, total)
            self.bar.setValue(cur)
        else:
            self.bar.setRange(0, 0)
        if text:
            self._step_base = text
            self.step_label.setText(text)

    def _on_beat(self):
        """秒表：当前步骤 + 已用秒数，每秒刷一次 step_label。

        ④ 逐体素拟合单层可能很慢，有它进度区每秒都在动。
        """
        if self._t_run:
            self.step_label.setText(
                f'{self._step_base}　⏱ 已用 {time.time() - self._t_run:.0f}s')

    def _on_done(self, res):
        self._set_running(False)
        self.bar.setRange(0, 1)
        self.bar.setValue(1)
        self._beat.stop()
        self._t_run = 0.0
        self.step_label.setText('完成 √')
        if self._log_file:
            self._log_file.close()
            self._log_file = None
        png = None
        try:
            png = self.module.preview_image(res)
        except Exception:                                            # noqa: BLE001
            png = None
        if png:
            self._show_image(png)
        self.window.refresh_modules()

    def _on_fail(self, msg, tb):
        self._set_running(False)
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
        self._beat.stop()
        self._t_run = 0.0
        self.step_label.setText(f'失败：{msg}')
        if self._log_file:
            self._log_file.close()
            self._log_file = None

    def _show_image(self, p):
        pm = QPixmap(str(p))
        if pm.isNull():
            return
        self._last_png = str(p)
        self.preview.setPixmap(pm.scaled(self.preview.size(),
                                         Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation))

    def _open_out(self):
        import os
        d = getattr(self, '_out_dir', None)
        if d and Path(d).exists():
            os.startfile(str(d))                                     # noqa: S606
        else:
            QMessageBox.information(self, '还没有输出', '这个模块的输出目录还不存在。')

    # ------------------------------------------------------- 数据链（输入根 → 解析）
    def refresh_chain(self, det=None, proj=None):
        """刷「输入根 / 解析（病例·数据·掩模）」两行 + 数据链自检 + 【开始】可用性。

        * 模块 chain_proj=True 且输入根是 PROJ → 用 detect/chain.py 自动填 case/src/mask_file；
        * 缺数据 → 红字写清缺哪一步，并把【开始】置灰（不硬跑）。
        """
        win = self.window
        proj = win.proj() if proj is None else proj
        det = getattr(win, 'det', None) if det is None else det
        mod = self.module
        root = win.input_root() if hasattr(win, 'input_root') else ''
        explicit = bool(getattr(win, '_inputs', {}).get(mod.id))
        tail = ('' if (explicit or mod.id not in chain.DATA_MODULES)
                else '（默认 = 输出总目录 PROJ）')
        self.root_label.setText(
            f'输入根：{root or "（未设置）"}{tail}'
            + (f'　·　识别：{det.label}' if det is not None else ''))

        self._case_combo_active = False
        is_proj = bool(det is not None and getattr(det, 'kind', '') == 'project_root')
        mode = ('proj' if (det is None or is_proj)
                else ('single' if getattr(det, 'kind', '') in SINGLE_KINDS else 'bad'))
        if not getattr(mod, 'chain_proj', False):
            self.lab_case.setVisible(False)
            self.case_combo.setVisible(False)
            self.lab_res.setText(f'解析：{det.label}' if det is not None
                                 else '解析：（先设置输入根）')
            self.lab_res.setStyleSheet('')
            self.miss_label.setText('')
            self.miss_label.setVisible(False)
            self._chain_ready = True
            self._update_stage(None)
            self._apply_visibility(det)
            self._apply_ready()
            return

        # ---- 病例下拉（从 PROJ 枚举，跳过 `_` 开头的元数据目录）----
        res0 = chain.resolve(proj, mod.id)
        cases = list(res0.cases or [])
        _pc = str(self._get('case')).strip() if 'case' in self.widgets else ''
        if mode == 'single':
            want = self._chain_case or _pc
            if want and want not in cases:
                cases.insert(0, want)
        else:                      # proj / bad：只能从 PROJ 的病例里选
            want = self._chain_case if self._chain_case in cases else ''
            if not want and _pc in cases:
                want = _pc
        self.case_combo.blockSignals(True)
        self.case_combo.clear()
        self.case_combo.addItems(cases)
        if want and want in cases:
            self.case_combo.setCurrentText(want)
        self.case_combo.blockSignals(False)
        pick = self.case_combo.currentText().strip()
        self._chain_case = pick or res0.case

        if mode == 'single':
            # 输入根 = 某个病例 / 某份结果目录 → 老办法（module.validate 把关）
            self.lab_case.setVisible(False)
            self.case_combo.setVisible(False)
            self._case_combo_active = False
            _c = self._chain_case or str(self._get('case')).strip() or '?'
            self.lab_res.setText(f'解析：单病例模式（{det.label}）'
                                 f'——掩模仍从 00_掩模\\{_c} 取')
            self.lab_res.setStyleSheet('')
            self.lab_res.setToolTip('')
            self.miss_label.setText('')
            self.miss_label.setStyleSheet('')
            self.miss_label.setVisible(False)
            self._chain_res = None
            self._chain_ready = True
            self._update_stage(None)
            self._apply_visibility(det)
            self._apply_ready()
            return

        res = chain.resolve(proj, mod.id, case=self._chain_case)
        if res.case:
            self._chain_case = res.case
        self._chain_res = res
        if mode == 'proj' and res.case:
            for key in getattr(mod, 'chain_fill', ()):
                if key not in self.widgets:
                    continue
                if key == 'case':
                    self._set('case', res.case)
                elif key == 'src':
                    self._set('src', res.src_dir or '')
                elif key == 'mask_file':
                    self._set('mask_file', res.mask_file or '')

        if mode == 'proj':
            show_combo = bool(cases)
            parts = [f'病例 {res.case or "—"}']
            parts.append(f'数据 {chain.relpath(proj, res.src_dir)} ✓' if res.src_dir
                         else '数据 ✗')
            if ('mask_file' in getattr(mod, 'chain_fill', ())
                    or '00_roi' in chain.NEEDS.get(mod.id, ())):
                parts.append(f'掩模 {chain.relpath(proj, res.mask_file)} ✓'
                             if res.mask_file else '掩模 ✗')
            self.lab_res.setText('　｜　'.join(parts))
            self.lab_res.setStyleSheet('color:#c0392b;' if res.missing
                                       else 'color:#1e8449;')
            self.lab_res.setToolTip(res.hint)
            self.miss_label.setText('；'.join(res.missing))
            self.miss_label.setStyleSheet('color:#c0392b;' if res.missing else '')
            self.miss_label.setVisible(bool(res.missing))
            self._chain_ready = bool(res.ready)
        else:
            # bad：输入根既不是项目目录（PROJ），也不是病例目录 → 红字 + 灰
            show_combo = False
            hint = (f'输入根 {root or "（空）"} 里没有可用数据：它既不是项目目录（PROJ，'
                    f'应含 01_重建\\<病例> 等子目录），也不是病例目录。'
                    + (f' 若是新项目，请先在 ① DICOM 重建 里跑出病例；'
                       f'或把本页输入根设成已有病例的 {proj}。' if proj else
                       ' 请把本页输入根改成项目目录（PROJ），或某个病例的重建结果目录。'))
            res.missing = [hint]
            self._chain_res = res
            self.lab_res.setText('解析：这不是项目目录（PROJ），也不是病例目录')
            self.lab_res.setStyleSheet('color:#c0392b;')
            self.lab_res.setToolTip('')
            self.miss_label.setText(hint)
            self.miss_label.setStyleSheet('color:#c0392b;')
            self.miss_label.setVisible(True)
            self._chain_ready = False

        # ---- 批量模式：放宽门禁（只要 PROJ 里有病例就放行；缺谁由模块 run 里提醒并跳过）----
        _bf = str(getattr(mod, 'chain_batch_field', '') or '')
        if _bf and _bf in self.widgets:
            _bv = str(self._get(_bf) or '')
            if any(_bv.startswith(v) for v in getattr(mod, 'chain_batch_values', ())):
                if res.cases:
                    self._chain_ready = True
                    self.miss_label.setText(
                        f'批量模式（{_bv}）：将处理 PROJ 下的全部病例；'
                        f'缺前置条件的会在运行时逐个提醒并跳过。')
                    self.miss_label.setStyleSheet('color:#b9770e;')
                    self.miss_label.setVisible(True)
                    # 「输入」字段：批量下不用单病例目录，直接指向 01_重建 根
                    if 'src' in self.widgets:
                        from detect import rules as _R
                        _rc = _R.find_subdir(proj, '01_recon')
                        if _rc is not None:
                            self._set('src', str(_rc))

        self.lab_case.setVisible(show_combo)
        self.case_combo.setVisible(show_combo)
        self._case_combo_active = show_combo
        self._refresh_dyn(proj)
        self._update_stage(res)
        self._apply_visibility(det)
        self._apply_ready()

    def _on_case_changed(self, text):
        text = str(text or '').strip()
        if not text or text == self._chain_case:
            return
        self._chain_case = text
        if 'case' in self.widgets:
            self._set('case', text)
        self.refresh_chain()
        self.refresh_extra()
        self.refresh_preview(self.window.proj())

    def _update_stage(self, res):
        case = (res.case if res is not None else '') or self._chain_case
        proj = self.window.proj()
        st = chain.stage_status(proj, case)
        try:                       # 模块可按当前参数覆盖个别阶段（如按模式判断 ⑤⑥）
            st.update(self.module.stage_overrides(self.params(), proj) or {})
        except Exception:                                      # noqa: BLE001
            pass
        for mid, b in self.stage_btns.items():
            ok = st.get(mid)
            b.setText(f'{chain.STAGE_NAME[mid]} {"✓" if ok else "待跑"}')
            b.setStyleSheet('color:#1e8449;' if ok else 'color:#b9770e;')

    def _refresh_dyn(self, proj=None):
        """重填动态下拉框（选项由模块 dynamic_choices 提供）。"""
        dyn = getattr(self, 'dyn_widgets', None) or {}
        fn = getattr(self.module, 'dynamic_choices', None)
        if not dyn or not callable(fn):
            return
        for key, w in dyn.items():
            try:
                opts = [str(o) for o in (fn(key, self._get, proj) or [])]
            except Exception:
                opts = []
            cur = [w.itemText(i) for i in range(w.count())]
            if opts == cur:
                continue
            keep = w.currentText()
            w.blockSignals(True)
            w.clear()
            w.addItems(opts)
            if keep and keep in opts:
                w.setCurrentText(keep)
            w.blockSignals(False)

    def _on_any_param(self, *_a):
        """任一参数控件变了 → 刷「将生成…」预览。

        只调 refresh_preview（纯字符串拼接）；重活（① 的序列扫描等）仍只在
        refresh_extra 里、且只挂在会影响计划的几个参数上，免得边输边扫盘。
        但若模块声明的批量开关（chain_batch_field）变了，就重走一次数据链。
        """
        self.refresh_preview(self.window.proj())
        self._refresh_dyn(self.window.proj())
        self._apply_visibility(getattr(self.window, 'det', None))
        self._update_stage(getattr(self, '_chain_res', None))   # 参数变了 → 数据链可能变（如 ⑥ 换分析模式）
        bf = str(getattr(self.module, 'chain_batch_field', '') or '')
        if bf and bf in getattr(self, 'widgets', {}):
            v = str(self._get(bf) or '')
            if v != getattr(self, '_batch_val_last', None):
                self._batch_val_last = v
                self.refresh_chain()

    def _apply_ready(self):
        running = bool(self.thread is not None and self.thread.isRunning())
        self.btn_run.setEnabled(bool(getattr(self, '_chain_ready', True)) and not running)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._last_png:
            pm = QPixmap(self._last_png)
            if not pm.isNull():
                self.preview.setPixmap(pm.scaled(self.preview.size(),
                                                 Qt.AspectRatioMode.KeepAspectRatio,
                                                 Qt.TransformationMode.SmoothTransformation))
