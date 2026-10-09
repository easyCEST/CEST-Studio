# -*- coding: utf-8 -*-
"""app/dialogs/mask_canvas_dialog.py —— ⑧ 去噪掩模画布（交互式）。

    ┌ 左：层览(缩略) ┐ ┌ 中：精画画布 ────────────┐ ┌ 右：控制面板 ────────────┐
    │ 层 1           │ │ 灰度底图 + 已画掩模叠加   │ │ 底图: S0逐层/平均/Z@ppm  │
    │ 层 2           │ │ 多边形/矩形/画笔/橡皮     │ │ 目标: liverMask/noiseROI │
    │ …              │ │ 滚轮缩放 / 中键平移       │ │ SNR: σ · 阈值 · 算掩模    │
    └────────────────┘ └──────────────────────────┘ └──────────────────────────┘

只画「去噪掩模」这一条线（与 MATLAB denoise_Mask / GenerateThmask /
GenerateliverMask / runCESTpost 的 4.1 节口径一致）：

    snrMask   = S0平均图 > snr × std(S0平均图[噪声ROI])
    liverMask = 手画的组织/肝脏多边形
    Mask      = liverMask × snrMask          ← 最终去噪掩模

本对话框只负责「画 + 返回数组」，落盘在 app/modules/m_roi.py 的 run() 里。
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QScrollArea, QSlider, QSpinBox,
                               QVBoxLayout, QWidget)

from app.widgets.mask_canvas import (CMAP_CHOICES, COLORS, ROI_PALETTE,
                                     MaskTarget, SliceCanvas, to_qimage)

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QScrollArea, QSlider, QSpinBox,
                               QVBoxLayout, QWidget)

TOOLS = (('select', '选择/拖动'), ('polygon', '多边形'), ('rect', '矩形'),
         ('brush', '画笔'), ('eraser', '橡皮'))

# ROI 底图在「底图」下拉框里的那一项（= MATLAB roi_display_mode 1/2：归一化 Z × 掩模）
ROI_BASE_ITEM = 'ROI 底图：Z 层均 × 掩模'
# ROI 底图的显示模式（对应 MATLAB 的 roi_display_mode）
ROI_DISP_Z = 'Z 原始（APT）'      # roi_display_mode = 1
ROI_DISP_ST = '1−Z（ST）'         # roi_display_mode = 2
ROI_DISP_ITEMS = (ROI_DISP_Z, ROI_DISP_ST)


def _f(s):
    try:
        t = str(s).strip()
        return None if t == '' else float(t)
    except Exception:                                        # noqa: BLE001
        return None


class MaskCanvasDialog(QDialog):
    def __init__(self, parent, s0, case='', img_interp=None, w=None,
                 init=None, snr=15.0, default_base='S0 逐层', nroi=0,
                 roi_ppm=3.5, roi_caxis=(None, None), mask2d=None,
                 roi_z=None, roi_w=None, roi_display='Z', on_nroi=None):
        super().__init__(parent)
        self.setWindowTitle(f'⑧ 去噪掩模 + 器官 ROI 画布　—　{case or "（未命名）"}')
        self.resize(1280, 820)
        self.s0 = np.asarray(s0, dtype=float)
        while self.s0.ndim > 3:
            self.s0 = self.s0[:, :, :, 0]
        if self.s0.ndim == 2:
            self.s0 = self.s0[:, :, None]
        self.case = case
        self.img_interp = img_interp
        self.w = None if w is None else np.asarray(w, dtype=float).ravel()
        self.n_layer = int(self.s0.shape[2])
        self.shape = self.s0.shape[:2]
        self.layer = 0
        self.mean_mode = False      # 底图 = 全层平均（画共用掩模）
        self.result_data = None
        self._init = init or {}
        self._overlay_alpha = 90
        # ---- 器官 ROI（ROI_total）相关 ----
        self.nroi = int(nroi or 0)
        self.mask2d = (None if mask2d is None
                       else np.asarray(mask2d).astype(bool))
        self.roi_z = (None if roi_z is None
                      else np.asarray(roi_z, dtype=np.float32))
        self.roi_w = (None if roi_w is None
                      else np.asarray(roi_w, dtype=float).ravel())
        self._roi_ppm0 = float(roi_ppm if roi_ppm not in (None, '') else 3.5)
        _dv = str(roi_display or '').strip().upper()
        self._roi_disp0 = ROI_DISP_ST if (_dv.startswith('1') or _dv.startswith('ST')) \
            else ROI_DISP_Z
        self._roi_caxis0 = tuple(roi_caxis or (None, None))
        self.on_nroi = on_nroi     # 改 ROI 数量时回写给 ⑧ 页参数（保持两边一致）

        # ---- 掩模对象 ----
        self.targets = {
            'liverMask': MaskTarget('liverMask', '组织/肝脏', COLORS['liverMask']),
            'noiseROI': MaskTarget('noiseROI', '噪声区（供 SNR）', COLORS['noiseROI']),
            'snrMask': MaskTarget('snrMask', 'SNR 自动掩模', COLORS['snrMask'],
                                  kind='derived', editable=False),
        }
        for k, tg in self.targets.items():
            arr = (self._init.get('targets') or {}).get(k)
            if isinstance(arr, np.ndarray):
                if arr.ndim == 2:
                    tg.set(arr)
                else:
                    for z in range(min(arr.shape[2], self.n_layer)):
                        if arr[:, :, z].any():
                            tg.per_layer[z] = arr[:, :, z].astype(bool)
                    s = arr.any(axis=2)
                    if s.any():
                        tg.shared = s
        # ---- 器官 ROI 目标：ROI1 … ROIn（名称与 MATLAB 完全一致） ----
        for k in range(1, self.nroi + 1):
            key = f'ROI{k}'
            tg = MaskTarget(key, key, ROI_PALETTE[(k - 1) % len(ROI_PALETTE)],
                            kind='roi', editable=True)
            arr = None
            _rois = self._init.get('rois')
            if isinstance(_rois, dict):
                arr = _rois.get(key)
            elif isinstance(_rois, (list, tuple)):      # result_data 里是 [(name, mask), …]
                arr = next((m for n, m in _rois if n == key), None)
            if isinstance(arr, np.ndarray):
                a = np.asarray(arr).astype(bool)
                if a.ndim == 3:
                    a = a.any(axis=2)
                tg.set(a)
            self.targets[key] = tg
        # 默认停在哪一个：有掩模且要画 ROI 时直接停 ROI1，否则停 liverMask
        self._default_target_key = ('ROI1' if (self.nroi >= 1 and self.mask2d is not None)
                                    else 'liverMask')
        self._snr = float(snr)
        self._default_base = default_base
        self._build()
        if isinstance(self._init.get('ops'), dict):
            self.canvas.load_ops(self._init['ops'])   # 上次画的多边形/笔迹 → 还能接着拖
            self._materialize_ops()                   # 再把它们还原成各目标的数组
        self._reload_layer(reset_view=True)
        self._refresh_targets()
        self._apply_target_base()

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
        tb = QHBoxLayout()
        tb.setSpacing(6)
        self.tool_btns = {}
        for key, name in TOOLS:
            b = QPushButton(name)
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, k=key: self._set_tool(k))
            self.tool_btns[key] = b
            tb.addWidget(b)
        tb.addSpacing(10)
        tb.addWidget(QLabel('笔粗'))
        self.sp_brush = QSpinBox()
        self.sp_brush.setRange(1, 30)
        self.sp_brush.setValue(4)
        self.sp_brush.valueChanged.connect(lambda v: setattr(self.canvas, 'brush_r', v))
        tb.addWidget(self.sp_brush)
        b_undo = QPushButton('撤销')
        b_undo.clicked.connect(lambda: self.canvas.undo())
        b_clr = QPushButton('清空本目标')
        b_clr.clicked.connect(self._clear_target)
        b_fit = QPushButton('适应窗口')
        b_fit.clicked.connect(lambda: self.canvas.fit())
        tb.addWidget(b_undo)
        tb.addWidget(b_clr)
        tb.addWidget(b_fit)
        tb.addSpacing(10)
        # 当前工具/怎么操作 —— 一眼能看到，不用猜
        self.lab_tool = QLabel('')
        self.lab_tool.setStyleSheet('color:#1d4ed8; font-weight:600;')
        tb.addWidget(self.lab_tool)
        tb.addStretch(1)
        mv.addLayout(tb)

        self.canvas = SliceCanvas()
        self.canvas.brush_r = self.sp_brush.value()
        self.canvas.edited.connect(self._on_canvas_edited)
        self.canvas.polygon_closed.connect(self._on_polygon_closed)
        mv.addWidget(self.canvas, 1)

        hint = QLabel('上方工具排右边那行蓝字会告诉你「当前工具」怎么用。\n'
                      '画共用掩模：点层览第 1 项「★ 全层平均」画一次（20 层共用）；'
                      '某层要单独画：点那一层 + 勾「本层单独保存」。\n'
                      '画器官 ROI：在右边「掩模对象」里选 ROI1/ROI2…（底图自动切成 '
                      '「Z 层均 × 掩模」，右侧「ROI 显示」可切 Z 原始 / 1−Z(ST)）；'
                      '双击确定 → 可随时拖顶点细调。\n'
                      '滚轮缩放 / 中键拖动平移；多边形闭合后，用「选择/拖动」拖顶点或整体挪。')
        hint.setObjectName('muted')
        hint.setWordWrap(True)
        mv.addWidget(hint)
        body.addWidget(mid, 1)

        # ---------------- 右：控制面板 ----------------
        right = QScrollArea()
        right.setWidgetResizable(True)
        right.setFixedWidth(268)
        rw = QWidget()
        rv = QVBoxLayout(rw)
        rv.setSpacing(8)
        right.setWidget(rw)

        g_base = QGroupBox('底图')
        fb = QFormLayout(g_base)
        self.cb_base = QComboBox()
        self.cb_base.addItems(['S0 逐层', 'S0 层间平均'])
        if self.img_interp is not None and self.w is not None and self.w.size:
            self.cb_base.addItems(['Z @ ppm（逐层）', 'Z @ ppm（层间平均）'])
        if self.roi_z is not None and self.roi_w is not None:
            self.cb_base.addItem(ROI_BASE_ITEM)
        self.cb_base.setCurrentText(self._default_base)
        self.cb_base.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.cb_cmap = QComboBox()
        self.cb_cmap.addItems([c[0] for c in CMAP_CHOICES])
        self.cb_cmap.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.cb_cmap.setToolTip('只影响看图的色图，不影响任何计算。\n'
                                'MATLAB 那边三张图本身就是三种色图：\n'
                                '　噪声 ROI → 灰度（ThreMask_SNR.m 写了 colormap(gray)）\n'
                                '　组织/肝脏 → 彩色（GenerateliverMask.m 没设，用默认 parula）\n'
                                '　器官 ROI/出图 → jet 首行黑（colormap([0 0 0; jet(255)])）\n'
                                '切「目标」时会自动切到对应那张，也可以在这里手动改。')
        self.sp_ppm = QDoubleSpinBox()
        self.sp_ppm.setRange(-20, 20)
        self.sp_ppm.setSingleStep(0.1)
        self.sp_ppm.setValue(self._roi_ppm0)
        self.sp_ppm.valueChanged.connect(lambda _v: self._reload_layer())
        fb.addRow('底图', self.cb_base)
        fb.addRow('色图', self.cb_cmap)
        fb.addRow('目标 ppm', self.sp_ppm)
        # ---- ROI 显示模式（仅 ROI 底图；对应 MATLAB roi_display_mode 1/2） ----
        self.cb_roi_disp = QComboBox()
        self.cb_roi_disp.addItems(list(ROI_DISP_ITEMS))
        self.cb_roi_disp.setCurrentText(self._roi_disp0)
        self.cb_roi_disp.setToolTip('ROI 底图的显示模式（对应 MATLAB roi_display_mode）：\n'
                                    '　Z 原始（APT）= 归一化 Z 谱 @ 目标 ppm 的 N 层平均（mode 1）\n'
                                    '　1−Z（ST）     = 1 − 上面的 Z（mode 2）\n'
                                    '两者都是「先算图、再 × 去噪掩模」；只影响观感，\n'
                                    '不改 ROI 几何、也不改 ROI_total 的索引值。')
        self.cb_roi_disp.currentTextChanged.connect(self._roi_display_changed)
        fb.addRow('ROI 显示', self.cb_roi_disp)
        # ---- 色标：画的时候随时可改、实时刷新（只影响观感，不动 ROI 几何/索引） ----
        w_cax = QWidget()
        h_cax = QHBoxLayout(w_cax)
        h_cax.setContentsMargins(0, 0, 0, 0)
        h_cax.setSpacing(4)
        self.ed_cax_lo = QLineEdit('')
        self.ed_cax_lo.setPlaceholderText('自动')
        self.ed_cax_hi = QLineEdit('')
        self.ed_cax_hi.setPlaceholderText('自动(95%)')
        # 限宽：否则两个 QLineEdit 的默认 sizeHint 会把整排（进而整侧栏）撑得很宽
        for _e in (self.ed_cax_lo, self.ed_cax_hi):
            _e.setMinimumWidth(70)
            _e.setMaximumWidth(96)
        self.ed_cax_lo.setToolTip('ROI 底图色标下限（留空 = 自动 0）\n只影响观感，不影响 ROI 几何与索引值')
        self.ed_cax_hi.setToolTip('ROI 底图色标上限（留空 = 自动「有效值 95% 分位」，同 MATLAB）'
                                 '\n只影响观感，不影响 ROI 几何与索引值')
        b_cax = QPushButton('自动(95%分位)')
        b_cax.setObjectName('pick')
        b_cax.clicked.connect(lambda: self._auto_caxis())
        h_cax.addWidget(self.ed_cax_lo, 1)
        h_cax.addWidget(QLabel('~'))
        h_cax.addWidget(self.ed_cax_hi, 1)
        h_cax.addWidget(b_cax)
        fb.addRow('色标', w_cax)
        if self._roi_caxis0[0] is not None:
            self.ed_cax_lo.setText(f'{float(self._roi_caxis0[0]):g}')
        if self._roi_caxis0[1] is not None:
            self.ed_cax_hi.setText(f'{float(self._roi_caxis0[1]):g}')
        self.ed_cax_lo.textChanged.connect(self._apply_clim)
        self.ed_cax_hi.textChanged.connect(self._apply_clim)
        rv.addWidget(g_base)

        g_tg = QGroupBox('掩模对象')
        tv = QVBoxLayout(g_tg)
        self.lst = QListWidget()
        self.lst.setFixedHeight(110)
        self.lst.currentRowChanged.connect(self._on_target_row)
        tv.addWidget(self.lst)
        hb = QHBoxLayout()
        hb.setSpacing(6)
        self.chk_vis = QCheckBox('显示')
        self.chk_vis.setChecked(True)
        self.chk_vis.toggled.connect(self._on_vis_toggled)
        self.chk_per = QCheckBox('本层单独保存')
        self.chk_per.setToolTip('勾上后，本层的涂改只作用于这一层；其余层用「共用」那张。\n'
                                '导出时自动合成 3D (X,Y,层)。')
        self.chk_per.toggled.connect(lambda _v: self._reload_layer())
        hb.addWidget(self.chk_vis)
        hb.addWidget(self.chk_per)
        hb.addStretch(1)
        tv.addLayout(hb)
        # ROI 数量（= MATLAB nROI）——就在画 ROI 的地方直接改，改完点【应用】
        hb2 = QHBoxLayout()
        hb2.setSpacing(6)
        hb2.addWidget(QLabel('ROI 数量'))
        self.sp_nroi = QSpinBox()
        self.sp_nroi.setRange(0, 12)
        self.sp_nroi.setValue(int(self.nroi))
        self.sp_nroi.setToolTip('器官 ROI 个数（= MATLAB nROI）；改完点【应用】。\n'
                                '变多 → 新增 ROI1…；变少 → 末尾 ROI 的勾画会被丢掉。\n'
                                '与 ⑧ 页「ROI 数量」参数联动。')
        hb2.addWidget(self.sp_nroi)
        b_ap = QPushButton('应用')
        b_ap.setObjectName('pick')
        b_ap.clicked.connect(self._apply_nroi)
        hb2.addWidget(b_ap)
        hb2.addStretch(1)
        tv.addLayout(hb2)
        rv.addWidget(g_tg)

        g_snr = QGroupBox('SNR 掩模')
        fs = QFormLayout(g_snr)
        self.sp_snr = QDoubleSpinBox()
        self.sp_snr.setRange(0.1, 200)
        self.sp_snr.setValue(self._snr)
        fs.addRow('SNR 系数', self.sp_snr)
        self.sp_sigma = QDoubleSpinBox()
        self.sp_sigma.setRange(0, 1e9)
        self.sp_sigma.setDecimals(4)
        self.sp_sigma.setValue(0.0)
        self.sp_sigma.setToolTip('留 0 = 用「噪声区」里画的像素算 σ；\n'
                                 '也可以直接填 MATLAB 那边算出的 σ 复现旧结果。')
        fs.addRow('噪声 σ', self.sp_sigma)
        b_snr = QPushButton('算 SNR 掩模')
        b_snr.clicked.connect(self._calc_snr)
        b_copy = QPushButton('→ 复制成 liverMask 初值')
        b_copy.setToolTip('SNR 掩模 → 复制成 liverMask 初值\n'
                          '（对应 MATLAB「先 SNR 自动出初值，再手工修」的用法）')
        b_copy.clicked.connect(self._copy_snr_to_liver)
        fs.addRow(b_snr)
        fs.addRow(b_copy)
        self.lab_snr = QLabel('还没算。')
        self.lab_snr.setObjectName('muted')
        self.lab_snr.setWordWrap(True)
        fs.addRow(self.lab_snr)
        rv.addWidget(g_snr)

        g_ov = QGroupBox('叠加显示')
        fo = QFormLayout(g_ov)
        self.sl_alpha = QSlider(Qt.Orientation.Horizontal)
        self.sl_alpha.setRange(20, 255)
        self.sl_alpha.setValue(self.canvas.alpha)
        self.sl_alpha.valueChanged.connect(self._on_alpha)
        fo.addRow('不透明度', self.sl_alpha)
        self.chk_ov = QCheckBox('显示其它掩模')
        self.chk_ov.setChecked(True)
        self.chk_ov.toggled.connect(self._on_show_ov)
        fo.addRow(self.chk_ov)
        rv.addWidget(g_ov)
        rv.addStretch(1)
        body.addWidget(right, 0)

        root.addLayout(body, 1)

        # ---------------- 底：确定/取消 ----------------
        bot = QHBoxLayout()
        self.lab_hint = QLabel('')
        self.lab_hint.setObjectName('muted')
        self.lab_hint.setWordWrap(True)
        bot.addWidget(self.lab_hint, 1)
        b_ok = QPushButton('确定（交给 ⑧ 保存）')
        b_ok.setObjectName('primary')
        b_ok.clicked.connect(self._ok)
        b_no = QPushButton('取消')
        b_no.clicked.connect(self.reject)
        bot.addWidget(b_ok)
        bot.addWidget(b_no)
        root.addLayout(bot)

        # 全部控件建好后才能连（_on_base_changed 会碰 lst）
        self.cb_base.currentTextChanged.connect(self._on_base_changed)
        self.cb_cmap.currentTextChanged.connect(self._on_cmap_changed)
        # ---- 右侧栏宽度按内容自适应 + 关掉左右滑动（宽度不够会把控件截掉） ----
        _need = max(rw.sizeHint().width(), rw.minimumSizeHint().width())
        right.setFixedWidth(max(352, min(int(_need) + 26, 560)))
        right.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.right_scroll = right
        self._set_tool('select')
        self._fill_layers()

    # ================================================================== 层览
    def _thumb_source(self, z):
        return self.s0[:, :, z]

    def _fill_layers(self):
        self.layers.blockSignals(True)
        self.layers.clear()
        # ★ 第一项：全层平均图（与 MATLAB 画掩模的底图 img_mask_base 是同一张）
        it = QListWidgetItem(QIcon(QPixmap.fromImage(to_qimage(self.s0.mean(axis=2), 'gray'))),
                             '★ 全层平均')
        it.setData(Qt.ItemDataRole.UserRole, -1)
        it.setToolTip('S0 的 20 层平均图——**画共通掩模就在这张上画**\n'
                      '（MATLAB 当年也是在它上画的；画一次，全 20 层共用）')
        self.layers.addItem(it)
        for z in range(self.n_layer):
            im = to_qimage(self._thumb_source(z), 'gray')
            it = QListWidgetItem(QIcon(QPixmap.fromImage(im)), f'层 {z + 1}')
            it.setData(Qt.ItemDataRole.UserRole, z)
            self.layers.addItem(it)
        self.layers.blockSignals(False)
        # 打开时停在「★ 全层平均」（默认底图 = S0 层间平均）；否则停在第 1 层
        self.layers.setCurrentRow(0 if '层间平均' in self._default_base else 1)

    # ================================================================== 底图
    def _base_2d(self, z=None):
        """当前底图的 2D 图像（按 combo 选择）。"""
        t = self.cb_base.currentText()
        z = self.layer if z is None else z
        if t.startswith('S0 层间平均'):
            return self.s0.mean(axis=2)
        if t.startswith('S0 逐层'):
            return self.s0[:, :, min(z, self.n_layer - 1)]
        if t.startswith('Z @'):
            if self.img_interp is None or self.w is None or self.w.size == 0:
                return self.s0.mean(axis=2)
            k = int(np.argmin(np.abs(self.w - self.sp_ppm.value())))
            arr = self.img_interp
            if arr.ndim == 4:
                if t.endswith('层间平均）'):
                    return arr[:, :, :, k].mean(axis=2)
                return arr[:, :, min(z, arr.shape[2] - 1), k]
            return arr[:, :, k] if arr.ndim == 3 else arr
        if t.startswith('ROI 底图'):
            z3 = self._roi_z_2d()
            if self._is_st():
                z3 = 1.0 - z3          # ST 显性模式（MATLAB roi_display_mode = 2）
            return z3 if self.mask2d is None else z3 * self.mask2d
        return self.s0.mean(axis=2)

    def _reload_layer(self, reset_view=False):
        lo, hi = self._clim() if self._is_roi_base() else (None, None)
        self.canvas.set_image(self._base_2d(), self._cmap_key(),
                              keep_view=not reset_view, vmin=lo, vmax=hi)
        self._sync_canvas()
        if self._is_roi_base():
            self.lab_layer.setText(
                f'共 {self.n_layer} 层\n当前：器官 ROI 底图\n'
                f'（{"1−Z" if self._is_st() else "Z"} @ {self.sp_ppm.value():g} ppm 的 '
                f'{self.n_layer} 层平均'
                + ('× 去噪掩模' if self.mask2d is not None else '，⚠ 还没掩模')
                + '）')
        else:
            self.lab_layer.setText(
                (f'共 {self.n_layer} 层\n当前：★ 全层平均图\n'
                 '（在这张上画一次 = 20 层共用）')
                if self.mean_mode else
                (f'共 {self.n_layer} 层\n当前：层 {self.layer + 1}'
                 + ('\n（底图选的是层间平均，画布作用在「共用」那张）'
                    if '层间平均' in self.cb_base.currentText() else '')))

    # ================================================================== 色图
    def _cmap_key(self):
        t = self.cb_cmap.currentText()
        for label, key in CMAP_CHOICES:
            if label == t:
                return key
        return 'gray'

    def _on_cmap_changed(self, _text):
        self._reload_layer()      # 换色图只重画底图，不动掩模

    def _on_base_changed(self, text):
        """底图下拉框被手动改了：选了「层间平均」就等同于停在「★ 全层平均」。"""
        mean = '层间平均' in str(text)
        if mean != self.mean_mode:
            self.mean_mode = mean
            self._refresh_targets()
        self._reload_layer()

    def _on_layer(self, row):
        if row < 0:
            return
        key = self.layers.item(row).data(Qt.ItemDataRole.UserRole)
        self.mean_mode = (key == -1)
        tg = self._cur_target()
        if tg is not None and tg.kind == 'roi':
            # 器官 ROI 底图是「Z 的 N 层平均」，与层览无关：只记层号，不动底图
            if key != -1:
                self.layer = int(key)
        elif self.mean_mode:
            # 点「★ 全层平均」→ 底图切成 S0 层间平均，画的是「共用」那张
            self.cb_base.blockSignals(True)
            self.cb_base.setCurrentText('S0 层间平均')
            self.cb_base.blockSignals(False)
        else:
            self.layer = int(key)
            if self.cb_base.currentText().startswith('S0 层间平均'):
                self.cb_base.blockSignals(True)
                self.cb_base.setCurrentText('S0 逐层')
                self.cb_base.blockSignals(False)
        self._reload_layer()
        self._refresh_targets()
        self._sync_canvas(sync_per=True)

    # ================================================================== 目标
    def _cur_key(self):
        it = self.lst.currentItem()
        return None if it is None else it.data(Qt.ItemDataRole.UserRole)

    def _cur_target(self):
        k = self._cur_key()
        return None if k is None else self.targets.get(k)

    def _refresh_targets(self, keep_undo=False):
        keep = self._cur_key()
        tg = self._cur_target()
        self.lst.blockSignals(True)
        self.lst.clear()
        for k, t in self.targets.items():
            self.lst.addItem(self._row_item(t))
        self.lst.blockSignals(False)
        row = (list(self.targets).index(keep) if keep in self.targets
               else list(self.targets).index(getattr(self, '_default_target_key',
                                                     'liverMask')
                                             if getattr(self, '_default_target_key',
                                                       'liverMask') in self.targets
                                             else next(iter(self.targets))))
        self.lst.setCurrentRow(row)
        if tg is not None:
            self.chk_vis.setChecked(tg.visible)
        self._sync_canvas(keep_undo=keep_undo)

    @staticmethod
    def _row_item(t):
        extra = f'（{len(t.per_layer)} 层单独）' if t.per_layer else ''
        it = QListWidgetItem(f'{t.name}　{t.n_voxels()} 体素{extra}')
        it.setData(Qt.ItemDataRole.UserRole, t.key)
        it.setForeground(QColor(t.color))
        return it

    def _update_row_text(self):
        """画笔连改时只刷新文字，不重建列表（否则拖动会卡）。"""
        tg = self._cur_target()
        row = self.lst.currentRow()
        if tg is None or row < 0:
            return
        it = self.lst.item(row)
        extra = f'（{len(tg.per_layer)} 层单独）' if tg.per_layer else ''
        it.setText(f'{tg.name}　{tg.n_voxels()} 体素{extra}')

    def _materialize_ops(self):
        """把画布上的矢量操作还原成各目标的数组。

        没有这一步：重开画布虽然看得到上次的多边形，但目标数组是空/旧的，
        一点【确定】就会导出空掩模（或旧值）——本模块（含器官 ROI）都靠这一步。
        只处理「有矢量操作」的上下文，不会把 per-layer 的语义改掉。
        """
        for ctx_id in list(getattr(self.canvas, 'ops', {})):
            if '|' not in ctx_id:
                continue
            key, scope = ctx_id.split('|', 1)
            tg = self.targets.get(key)
            if tg is None:
                continue
            m = self.canvas.compose_ctx(ctx_id)
            if m is None or not m.any():
                continue
            if tg.kind == 'roi' or scope in ('shared', 'mean'):
                tg.shared = m
            elif scope.startswith('L'):
                try:
                    tg.per_layer[int(scope[1:])] = m
                except ValueError:
                    continue
            tg.shape = m.shape

    def _apply_nroi(self):
        """画布里改 ROI 数量：新增/移除 ROI1…ROIn（保留已有勾画；减少时会丢掉末尾的）。"""
        n = int(self.sp_nroi.value())
        if n == int(self.nroi):
            return
        if n < int(self.nroi):
            keep = '、'.join(f'ROI{k}' for k in range(n + 1, int(self.nroi) + 1))
            if QMessageBox.question(self, '减少 ROI 数量',
                                    f'从 {int(self.nroi)} 减到 {n}：{keep} 的勾画会被丢掉，确定？'
                                    ) != QMessageBox.StandardButton.Yes:
                self.sp_nroi.setValue(int(self.nroi))
                return
        for k in range(1, n + 1):
            key = f'ROI{k}'
            if key not in self.targets:
                self.targets[key] = MaskTarget(key, key,
                                               ROI_PALETTE[(k - 1) % len(ROI_PALETTE)],
                                               kind='roi', editable=True)
        for k in range(n + 1, int(self.nroi) + 1):
            self.targets.pop(f'ROI{k}', None)
            self.canvas.drop_ops(f'ROI{k}|')
        self.nroi = n
        self._default_target_key = ('ROI1' if (n >= 1 and self.mask2d is not None)
                                    else 'liverMask')
        self._refresh_targets()
        self._apply_target_base()
        if callable(self.on_nroi):
            self.on_nroi(n)

    def _on_target_row(self, _row):
        tg = self._cur_target()
        if tg is not None:
            self.chk_vis.setChecked(tg.visible)
        self._apply_target_base()
        self._sync_canvas(sync_per=True)

    def _sync_canvas(self, keep_undo=False, sync_per=False):
        """把当前目标的该层掩模灌进画布，其它目标作为叠加。"""
        tg = self._cur_target()
        is_roi = bool(tg and tg.kind == 'roi')
        if sync_per:
            self.chk_per.blockSignals(True)
            self.chk_per.setChecked(bool(not self.mean_mode and tg
                                         and self.layer in tg.per_layer))
            self.chk_per.blockSignals(False)
        self.chk_per.setEnabled((not self.mean_mode) and not is_roi)
        self.chk_per.setToolTip(
            '现在停在「★ 全层平均」上，画的是一张 20 层共用的掩模。\n'
            '要让某一层单独画：左边点那一层 → 再勾这个。'
            if self.mean_mode else
            '勾上后，本层的涂改只作用于这一层；其余层用「共用」那张。\n'
            '导出时自动合成 3D (X,Y,层)。')
        editable = bool(tg and tg.editable)
        self.canvas.readonly = not editable
        self.canvas.color = tg.color if tg else '#22c55e'
        # 上下文 = 目标 × （共用张 / 某一层）；同一下文里保留矢量操作，切回来还能继续拖
        # 器官 ROI 一律存「共用」（2D 一张，与 MATLAB roipoly 口径一致）
        if tg is not None:      # 目标还没选出来时不建上下文（免得留个空的 none|shared）
            scope = 'shared' if is_roi else (
                'mean' if self.mean_mode else ('L%d' % self.layer
                                               if self.chk_per.isChecked() else 'shared'))
            self.canvas.set_context(f'{tg.key}|{scope}', seed=self._editing_mask(tg),
                                    reset_undo=not keep_undo)
        ovs = []
        for k, t in self.targets.items():
            if tg is None or t is tg or not t.visible:
                continue
            m = t.shared if self.mean_mode else t.resolved(self.layer)
            if m is not None:
                ovs.append((m, t.color, self._overlay_alpha))
        self.canvas.overlays = ovs
        for k, b in self.tool_btns.items():
            b.setEnabled(editable)
            b.setChecked(editable and k == self.canvas.tool)
        self.canvas.update()

    def _editing_mask(self, tg):
        """当前该拿到画布上改的那张。

        · 停在「★ 全层平均」→ 改的一定是「共用」那张（= 20 层共用掩模）
        · 停在某一层且勾了「本层单独保存」→ 改这一层的（不存在就以共用张为初值）
        · 停在某一层没勾 → 改「共用」那张
        """
        if tg is None:
            return None
        if tg.kind == 'roi':
            return tg.shared          # 器官 ROI：永远一张 2D 共用
        if self.mean_mode:
            return tg.shared
        if self.chk_per.isChecked():
            return tg.per_layer.get(self.layer, tg.shared)
        return tg.shared

    def _on_canvas_edited(self):
        tg = self._cur_target()
        if tg is None or not tg.editable:
            return
        per = (not self.mean_mode) and self.chk_per.isChecked() and tg.kind != 'roi'
        tg.set(self.canvas.mask, self.layer, per)
        self._update_row_text()

    def _on_polygon_closed(self):
        """多边形双击确定 → 立刻切到「选择/拖动」，顶点手柄就在手边，可以马上拖。"""
        if not self.canvas.readonly:
            self._set_tool('select')

    def _on_vis_toggled(self, v):
        tg = self._cur_target()
        if tg is not None:
            tg.visible = bool(v)
            self._sync_canvas()

    def _clear_target(self):
        tg = self._cur_target()
        if tg is None or not tg.editable:
            return
        if QMessageBox.question(self, '清空', f'清空「{tg.name}」？'
                               '（本层单独保存的会被清掉）') != QMessageBox.StandardButton.Yes:
            return
        tg.clear()
        self.canvas.drop_ops(f'{tg.key}|')
        self._refresh_targets()

    def _set_tool(self, key):
        self.canvas.set_tool(key)
        for k, b in self.tool_btns.items():
            b.setChecked(k == key)
        if getattr(self, 'lab_tool', None) is not None:
            self.lab_tool.setText({
                'select': '当前工具：选择/拖动 —— 拖顶点改那一点 · 拖多边形内部整体挪 · '
                          '右键顶点删掉它',
                'polygon': '当前工具：多边形 —— 左键加点，双击确定（自动封闭）；'
                           '右键 / Backspace = 撤掉刚点的那个点',
                'rect': '当前工具：矩形 —— 按住左键拖一个框出来',
                'brush': '当前工具：画笔 —— 按住左键在画布上涂（大小看「笔粗」）',
                'eraser': '当前工具：橡皮 —— 按住左键擦掉绿色（大小看「笔粗」）',
            }.get(key, ''))

    def _on_alpha(self, v):
        self.canvas.alpha = int(v)
        self._sync_canvas()

    def _on_show_ov(self, v):
        self.canvas.show_overlays = bool(v)
        self.canvas.update()

    # ================================================================== ROI 底图 / 色标
    def _is_roi_base(self):
        return self.cb_base.currentText().startswith('ROI 底图')

    def _is_st(self):
        """ROI 底图当前是不是「1−Z（ST）」显示模式。"""
        return self.cb_roi_disp.currentText() == ROI_DISP_ST

    def _roi_display_changed(self, *_a):
        """Z ↔ 1−Z 切换：色标按新图重算初值并即时刷新（只在 ROI 底图上生效）。"""
        if not self._is_roi_base():
            return
        self._auto_caxis()

    def _roi_z_2d(self, z=None):
        """归一化 Z 谱 @ 目标 ppm 的 N 层平均（严格复刻 MATLAB roi_display_mode=1）。"""
        if self.roi_z is None or self.roi_w is None or self.roi_w.size == 0:
            return self.s0.mean(axis=2)
        k = int(np.argmin(np.abs(self.roi_w - self.sp_ppm.value())))
        arr = self.roi_z
        if arr.ndim == 4:            # (X, Y, 频, 层)
            return arr[:, :, k, :].mean(axis=2)
        if arr.ndim == 3:
            return arr.mean(axis=2)
        return arr

    def _clim(self):
        return _f(self.ed_cax_lo.text()), _f(self.ed_cax_hi.text())

    def _apply_clim(self, *_a):
        """色标一改就实时刷新（只在 ROI 底图上生效）。"""
        if self._is_roi_base():
            self.canvas.set_clim(*self._clim())

    def _auto_caxis(self):
        """色标初值 = [0, 有效值 95% 分位]（= MATLAB dmin=0, dmax=quantile(valid,0.95)）。"""
        if not self._is_roi_base():
            return
        base = self._base_2d()
        v = base[np.isfinite(base)]
        if self.mask2d is not None and self.mask2d.shape == base.shape:
            v = base[(self.mask2d > 0) & np.isfinite(base)]
        hi = float(np.quantile(v, 0.95)) if v.size else 1.0
        self.ed_cax_lo.blockSignals(True)
        self.ed_cax_hi.blockSignals(True)
        self.ed_cax_lo.setText('0')
        self.ed_cax_hi.setText(f'{hi:.6g}')
        self.ed_cax_lo.blockSignals(False)
        self.ed_cax_hi.blockSignals(False)
        self._reload_layer()

    def _apply_target_base(self):
        """切「掩模对象」时：ROI → 底图换 Z×掩模 + 色图 jet 首行黑；其余 → 回 S0。"""
        tg = self._cur_target()
        _roi_ok = bool(tg is not None and tg.kind == 'roi'
                       and self.roi_z is not None and self.roi_w is not None)
        if getattr(self, 'cb_roi_disp', None) is not None:
            self.cb_roi_disp.setEnabled(_roi_ok)
        if tg is not None and tg.kind == 'roi':
            if self.roi_z is not None and self.roi_w is not None:
                self.cb_base.blockSignals(True)
                self.cb_base.setCurrentText(ROI_BASE_ITEM)
                self.cb_base.blockSignals(False)
                self.cb_cmap.blockSignals(True)
                self.cb_cmap.setCurrentText('jet（首行黑）')
                self.cb_cmap.blockSignals(False)
                if _f(self.ed_cax_lo.text()) is None and \
                        _f(self.ed_cax_hi.text()) is None:
                    self._auto_caxis()
                else:
                    self._reload_layer()
        elif self._is_roi_base():
            self.cb_base.blockSignals(True)
            self.cb_base.setCurrentText('S0 层间平均')
            self.cb_base.blockSignals(False)
            self.cb_cmap.blockSignals(True)
            self.cb_cmap.setCurrentText('彩色 viridis（≈parula）')
            self.cb_cmap.blockSignals(False)
            self._reload_layer()

    # ================================================================== SNR
    def _calc_snr(self):
        base = self.s0.mean(axis=2)          # 与 MATLAB img_mask_base 一致
        sigma = float(self.sp_sigma.value())
        src = '手输'
        if sigma <= 0:
            noise = self.targets['noiseROI'].shared
            if noise is None or not np.asarray(noise).any():
                QMessageBox.information(
                    self, '还没画噪声区',
                    '请先把左下角/背景那类「纯噪声」区域框一圈（目标选「噪声区」），\n'
                    '或者直接在手输σ里填 MATLAB 那边算出的 σ。')
                return
            px = base[np.asarray(noise, dtype=bool)]
            sigma = float(np.std(px))
            src = f'噪声区 {int(np.asarray(noise).sum())} 像素'
            self.sp_sigma.blockSignals(True)
            self.sp_sigma.setValue(sigma)
            self.sp_sigma.blockSignals(False)
        thr = float(self.sp_snr.value()) * sigma
        m = base > thr
        self.targets['snrMask'].set(m)
        self._snr_meta = dict(sigma=sigma, snr=float(self.sp_snr.value()), thr=thr,
                              n=int(m.sum()), src=src)
        self.lab_snr.setText(f'σ={sigma:.4f}（{src}）\n阈值 = {self.sp_snr.value():g}σ '
                             f'= {thr:.4g}\n信号体素 {int(m.sum())}')
        self.lst.setCurrentRow(list(self.targets).index('snrMask'))
        self._refresh_targets()

    def _copy_snr_to_liver(self):
        snr = self.targets['snrMask'].shared
        if snr is None:
            QMessageBox.information(self, '先算 SNR 掩模', '请先点【算 SNR 掩模】。')
            return
        self.targets['liverMask'].set(np.asarray(snr).copy())
        self.targets['liverMask'].per_layer.clear()
        self.canvas.drop_ops('liverMask|')          # 丢掉旧的多边形/笔迹，改用这张
        self.lst.setCurrentRow(list(self.targets).index('liverMask'))
        self._refresh_targets()

    # ================================================================== 导出
    def _ok(self):
        liver = self.targets['liverMask']
        snr = self.targets['snrMask']
        noise = self.targets['noiseROI']
        if liver.is_empty():
            if QMessageBox.question(self, '还没画 liverMask',
                                    '还没画「组织/肝脏」掩模，确定要保存吗？\n'
                                    '（没有它就没有去噪掩模）'
                                    ) != QMessageBox.StandardButton.Yes:
                return
        meta = dict(getattr(self, '_snr_meta', None) or {})
        per = sorted({int(z) for t in self.targets.values() for z in t.per_layer})
        self.result_data = dict(
            case=self.case,
            n_layer=self.n_layer,
            shape=tuple(int(v) for v in self.shape),
            base=self.cb_base.currentText(),
            cmap=self._cmap_key(),
            ops=self.canvas.dump_ops(),          # 会话内重开画布时恢复可拖的多边形
            ppm=float(self.sp_ppm.value()),
            liver=liver.as_array(self.n_layer),
            noise=(None if noise.is_empty() else noise.as_array(self.n_layer)),
            snr=(None if snr.is_empty() else snr.as_array(self.n_layer)),
            snr_sigma=float(meta.get('sigma') or self.sp_sigma.value()),
            snr_factor=float(self.sp_snr.value()),
            snr_thr=float(meta.get('thr') or 0.0),
            snr_src=str(meta.get('src') or ''),
            per_layer=per,
            n_liver=int(liver.n_voxels()),
            n_noise=int(noise.n_voxels()),
            n_snr=int(snr.n_voxels()),
            rois=[(f'ROI{k}', None if self.targets.get(f'ROI{k}') is None
                   else self.targets[f'ROI{k}'].as_array(self.n_layer))
                  for k in range(1, self.nroi + 1)],
            nroi=int(self.nroi),
            roi_ppm=float(self.sp_ppm.value()),
            roi_caxis=[_f(self.ed_cax_lo.text()), _f(self.ed_cax_hi.text())],
            roi_base=self._is_roi_base(),
            roi_display=('1-Z' if self._is_st() else 'Z'),
            roi_mask_used=bool(self.mask2d is not None),
        )
        self.accept()


def pick_mask_file(parent, start=''):
    from PySide6.QtWidgets import QFileDialog
    d = start if (start and __import__('pathlib').Path(start).is_dir()) else ''
    f, _ = QFileDialog.getOpenFileName(parent, '选择掩模 .mat（Mask / Thmask / liverMask / snrMask）',
                                       d, 'MAT (*.mat);;所有文件 (*)')
    return f or ''
