# -*- coding: utf-8 -*-
"""app/dialogs/ortho_view_dialog.py —— ② 三正交面视图（横断 / 冠状 / 矢状）。

    ┌ 横断面（X×Y，切在第 z 层） ┐ ┌ 冠状面（Z×X，切在 y） ┐
    ├ 矢状面（Z×Y，切在 x）     ┤ ├ 信息 / 导出 ───────── ┤

★ 取数与 ②【开始】**同源**：都走 build_mode_and_caxis（→ cest_display.build_mode_data），
  拿到的 img3d 就是 (X, Y, 层)。本窗口只把同一个三维体沿三个轴各切一刀：

      横断面 = vol[:, :, z]        → (X, Y)   行=X、列=Y
      冠状面 = vol[:, y, :].T      → (Z, X)   行=Z、列=X（头脚竖着）
      矢状面 = vol[x, :, :].T      → (Z, Y)   行=Z、列=Y（头脚竖着）

★ 只切面不重算：改 x/y/z 只重新切片（毫秒级）；只有换图类型 / 频偏 / 掩模才重算体积。
★ 十字线联动：在任一张图上点一下，另外两张自动跳到该坐标。
★ 方向不靠猜：给「水平翻转 / 垂直翻转」，看着不对就勾（只影响显示，不动数据）。
★ 导出：在输出目录下**新建一个文件夹**（`{MODE} {w}ppm_ortho\`，同名已存在则加时间戳），
  里面写 4 张 PNG：三视图合成 1 张 + 横断 / 冠状 / 矢状 各 1 张。
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
                               QFormLayout, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget)

from app.widgets.mask_canvas import SliceCanvas, to_qimage

# 预览色图（导出跟当前所见一致；②【开始】落盘仍固定 jet 首行黑）
CMAP_VIEW = (('jet（首行黑）', 'jet_black1'), ('灰度', 'gray'), ('彩色 viridis', 'viridis'))
MODES = ('raw', 'ST', 'APTW', 'LD')

CROSS_COLOR = '#facc15'

# 导出时除了四格合成图，再单独存这三个面
PLANE_FILES = (('ax', '横断面'), ('co', '冠状面'), ('sa', '矢状面'))


def _f(s):
    try:
        t = str(s).strip()
        return None if t == '' else float(t)
    except Exception:                                            # noqa: BLE001
        return None


# ===========================================================================
# 画布：只读 + 十字线 + 点击取值（不改 mask_canvas.SliceCanvas 的任何行为）
# ===========================================================================
class OrthoCanvas(SliceCanvas):
    """在 SliceCanvas 上加「十字线」「点一下取坐标」「横/纵向拉伸」，其余沿用父类。

    ★ hscale / vscale 只解决一件事：CEST 的层厚通常远大于像素间距，若按等方画，
      32 层的冠状面会被压成一条扁带。拉伸只改显示，不动数据、不影响导出数值。
    对外一律用「原始图像坐标」，canvas 内部负责 ×/÷ 倍数。
    """

    clicked = Signal(float, float)          # 原始图像坐标 (col, row)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.readonly = True                # 只看不画
        self.tool = 'select'
        self.show_overlays = False
        self.mask = None
        self.cross = None                   # (col, row) 原始坐标
        self.vscale = 1.0
        self.hscale = 1.0
        self.setCursor(Qt.CursorShape.CrossCursor)

    def set_vscale(self, k):
        self.vscale = max(0.05, float(k))
        self.update()

    def set_hscale(self, k):
        self.hscale = max(0.05, float(k))
        self.update()

    def _disp(self):
        if self.img is None:
            return 1.0, 0.0, 0.0
        h, w = self.img.shape[:2]
        vw = float(w) * self.hscale
        vh = float(h) * self.vscale
        s = min(self.width() / vw, self.height() / vh) * 0.97 * self._zoom
        cx = self.width() / 2.0 + self._off.x()
        cy = self.height() / 2.0 + self._off.y()
        return s, cx - vw * s / 2.0, cy - vh * s / 2.0

    def _img_rect(self):
        s, x0, y0 = self._disp()
        h, w = self.img.shape[:2]
        return QRectF(x0, y0, w * self.hscale * s, h * self.vscale * s)

    def to_img(self, pos) -> tuple:
        s, x0, y0 = self._disp()
        return ((pos.x() - x0) / (s * self.hscale), (pos.y() - y0) / (s * self.vscale))

    def to_widget(self, col, row) -> QPointF:
        s, x0, y0 = self._disp()
        return QPointF(x0 + col * self.hscale * s, y0 + row * self.vscale * s)

    def set_cross(self, col, row):
        self.cross = None if col is None else (float(col), float(row))
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor('#0b1220'))
        if self.img is None:
            p.setPen(QColor('#94a3b8'))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       '还没有底图\n（先选好输入 / 病例再打开）')
            p.end()
            return
        if self._qi is None:
            self._qi = to_qimage(self.img, self.cmap, self.vmin, self.vmax)
        rect = self._img_rect()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        p.drawImage(rect, self._qi)

        if self.cross is not None:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            q = self.to_widget(self.cross[0], self.cross[1])
            pen = QPen(QColor(CROSS_COLOR), 1.2)
            pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawLine(QPointF(rect.left(), q.y()), QPointF(rect.right(), q.y()))
            p.drawLine(QPointF(q.x(), rect.top()), QPointF(q.x(), rect.bottom()))

        p.setPen(QPen(QColor('#334155')))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        p.end()

    def mousePressEvent(self, e):
        if (self.img is not None and e.button() == Qt.MouseButton.LeftButton
                and self._pan is None):
            col, row = self.to_img(e.position())
            h, w = self.img.shape[:2]
            if -0.5 <= col <= w - 0.5 and -0.5 <= row <= h - 0.5:
                self.clicked.emit(float(col), float(row))
        super().mousePressEvent(e)

    def wheelEvent(self, e):
        """拉伸后横向/纵向的比例不同，缩放要分开算，否则光标下的点会漂。"""
        if self.img is None:
            return
        k = max(0.3, min(8.0, 1.0015 ** e.angleDelta().y()))
        before = self.to_img(e.position())
        self._zoom *= k
        after = self.to_img(e.position())
        s, _x0, _y0 = self._disp()
        self._off += QPointF((after[0] - before[0]) * s * self.hscale,
                             (after[1] - before[1]) * s * self.vscale)
        self.update()


# ===========================================================================
# 一个切面：标题 + 画布 + 索引滑条
# ===========================================================================
class _Plane(QWidget):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        self.lab_title = QLabel(title)
        self.lab_title.setObjectName('muted')
        v.addWidget(self.lab_title)

        self.canvas = OrthoCanvas()
        self.canvas.setMinimumSize(240, 200)
        v.addWidget(self.canvas, 1)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.spin = QSpinBox()
        self.spin.setRange(1, 1)
        self.spin.setFixedWidth(72)
        self.spin.setKeyboardTracking(False)          # 输完按回车 / 离开焦点才跳
        self.spin.setToolTip('直接填第几层 / 第几列（回车生效）；滑条会跟着走')
        self.lab_idx = QLabel('-')
        self.lab_idx.setObjectName('muted')
        self.lab_idx.setMinimumWidth(40)
        row.addWidget(self.slider, 1)
        row.addWidget(self.spin, 0)
        row.addWidget(self.lab_idx, 0)
        v.addLayout(row)


# ===========================================================================
# 主对话框
# ===========================================================================
class OrthoViewDialog(QDialog):
    """三正交面预览：左右上下随便改，满意了导出。"""

    def __init__(self, parent, data, case='', params=None, out_dir=None):
        super().__init__(parent)
        self.setWindowTitle(f'② 三正交面视图　—　{case or "（未命名）"}')
        self.resize(1480, 920)

        self.d = data
        _img = data['img_interp']
        _shape = tuple(int(v) for v in np.shape(_img))
        if len(_shape) != 4:
            raise ValueError(f'需要 4D (X,Y,层,频) 数据，收到 {_shape}')
        # ★ 不整本 np.asarray：大文件是 Lazy4D，取一次就等于把 2.4 GB 搬一遍
        self.img4 = _img
        self.n_freq = _shape[3]
        self.nx, self.ny, self.nz = _shape[0], _shape[1], _shape[2]
        self.case = case
        self.params = dict(params or {})
        self.out_dir = out_dir

        # 当前切面位置（0 基）
        self.ix = self.nx // 2
        self.iy = self.ny // 2
        self.iz = self.nz // 2

        self.vol = None          # (X, Y, Z) 已选频偏/已压掩模的三维体
        self.qsrc = None         # 自适应色标的取数源（背景=0）
        self.caxis = None
        self._raw = {}           # 未翻转的三个切面
        self._buf = {}           # 已翻转（= 显示）的三个切面

        self._build()
        self._reload(first=True)

    # ================================================================== UI
    def _build(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # ---------------- 顶部：数据 / 显示 / 导出 ----------------
        top = QGroupBox('数据与显示')
        t = QVBoxLayout(top)
        t.setSpacing(6)

        r1 = QHBoxLayout()
        r1.setSpacing(6)

        self.cb_mode = QComboBox()
        self.cb_mode.addItems(list(MODES))
        _m0 = str(self.params.get('mode') or 'APTW').upper()
        for _i in range(self.cb_mode.count()):
            if self.cb_mode.itemText(_i).upper() == _m0:
                self.cb_mode.setCurrentIndex(_i)
                break
        self.cb_mode.setToolTip('图类型：raw / ST / APTW / LD（与 ② 同口径）')
        self.cb_mode.currentTextChanged.connect(lambda _t: self._reload())

        self.sp_w = QDoubleSpinBox()
        self.sp_w.setRange(-12, 12)
        self.sp_w.setSingleStep(0.1)
        self.sp_w.setDecimals(2)
        self.sp_w.setValue(float(self.params.get('w') or 3.5))
        self.sp_w.setToolTip('目标频偏（ppm）。改这里 = 换成另一个频率的三维体')
        self.sp_w.valueChanged.connect(lambda _v: self._reload())

        self.cb_ld = QComboBox()
        self.cb_ld.addItems(['POS', 'NEG'])
        self.cb_ld.setCurrentText(str(self.params.get('ld_mode') or 'POS'))
        self.cb_ld.setToolTip('只在 LD 生效：POS = +目标ppm；NEG = -目标ppm')
        self.cb_ld.currentTextChanged.connect(lambda _t: self._reload())

        self.chk_mask = QCheckBox('用掩模压背景')
        self.chk_mask.setChecked(bool(self.params.get('use_mask', True)))
        self.chk_mask.setToolTip('口径 .*Mask - (~Mask)：掩模外压成 -1（与 ② 一致）')
        self.chk_mask.toggled.connect(lambda _v: self._reload())

        for cap, wdg in (('图类型', self.cb_mode), ('目标 ppm', self.sp_w),
                         ('LD 正负', self.cb_ld)):
            r1.addWidget(QLabel(cap))
            r1.addWidget(wdg)
        r1.addWidget(self.chk_mask)

        r1.addSpacing(10)
        self.ed_lo = QLineEdit('')
        self.ed_hi = QLineEdit('')
        for _e in (self.ed_lo, self.ed_hi):
            _e.setFixedWidth(88)
        self.ed_lo.textChanged.connect(lambda _t: self._apply_clim())
        self.ed_hi.textChanged.connect(lambda _t: self._apply_clim())
        b_auto = QPushButton('自动')
        b_auto.setObjectName('pick')
        b_auto.setToolTip('按「img .* Mask（背景=0）」非 0 体素取 5~95%（= MATLAB 口径）')
        b_auto.clicked.connect(self._auto_cax)
        r1.addWidget(QLabel('色标'))
        r1.addWidget(self.ed_lo)
        r1.addWidget(QLabel('~'))
        r1.addWidget(self.ed_hi)
        r1.addWidget(b_auto)

        self.cb_cmap = QComboBox()
        self.cb_cmap.addItems([c[0] for c in CMAP_VIEW])
        self.cb_cmap.setToolTip('只影响看图的色图（导出跟这里一致）')
        self.cb_cmap.currentTextChanged.connect(lambda _t: self._refresh(keep_view=True))
        r1.addWidget(QLabel('色图'))
        r1.addWidget(self.cb_cmap)
        r1.addStretch(1)
        t.addLayout(r1)

        r2 = QHBoxLayout()
        r2.setSpacing(6)
        self.chk_fh = QCheckBox('水平翻转')
        self.chk_fv = QCheckBox('垂直翻转')
        for c in (self.chk_fh, self.chk_fv):
            c.setToolTip('方向看着不对就勾上（只改显示，不动数据、不影响导出的数值）')
            c.toggled.connect(lambda _v: self._refresh(keep_view=True))
            r2.addWidget(c)

        b_reset = QPushButton('回到中心')
        b_reset.setToolTip('三个切面都跳到体积正中')
        b_reset.clicked.connect(self._to_center)
        r2.addWidget(b_reset)

        self.sp_z = QDoubleSpinBox()
        self.sp_z.setRange(0.2, 12.0)
        self.sp_z.setSingleStep(0.5)
        self.sp_z.setDecimals(1)
        self.sp_z.setValue(1.0)
        self.sp_z.setFixedWidth(64)
        self.sp_z.setToolTip('纵向拉伸倍数（只影响显示）。\n'
                             '层厚通常远大于像素间距，按 1.0 画会显得很扁 —— 觉得扁就调大。')
        self.sp_z.valueChanged.connect(lambda _v: self._refresh(keep_view=True))
        r2.addWidget(QLabel('纵向拉伸'))
        r2.addWidget(self.sp_z)

        self.sp_x = QDoubleSpinBox()
        self.sp_x.setRange(0.2, 12.0)
        self.sp_x.setSingleStep(0.5)
        self.sp_x.setDecimals(1)
        self.sp_x.setValue(1.0)
        self.sp_x.setFixedWidth(64)
        self.sp_x.setToolTip('横向拉伸倍数（只影响显示）。\n'
                             '觉得左右方向太窄/太宽就调它，不动数据。')
        self.sp_x.valueChanged.connect(lambda _v: self._refresh(keep_view=True))
        r2.addWidget(QLabel('横向拉伸'))
        r2.addWidget(self.sp_x)

        r2.addStretch(1)
        self.lab_hint = QLabel('')
        self.lab_hint.setObjectName('muted')
        self.lab_hint.setWordWrap(True)
        r2.addWidget(self.lab_hint, 1)
        t.addLayout(r2)

        # ---- 第二行：频率（帧）相关。ppm 不常换，所以放在单独一行，跟拉伸区分开 ----
        r3 = QHBoxLayout()
        r3.setSpacing(6)
        self.cb_frame = QComboBox()
        self.cb_frame.setMinimumWidth(300)
        self.cb_frame.setToolTip('直接选频点（不靠 ppm 猜最近帧）。'
                                 'ppm 输入框会跟着这个同步。')
        self.cb_frame.currentIndexChanged.connect(self._on_frame_combo)
        r3.addWidget(QLabel('频点'))
        r3.addWidget(self.cb_frame, 1)

        b_prev = QPushButton('◀')
        b_prev.setFixedWidth(34)
        b_prev.setToolTip('上一个频点')
        b_prev.clicked.connect(lambda: self._step_frame(-1))
        b_next = QPushButton('▶')
        b_next.setFixedWidth(34)
        b_next.setToolTip('下一个频点')
        b_next.clicked.connect(lambda: self._step_frame(+1))
        r3.addWidget(b_prev)
        r3.addWidget(b_next)
        t.addLayout(r3)

        root.addWidget(top)

        # ---------------- 中部：2×2 ----------------
        grid = QGridLayout()
        grid.setSpacing(8)

        self.p_ax = _Plane('横断面（X × Y，切在第 z 层）')
        self.p_co = _Plane('冠状面（Z × X，切在 y）')
        self.p_sa = _Plane('矢状面（Z × Y，切在 x）')
        grid.addWidget(self.p_ax, 0, 0)
        grid.addWidget(self.p_co, 0, 1)
        grid.addWidget(self.p_sa, 1, 0)

        # 第 4 格：信息 + 导出
        g_out = QGroupBox('当前所见')
        fo = QFormLayout(g_out)
        self.lab_dim = QLabel('-')
        self.lab_dim.setWordWrap(True)
        self.lab_pos = QLabel('-')
        self.lab_pos.setWordWrap(True)
        self.lab_file = QLabel('-')
        self.lab_file.setWordWrap(True)
        fo.addRow('数据', self.lab_dim)
        fo.addRow('切面', self.lab_pos)
        fo.addRow('导出', self.lab_file)
        self.btn_export = QPushButton('导出三视图 PNG')
        self.btn_export.setObjectName('primary')
        self.btn_export.clicked.connect(self._do_export)
        fo.addRow(self.btn_export)
        grid.addWidget(g_out, 1, 1)

        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)
        grid.setRowStretch(0, 1)
        grid.setRowStretch(1, 1)
        root.addLayout(grid, 1)

        # ---------------- 底部 ----------------
        bot = QHBoxLayout()
        tip = QLabel('滚轮缩放 · 中键拖动平移 · 在任一张图上点一下，另外两张自动跳到该位置。'
                     '改 x/y/z 只重新切片，不会重算数据。')
        tip.setObjectName('muted')
        tip.setWordWrap(True)
        bot.addWidget(tip, 1)
        b_close = QPushButton('关闭')
        b_close.clicked.connect(self.accept)
        bot.addWidget(b_close)
        root.addLayout(bot)

        # ---- 连线：滑条 / 点击 ----
        self.p_ax.slider.valueChanged.connect(self._on_sl_ax)
        self.p_co.slider.valueChanged.connect(self._on_sl_co)
        self.p_sa.slider.valueChanged.connect(self._on_sl_sa)
        self.p_ax.spin.valueChanged.connect(lambda v: self._on_spin('ax', v))
        self.p_co.spin.valueChanged.connect(lambda v: self._on_spin('co', v))
        self.p_sa.spin.valueChanged.connect(lambda v: self._on_spin('sa', v))
        self.p_ax.canvas.clicked.connect(lambda c, r: self._on_click('ax', c, r))
        self.p_co.canvas.clicked.connect(lambda c, r: self._on_click('co', c, r))
        self.p_sa.canvas.clicked.connect(lambda c, r: self._on_click('sa', c, r))

        self._ensure_frame_combo()

    # ================================================================== 取数
    def _mode(self):
        return str(self.cb_mode.currentText()).upper()

    def _cmap_key(self):
        t = self.cb_cmap.currentText()
        for label, key in CMAP_VIEW:
            if label == t:
                return key
        return 'jet_black1'

    def _default_caxis(self, mode):
        lo, hi = _f(self.params.get('caxis_lo')), _f(self.params.get('caxis_hi'))
        if lo is not None and hi is not None:
            return (lo, hi)
        try:
            from app.modules.m2_display import DEFAULT_CAXIS
            return DEFAULT_CAXIS.get(mode)
        except Exception:                                        # noqa: BLE001
            return None

    def _auto_pair(self, qsrc=None):
        """在「img .* Mask（背景=0）」上取非 0 体素 5~95%（= MATLAB 口径）。"""
        v = np.asarray(qsrc if qsrc is not None else self.qsrc, dtype=float)
        v = v[np.isfinite(v) & (v != 0)]
        if v.size:
            return (float(np.percentile(v, 5)), float(np.percentile(v, 95)))
        return None

    def _reload(self, first=False):
        """换图类型 / 频偏 / 掩模 → 重算三维体；切面位置尽量保留。

        ★ 大文件走 Lazy4D：这里只会读当前频点那一帧（约 10 MB），
          不会把 2.4 GB 的 4D 整本搬进内存。
        """
        from app.modules.m2_display import build_mode_and_caxis

        mode = self._mode()
        self.cb_ld.setEnabled(mode == 'LD')
        try:
            vol, cax, qsrc = build_mode_and_caxis(
                self.d, mode, float(self.sp_w.value()),
                use_mask=bool(self.chk_mask.isChecked()),
                caxis_mode='manual', caxis=self._default_caxis(mode), layers=None,
                ld_mode=str(self.cb_ld.currentText()), verbose=False)
        except Exception as e:                                   # noqa: BLE001
            self.vol = None
            for _p in (self.p_ax, self.p_co, self.p_sa):
                _p.canvas.set_image(None, 'gray')
                _p.canvas.set_cross(None, None)
            self.lab_hint.setText(f'这个组合现在出不了图：{e}')
            return

        self.vol = np.asarray(vol, dtype=float)
        self.qsrc = qsrc
        nz_new = int(self.vol.shape[2])
        if nz_new != self.nz:
            self.nz = nz_new
        self.caxis = cax if cax is not None else self._auto_pair(qsrc)
        self._set_cax_edits()
        self._sync_frame_combo()

        self.ix = min(self.ix if not first else self.nx // 2, self.nx - 1)
        self.iy = min(self.iy if not first else self.ny // 2, self.ny - 1)
        self.iz = min(self.iz if not first else self.nz // 2, self.nz - 1)

        if first:
            # 默认让冠状/矢状面大致填满格子（层数通常比 X/Y 少得多，按 1:1 会压成一条）
            k0 = float(np.clip(min(self.nx, self.ny) / max(1, self.nz), 1.0, 12.0))
            self.sp_z.blockSignals(True)
            self.sp_z.setValue(round(k0, 1))
            self.sp_z.blockSignals(False)

        self.p_ax.slider.setRange(0, max(0, self.nz - 1))
        self.p_co.slider.setRange(0, max(0, self.ny - 1))
        self.p_sa.slider.setRange(0, max(0, self.nx - 1))
        self._sync_sliders()

        self.lab_hint.setText(
            f'色标 {"（固定）" if cax is not None else "（自动 5~95%）"}{tuple(round(x, 6) for x in (self.caxis or ())) if self.caxis else ""}'
            '　·　拖动滑条或直接点图都能改')
        self._refresh(keep_view=not first)

    def _set_cax_edits(self):
        lo, hi = self.caxis or (None, None)
        for e, v in ((self.ed_lo, lo), (self.ed_hi, hi)):
            e.blockSignals(True)
            e.setText('' if v is None else f'{v:.6g}')
            e.blockSignals(False)

    def _cur_clim(self):
        lo, hi = _f(self.ed_lo.text()), _f(self.ed_hi.text())
        if lo is None or hi is None or hi <= lo:
            lo, hi = self.caxis or (None, None)
        if lo is None or hi is None or hi <= lo:
            lo, hi = self._auto_pair() or (0.0, 1.0)
        return float(lo), float(hi)

    def _apply_clim(self):
        lo, hi = _f(self.ed_lo.text()), _f(self.ed_hi.text())
        if lo is not None and hi is not None and hi > lo:
            self.caxis = (lo, hi)
        self._refresh(keep_view=True)

    def _auto_cax(self):
        self.caxis = self._auto_pair()
        self._set_cax_edits()
        self._refresh(keep_view=True)

    # ================================================================== 切片 / 显示
    def _cuts(self):
        v = self.vol
        ax = v[:, :, self.iz]                    # (X, Y)　行=X 列=Y
        co = v[:, self.iy, :].T                  # (Z, X)　行=Z 列=X
        sa = v[self.ix, :, :].T                  # (Z, Y)　行=Z 列=Y
        return ax, co, sa

    def _flip(self, a):
        if self.chk_fh.isChecked():
            a = a[:, ::-1]
        if self.chk_fv.isChecked():
            a = a[::-1, :]
        return np.ascontiguousarray(a)

    def _map_xy(self, shape, col, row):
        """逻辑坐标 ⇄ 显示坐标（翻转是自反的，正反都用这一个）。"""
        h, w = shape
        c = (w - 1 - col) if self.chk_fh.isChecked() else col
        r = (h - 1 - row) if self.chk_fv.isChecked() else row
        return c, r

    def _refresh(self, keep_view=True):
        if self.vol is None:
            return
        ax, co, sa = self._cuts()
        self._raw = {'ax': ax, 'co': co, 'sa': sa}
        try:
            lo, hi = self._cur_clim()
        except Exception:                                        # noqa: BLE001
            lo, hi = (None, None)
        cmap = self._cmap_key()
        kz = float(self.sp_z.value())
        kx = float(self.sp_x.value())
        plan = (
            ('ax', self.p_ax, ax, (self.iy, self.ix), kx, 1.0),    # col=Y，row=X
            ('co', self.p_co, co, (self.ix, self.iz), kx, kz),     # col=X，row=Z
            ('sa', self.p_sa, sa, (self.iy, self.iz), 1.0, kz),    # col=Y，row=Z
        )
        for key, plane, arr, (lc, lr), kh, kv in plan:
            d = self._flip(arr)
            self._buf[key] = d
            plane.canvas.set_hscale(kh)
            plane.canvas.set_vscale(kv)
            plane.canvas.set_image(d, cmap, keep_view=keep_view, vmin=lo, vmax=hi)
            c, r = self._map_xy(d.shape, lc, lr)
            plane.canvas.set_cross(c, r)
        self._refresh_labels()

    def _refresh_labels(self):
        for plane, v, n in ((self.p_ax, self.iz, self.nz), (self.p_co, self.iy, self.ny),
                            (self.p_sa, self.ix, self.nx)):
            plane.lab_idx.setText(f'/ {n}')
            plane.spin.blockSignals(True)
            plane.spin.setRange(1, max(1, int(n)))
            plane.spin.setValue(int(v) + 1)
            plane.spin.blockSignals(False)
        self.lab_dim.setText(f'{self.case}　{self.nx} × {self.ny} × {self.nz}'
                             f'（X × Y × 层）　{int(self.n_freq)} 个频点')
        self.lab_pos.setText(f'横断 层 {self.iz + 1}　｜　冠状 Y {self.iy + 1}'
                             f'　｜　矢状 X {self.ix + 1}')
        if self.out_dir:
            self.lab_file.setText(
                f'新建文件夹 {self._target_name()}\\　共 4 张：\n'
                f'　三视图合成 + 横断/冠状/矢状 各一张\n→ {self.out_dir}')
        else:
            self.lab_file.setText('（未选 PROJ，不能导出）')

    def _target_name(self):
        mode = self._mode()
        w = float(self.sp_w.value())
        return (f'{mode} {w:g}ppm').replace('.', '_') + '_ortho'

    # ================================================================== 交互
    def _ensure_frame_combo(self):
        """把频点列进下拉框（能直接选帧，不用靠 ppm 猜最近帧）。"""
        w = self.d.get('w')
        if w is None:
            self._w_arr = None
            self.cb_frame.addItem('（没有 w_offset.mat，只能按 ppm 取最近帧）')
            self.cb_frame.setEnabled(False)
            return
        wa = np.asarray(w, float).ravel()
        self._w_arr = wa
        self.cb_frame.blockSignals(True)
        self.cb_frame.clear()
        for i, v in enumerate(wa):
            self.cb_frame.addItem(f'{i + 1}／{wa.size}　{v:+.2f} ppm')
        k = int(np.clip(np.argmin(np.abs(wa - float(self.sp_w.value()))), 0, wa.size - 1))
        self.cb_frame.setCurrentIndex(k)
        self.cb_frame.blockSignals(False)

    def _sync_frame_combo(self):
        if getattr(self, '_w_arr', None) is None or not self.cb_frame.count():
            return
        k = int(np.clip(np.argmin(np.abs(self._w_arr - float(self.sp_w.value()))),
                        0, self.cb_frame.count() - 1))
        if k != self.cb_frame.currentIndex():
            self.cb_frame.blockSignals(True)
            self.cb_frame.setCurrentIndex(k)
            self.cb_frame.blockSignals(False)

    def _on_frame_combo(self, i):
        if i < 0 or getattr(self, '_w_arr', None) is None:
            return
        self.sp_w.blockSignals(True)
        self.sp_w.setValue(float(self._w_arr[i]))
        self.sp_w.blockSignals(False)
        self._reload()

    def _step_frame(self, d):
        if not self.cb_frame.isEnabled() or not self.cb_frame.count():
            return
        k = int(np.clip(self.cb_frame.currentIndex() + int(d),
                        0, self.cb_frame.count() - 1))
        self.cb_frame.setCurrentIndex(k)

    def _sync_sliders(self):
        for plane, v, n in ((self.p_ax, self.iz, self.nz), (self.p_co, self.iy, self.ny),
                            (self.p_sa, self.ix, self.nx)):
            plane.slider.blockSignals(True)
            plane.slider.setValue(int(v))
            plane.slider.blockSignals(False)
            plane.spin.blockSignals(True)
            plane.spin.setRange(1, max(1, int(n)))
            plane.spin.setValue(int(v) + 1)
            plane.spin.blockSignals(False)

    def _on_sl_ax(self, v):
        self.iz = int(v)
        self._refresh(keep_view=True)

    def _on_sl_co(self, v):
        self.iy = int(v)
        self._refresh(keep_view=True)

    def _on_sl_sa(self, v):
        self.ix = int(v)
        self._refresh(keep_view=True)

    def _on_spin(self, key, v):
        """输入框直接填数字定位（滑条同步）。"""
        i = int(v) - 1
        if key == 'ax':
            self.iz = int(np.clip(i, 0, self.nz - 1))
        elif key == 'co':
            self.iy = int(np.clip(i, 0, self.ny - 1))
        else:
            self.ix = int(np.clip(i, 0, self.nx - 1))
        self._sync_sliders()
        self._refresh(keep_view=True)

    def _on_click(self, plane, col, row):
        d = self._buf.get(plane)
        if d is None:
            return
        h, w = d.shape
        lc, lr = self._map_xy(d.shape, float(col), float(row))
        lc = int(np.clip(round(lc), 0, w - 1))
        lr = int(np.clip(round(lr), 0, h - 1))
        if plane == 'ax':
            self.iy, self.ix = lc, lr
        elif plane == 'co':
            self.ix, self.iz = lc, lr
        else:   # sa
            self.iy, self.iz = lc, lr
        self._sync_sliders()
        self._refresh(keep_view=True)

    def _to_center(self):
        self.ix, self.iy, self.iz = self.nx // 2, self.ny // 2, self.nz // 2
        self._sync_sliders()
        self._refresh(keep_view=True)

    # ================================================================== 导出
    def _do_export(self):
        if self.vol is None or not self._buf:
            return
        if not self.out_dir:
            QMessageBox.information(self, '不能导出', '没有输出目录（请从 ② 页打开本窗口）。')
            return
        try:
            combo = self._compose_png()
            singles = [(name, self._compose_single(key)) for key, name in PLANE_FILES]
        except Exception as e:                                   # noqa: BLE001
            QMessageBox.warning(self, '导出失败', f'{type(e).__name__}: {e}')
            return

        # ★ 每次导出新建一个文件夹（同名已存在就加时间戳），三视图 + 三个单面图都放里面
        base = Path(self.out_dir) / self._target_name()
        out = base
        if out.exists():
            out = base.with_name(f'{base.name}_{time.strftime("%Y%m%d_%H%M%S")}')
        try:
            out.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, '导出失败', f'建不了文件夹：{out}\n{e}')
            return

        files = [(out / f'{self._target_name()}.png', combo)]
        files += [(out / f'{name}.png', qi) for name, qi in singles]
        bad = [str(f) for f, qi in files if not qi.save(str(f), 'PNG')]
        if bad:
            QMessageBox.warning(self, '导出失败', '写不进：\n' + '\n'.join(bad))
            return
        self.lab_file.setText(f'{out.name}\\（{len(files)} 个文件）\n→ {self.out_dir}')
        QMessageBox.information(
            self, '已导出',
            f'已新建文件夹并写入 {len(files)} 个文件：\n{out}\n\n'
            + '\n'.join(f'· {f.name}' for f, _ in files))

    def _compose_single(self, key):
        """单个切面导出一张（带标题 + 色标条），拉伸与当前所见一致。"""
        cell = 460
        pad = 28
        head = 74
        foot = 92
        scale = 3
        W = cell + pad * 2
        H = cell + pad * 2 + head + foot
        img = QImage(W * scale, H * scale, QImage.Format.Format_RGB32)
        img.fill(QColor('#ffffff'))
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.scale(scale, scale)

        f_title = QFont('Microsoft YaHei', 15, QFont.Weight.Bold)
        f_sub = QFont('Microsoft YaHei', 10)

        _key, title, kh, kv = self._plane_info(key)
        p.setPen(QColor('#111827'))
        p.setFont(f_title)
        p.drawText(QRectF(pad, 10, W - pad * 2, 30),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
        p.setFont(f_sub)
        p.setPen(QColor('#475569'))
        p.drawText(QRectF(pad, 40, W - pad * 2, 24),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f'{self.case}　{self._mode()} {float(self.sp_w.value()):g} ppm')

        arr = self._buf.get(key)
        lo, hi = self._cur_clim()
        x = pad
        y = head + pad
        p.setPen(QPen(QColor('#d1d5db'), 1.0))
        p.setBrush(QColor('#0b1220'))
        p.drawRect(QRectF(x, y, cell, cell))
        if arr is not None and arr.size:
            qi = to_qimage(arr, self._cmap_key(), lo, hi)
            h, w = arr.shape
            s = min((cell - 8) / (float(w) * kh), (cell - 8) / (float(h) * kv))
            dw, dh = w * kh * s, h * kv * s
            p.drawImage(QRectF(x + (cell - dw) / 2.0, y + (cell - dh) / 2.0, dw, dh), qi)

        # 色标条
        bar = QRectF(x + 24, y + cell + 14, cell - 48, 16)
        p.drawImage(bar, to_qimage(np.linspace(0, 1, 256)[None, :], self._cmap_key(), 0, 1))
        p.setPen(QColor('#334155'))
        p.setFont(f_sub)
        p.drawText(QRectF(bar.left(), bar.bottom() + 3, bar.width() / 2, 18),
                   Qt.AlignmentFlag.AlignLeft, f'{lo:.6g}')
        p.drawText(QRectF(bar.left() + bar.width() / 2, bar.bottom() + 3,
                          bar.width() / 2, 18),
                   Qt.AlignmentFlag.AlignRight, f'{hi:.6g}')

        p.setPen(QColor('#64748b'))
        p.drawText(QRectF(pad, H - 22, W - pad * 2, 18),
                   Qt.AlignmentFlag.AlignLeft,
                   f'{time.strftime("%Y-%m-%d %H:%M:%S")}　CEST Studio ② 三正交面视图')
        p.end()
        return img

    def _plane_info(self, key):
        """(key, 标题, 横向倍数, 纵向倍数) —— 与 _refresh 的 plan 同源。"""
        kz = float(self.sp_z.value())
        kx = float(self.sp_x.value())
        if key == 'ax':
            return key, f'横断面（X × Y）　层 {self.iz + 1} / {self.nz}', kx, 1.0
        if key == 'co':
            return key, f'冠状面（Z × X）　Y {self.iy + 1} / {self.ny}', kx, kz
        return key, f'矢状面（Z × Y）　X {self.ix + 1} / {self.nx}', 1.0, kz

    def _compose_png(self):
        """三面 + 色标条合成一张白底 PNG（QPainter 自绘，不依赖 matplotlib）。"""
        cell = 460
        pad = 28
        head = 74
        foot = 54
        scale = 3
        W = cell * 2 + pad * 3
        H = cell * 2 + pad * 3 + head + foot
        img = QImage(W * scale, H * scale, QImage.Format.Format_RGB32)
        img.fill(QColor('#ffffff'))
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.scale(scale, scale)

        f_title = QFont('Microsoft YaHei', 15, QFont.Weight.Bold)
        f_sub = QFont('Microsoft YaHei', 10)
        f_lab = QFont('Microsoft YaHei', 11)

        # 标题
        p.setPen(QColor('#111827'))
        p.setFont(f_title)
        p.drawText(QRectF(pad, 10, W - pad * 2, 30),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f'{self._mode()}  {float(self.sp_w.value()):g} ppm　三正交面')
        p.setFont(f_sub)
        p.setPen(QColor('#475569'))
        p.drawText(QRectF(pad, 40, W - pad * 2, 24),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f'{self.case}　{self.nx} × {self.ny} × {self.nz}（X × Y × 层）'
                   f'　色标 {self._cur_clim()[0]:.6g} ~ {self._cur_clim()[1]:.6g}'
                   f'　{"用掩模压背景" if self.chk_mask.isChecked() else "不压背景"}')

        kz = float(self.sp_z.value())
        kx = float(self.sp_x.value())
        cells = (
            (0, 0, 'ax', f'横断面（X × Y）　层 {self.iz + 1} / {self.nz}', kx, 1.0),
            (0, 1, 'co', f'冠状面（Z × X）　Y {self.iy + 1} / {self.ny}', kx, kz),
            (1, 0, 'sa', f'矢状面（Z × Y）　X {self.ix + 1} / {self.nx}', 1.0, kz),
        )
        lo, hi = self._cur_clim()
        cmap = self._cmap_key()
        for r, c, key, title, kh, kv in cells:
            x = pad + c * (cell + pad)
            y = head + pad + r * (cell + pad)
            # 面板背景 + 边框
            p.setPen(QPen(QColor('#d1d5db'), 1.0))
            p.setBrush(QColor('#0b1220'))
            p.drawRect(QRectF(x, y, cell, cell))
            arr = self._buf.get(key)
            if arr is not None and arr.size:
                qi = to_qimage(arr, cmap, lo, hi)
                h, w = arr.shape
                s = min((cell - 8) / (float(w) * kh), (cell - 8) / (float(h) * kv))
                dw, dh = w * kh * s, h * kv * s
                p.drawImage(QRectF(x + (cell - dw) / 2.0, y + (cell - dh) / 2.0, dw, dh), qi)
            p.setPen(QColor('#111827'))
            p.setFont(f_lab)
            p.drawText(QRectF(x, y - 24, cell, 22),
                       Qt.AlignmentFlag.AlignCenter, title)

        # 色标条（占第 4 格的位置）
        x = pad + (cell + pad)
        y = head + pad + (cell + pad)
        bar = QRectF(x + 24, y + cell / 2.0 - 46, cell - 48, 18)
        grad = to_qimage(np.linspace(0, 1, 256)[None, :], cmap, 0, 1)
        p.setPen(QPen(QColor('#d1d5db'), 1.0))
        p.drawImage(bar, grad)
        p.setPen(QColor('#334155'))
        p.setFont(f_sub)
        p.drawText(QRectF(bar.left(), bar.bottom() + 4, bar.width() / 2, 20),
                   Qt.AlignmentFlag.AlignLeft, f'{lo:.6g}')
        p.drawText(QRectF(bar.left() + bar.width() / 2, bar.bottom() + 4,
                          bar.width() / 2, 20),
                   Qt.AlignmentFlag.AlignRight, f'{hi:.6g}')
        p.drawText(QRectF(x + 24, bar.top() - 22, cell - 48, 20),
                   Qt.AlignmentFlag.AlignLeft, '色标')

        # 页脚
        p.setPen(QColor('#64748b'))
        p.setFont(f_sub)
        p.drawText(QRectF(pad, H - foot + 8, W - pad * 2, 20),
                   Qt.AlignmentFlag.AlignLeft,
                   f'{time.strftime("%Y-%m-%d %H:%M:%S")}　CEST Studio ② 三正交面视图'
                   f'　{"水平翻转 " if self.chk_fh.isChecked() else ""}'
                   f'{"垂直翻转" if self.chk_fv.isChecked() else ""}')
        p.end()
        return img
