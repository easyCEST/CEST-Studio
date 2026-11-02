# -*- coding: utf-8 -*-
"""app/dialogs/mosaic_view_dialog.py —— ② 多层图谱「交互式预览」。

    ┌ 左：层览(缩略图) ┐ ┌ 中：画布（只读，缩放/平移）┐ ┌ 右：控制 ─────────────┐
    │ ★ 层 1 … 层 N     │ │ 当前层的图谱图（jet 首行黑）   │ │ 模式 / 频偏 / 掩模     │
    │ 点哪层看哪层      │ │ 滚轮缩放 · 中键平移            │ │ 色标(实时) / 色图 / 导出 │
    └──────────────────┘ └──────────────────────────────┘ └───────────────────────┘

数据由 app/modules/m2_display.py 备好（cest_display.load_display_input 的结果）后传进来；
本对话框只做「看图 + 调色标 + 导出」，用到的取数/色标口径与 ② 的【开始】**完全同源**
（build_mode_and_caxis），导出的命名/备份也走**同一个** save_mosaic ——
所以「预览看到的 = 导出的 = ②【开始】出的」。
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QSpinBox, QVBoxLayout, QWidget)

from app.widgets.mask_canvas import SliceCanvas, to_qimage

# 预览色图（导出固定用 jet 首行黑，与 ②【开始】一致）
CMAP_VIEW = (('jet（首行黑）', 'jet_black1'), ('灰度', 'gray'), ('彩色 viridis', 'viridis'))
MODES = ('raw', 'ST', 'APTW', 'LD')


def _f(s):
    try:
        t = str(s).strip()
        return None if t == '' else float(t)
    except Exception:                                            # noqa: BLE001
        return None


class MosaicViewDialog(QDialog):
    def __init__(self, parent, data, case='', params=None, out_dir=None,
                 on_export=None):
        super().__init__(parent)
        self.setWindowTitle(f'② 多层图谱预览　—　{case or "（未命名）"}')
        self.resize(1280, 820)
        self.d = data
        self.img4 = np.asarray(data['img_interp'], dtype=float)
        self.n_layer = int(self.img4.shape[2])
        self.case = case
        self.params = dict(params or {})
        self.out_dir = out_dir
        self.on_export = on_export

        self.layer = 0
        self.img3d = None
        self.caxis = None
        self._build()
        self._reset_for_mode(first=True)

    # ================================================================== UI
    def _build(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)
        body = QHBoxLayout()
        body.setSpacing(8)

        # ---------------- 左：层览 ----------------
        left = QGroupBox('层览')
        lv = QVBoxLayout(left)
        lv.setSpacing(4)
        self.layers = QListWidget()
        self.layers.setIconSize(QPixmap(64, 64).size())
        self.layers.setFixedWidth(150)
        self.layers.currentRowChanged.connect(self._on_layer)
        lv.addWidget(self.layers, 1)
        self.lab_layer = QLabel('')
        self.lab_layer.setObjectName('muted')
        self.lab_layer.setWordWrap(True)
        lv.addWidget(self.lab_layer)
        body.addWidget(left, 0)

        # ---------------- 中：画布 ----------------
        mid = QWidget()
        mv = QVBoxLayout(mid)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.setSpacing(6)
        self.canvas = SliceCanvas()
        self.canvas.readonly = True            # 只看，不画
        self.canvas.tool = 'select'
        self.canvas.show_overlays = False
        self.canvas.mask = None
        mv.addWidget(self.canvas, 1)
        hint = QLabel('滚轮缩放 · 中键拖动平移；左边点哪层看哪层。'
                      '色标随时改、即时刷新（只影响观感）。')
        hint.setObjectName('muted')
        hint.setWordWrap(True)
        mv.addWidget(hint)
        body.addWidget(mid, 1)

        # ---------------- 右：控制 ----------------
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setSpacing(8)
        right.setFixedWidth(332)

        g_data = QGroupBox('数据')
        fd = QFormLayout(g_data)
        self.cb_mode = QComboBox()
        self.cb_mode.addItems(list(MODES))
        _m0 = str(self.params.get('mode') or 'APTW')
        for _i in range(self.cb_mode.count()):
            if self.cb_mode.itemText(_i).upper() == _m0.upper():
                self.cb_mode.setCurrentIndex(_i)
                break
        self.cb_mode.currentTextChanged.connect(lambda _t: self._reset_for_mode())
        self.sp_w = QDoubleSpinBox()
        self.sp_w.setRange(-12, 12)
        self.sp_w.setSingleStep(0.1)
        self.sp_w.setDecimals(2)
        self.sp_w.setValue(float(self.params.get('w') or 3.5))
        self.sp_w.valueChanged.connect(lambda _v: self._reset_for_mode())
        self.chk_mask = QCheckBox('用掩模压背景')
        self.chk_mask.setChecked(bool(self.params.get('use_mask', True)))
        self.chk_mask.setToolTip('口径 .*Mask - (~Mask)：掩模外压成 -1（与 ②【开始】一致）')
        self.chk_mask.toggled.connect(lambda _v: self._reset_for_mode())
        fd.addRow('图类型', self.cb_mode)
        fd.addRow('目标 ppm', self.sp_w)
        self.cb_ld = QComboBox()
        self.cb_ld.addItems(['POS', 'NEG'])
        self.cb_ld.setCurrentText(str(self.params.get('ld_mode') or 'POS'))
        self.cb_ld.setToolTip('只在 LD 模式生效：POS = 取 +目标ppm（NOE/-1.2 一带）；NEG = 取 -目标ppm')
        self.cb_ld.currentTextChanged.connect(lambda _t: self._reset_for_mode())
        fd.addRow('LD 正负', self.cb_ld)
        fd.addRow(self.chk_mask)
        self.lab_info = QLabel('')
        self.lab_info.setObjectName('muted')
        self.lab_info.setWordWrap(True)
        fd.addRow(self.lab_info)
        rv.addWidget(g_data)

        g_v = QGroupBox('显示')
        fv = QFormLayout(g_v)
        self.ed_lo = QLineEdit('')
        self.ed_hi = QLineEdit('')
        for _e in (self.ed_lo, self.ed_hi):
            _e.setMinimumWidth(64)
            _e.setMaximumWidth(96)
        b_auto = QPushButton('自动')
        b_auto.setObjectName('pick')
        b_auto.setToolTip('取「img .* Mask（背景=0）」非 0 体素的 5~95% 分位（= MATLAB 口径）')
        b_auto.clicked.connect(self._auto_cax)
        w_cax = QWidget()
        h_cax = QHBoxLayout(w_cax)
        h_cax.setContentsMargins(0, 0, 0, 0)
        h_cax.setSpacing(4)
        h_cax.addWidget(self.ed_lo, 1)
        h_cax.addWidget(QLabel('~'))
        h_cax.addWidget(self.ed_hi, 1)
        h_cax.addWidget(b_auto)
        fv.addRow('色标', w_cax)
        self.ed_lo.textChanged.connect(lambda _t: self._apply_clim())
        self.ed_hi.textChanged.connect(lambda _t: self._apply_clim())
        self.cb_cmap = QComboBox()
        self.cb_cmap.addItems([c[0] for c in CMAP_VIEW])
        self.cb_cmap.setToolTip('只影响看图的色图。导出一律用 jet（首行黑），与 ②【开始】一致。')
        self.cb_cmap.currentTextChanged.connect(lambda _t: self._show_layer(self.layer))
        fv.addRow('色图', self.cb_cmap)
        self.sp_cols = QSpinBox()
        self.sp_cols.setRange(1, 12)
        self.sp_cols.setValue(int(self.params.get('cols') or 5))
        self.sp_cols.setToolTip('导出拼图每行几个子图')
        fv.addRow('每行子图数', self.sp_cols)
        self.sp_dpi = QSpinBox()
        self.sp_dpi.setRange(72, 600)
        self.sp_dpi.setValue(int(self.params.get('dpi') or 300))
        fv.addRow('分辨率', self.sp_dpi)
        rv.addWidget(g_v)

        g_out = QGroupBox('导出')
        ov = QVBoxLayout(g_out)
        self.lab_out = QLabel('')
        self.lab_out.setObjectName('muted')
        self.lab_out.setWordWrap(True)
        ov.addWidget(self.lab_out)
        self.btn_export = QPushButton('导出当前所见')
        self.btn_export.setObjectName('primary')
        self.btn_export.clicked.connect(self._do_export)
        ov.addWidget(self.btn_export)
        rv.addWidget(g_out)
        rv.addStretch(1)
        body.addWidget(right, 0)

        root.addLayout(body, 1)

        bot = QHBoxLayout()
        self.lab_hint = QLabel('')
        self.lab_hint.setObjectName('muted')
        self.lab_hint.setWordWrap(True)
        bot.addWidget(self.lab_hint, 1)
        b_no = QPushButton('关闭')
        b_no.clicked.connect(self.accept)
        bot.addWidget(b_no)
        root.addLayout(bot)

    # ================================================================== 取数
    def _mode(self):
        return str(self.cb_mode.currentText()).upper()

    def _cmap_key(self):
        t = self.cb_cmap.currentText()
        for label, key in CMAP_VIEW:
            if label == t:
                return key
        return 'jet_black1'

    def _layers(self):
        s = str(self.params.get('layers') or '').replace('，', ',')
        v = [int(x) for x in s.split(',') if x.strip()]
        return v or None

    def _default_caxis(self, mode):
        lo, hi = _f(self.params.get('caxis_lo')), _f(self.params.get('caxis_hi'))
        if lo is not None and hi is not None:
            return (lo, hi)
        try:
            from app.modules.m2_display import DEFAULT_CAXIS
            return DEFAULT_CAXIS.get(mode)
        except Exception:                                        # noqa: BLE001
            return None

    def _reset_for_mode(self, first=False):
        """切模式/频偏/掩模 → 重算数据；色标按 ②【开始】的口径重置。"""
        from app.modules.m2_display import build_mode_and_caxis

        mode = self._mode()
        self.cb_ld.setEnabled(mode == 'LD')
        try:
            cax0 = self._default_caxis(mode)
            img3d, cax, qsrc = build_mode_and_caxis(
                self.d, mode, float(self.sp_w.value()),
                use_mask=bool(self.chk_mask.isChecked()),
                caxis_mode='manual', caxis=cax0, layers=None,
                ld_mode=str(self.cb_ld.currentText()), verbose=False)
        except Exception as e:                                   # noqa: BLE001
            self.img3d = None
            self.qsrc = None
            self.layers.blockSignals(True)
            self.layers.clear()
            self.layers.blockSignals(False)
            self.canvas.set_image(None, 'gray')
            self.lab_hint.setText(f'这个模式现在出不了图：{e}')
            self._refresh_info()
            return
        self.img3d = img3d
        self.qsrc = qsrc
        self.caxis = cax if cax is not None else self._auto_pair()
        if cax is None:
            self.lab_hint.setText('该图类型没给固定色标 → 已按「非 0 体素 5~95%」自动取。')
        else:
            self.lab_hint.setText(f'色标按 ② 的口径：{cax}（可随时改/点自动）。')
        self._set_cax_edits()
        keep = self.layer if not first else 0
        self._refresh_layers()
        self.layers.setCurrentRow(min(keep, self.n_layer - 1))
        self._show_layer(self.layers.currentRow())
        self._refresh_info()

    def _auto_pair(self):
        """在「img .* Mask（背景=0）」上取非 0 体素的 5~95%（= MATLAB 口径）。"""
        v = np.asarray(getattr(self, 'qsrc', self.img3d), dtype=float)
        v = v[np.isfinite(v) & (v != 0)]
        if v.size:
            return (float(np.percentile(v, 5)), float(np.percentile(v, 95)))
        return (float(np.nanmin(self.img3d)), float(np.nanmax(self.img3d)))

    def _set_cax_edits(self):
        lo, hi = self.caxis or (None, None)
        self.ed_lo.blockSignals(True)
        self.ed_hi.blockSignals(True)
        self.ed_lo.setText('' if lo is None else f'{lo:.6g}')
        self.ed_hi.setText('' if hi is None else f'{hi:.6g}')
        self.ed_lo.blockSignals(False)
        self.ed_hi.blockSignals(False)

    def _cur_clim(self):
        lo, hi = _f(self.ed_lo.text()), _f(self.ed_hi.text())
        if lo is None or hi is None or hi <= lo:
            lo, hi = self.caxis if self.caxis else self._auto_pair()
        return float(lo), float(hi)

    def _apply_clim(self):
        self.caxis = (_f(self.ed_lo.text()), _f(self.ed_hi.text()))
        self.canvas.set_clim(*self._cur_clim())

    def _auto_cax(self):
        self.caxis = self._auto_pair()
        self._set_cax_edits()
        self.canvas.set_clim(*self.caxis)
        self.lab_hint.setText(f'已自动取「非 0 体素 5~95%」= {self.caxis}')

    # ================================================================== 层览 / 画布
    def _refresh_layers(self):
        if self.img3d is None:
            return
        cur = self.layers.currentRow()
        self.layers.blockSignals(True)
        self.layers.clear()
        for k in range(self.n_layer):
            it = QListWidgetItem(QIcon(QPixmap.fromImage(to_qimage(self.img3d[:, :, k], 'gray'))),
                                 f'层 {k + 1}')
            it.setData(Qt.ItemDataRole.UserRole, k)
            self.layers.addItem(it)
        self.layers.blockSignals(False)
        self.layers.setCurrentRow(min(max(cur, 0), self.n_layer - 1))

    def _on_layer(self, row):
        if row < 0:
            return
        self.layer = int(row)
        self._show_layer(self.layer)

    def _show_layer(self, row):
        if self.img3d is None or row < 0:
            return
        lo, hi = self._cur_clim()
        self.canvas.set_image(self.img3d[:, :, int(row)], self._cmap_key(),
                              keep_view=True, vmin=lo, vmax=hi)
        self.lab_layer.setText(f'共 {self.n_layer} 层\n当前：层 {int(row) + 1}')

    def _refresh_info(self):
        mode = self._mode()
        w = float(self.sp_w.value())
        _wax = self.d.get('w')
        n_freq = (len(np.asarray(_wax).ravel()) if _wax is not None else 0)
        self.lab_info.setText(f'{self.case}　{self.n_layer} 层　{n_freq} 个频偏')
        name = self._target_name(mode, w)
        out = str(self.out_dir) if self.out_dir else '（未选 PROJ）'
        self.lab_out.setText(f'目录：{out}\n'
                             f'文件：{name}.png / .mat / .json\n'
                             f'（同名会先备份 .bak_<时间戳>）')

    def _target_name(self, mode, w):
        try:
            from app.modules.m2_display import combined_name
            return combined_name(mode, w)
        except Exception:                                        # noqa: BLE001
            return (f'{mode} {w}ppm').replace('.', '_') + '_combined'

    # ================================================================== 导出
    def _do_export(self):
        if not callable(self.on_export):
            QMessageBox.information(self, '不能导出', '没有配置导出（请从 ② 页打开本窗口）。')
            return
        if self.img3d is None:
            return
        mode = self._mode()
        w = float(self.sp_w.value())
        cax = self._cur_clim()
        try:
            paths = self.on_export(self.img3d, mode, w, cax,
                                   int(self.sp_cols.value()), int(self.sp_dpi.value()))
        except Exception as e:                                   # noqa: BLE001
            QMessageBox.warning(self, '导出失败', f'{type(e).__name__}: {e}')
            return
        QMessageBox.information(self, '已导出',
                                '已写出：\n' + '\n'.join(str(x) for x in paths))
