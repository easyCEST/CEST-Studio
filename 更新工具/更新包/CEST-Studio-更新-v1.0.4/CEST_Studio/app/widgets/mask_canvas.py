# -*- coding: utf-8 -*-
"""app/widgets/mask_canvas.py —— ⑧ 掩模画布的底层控件（自绘，不依赖 matplotlib 窗口）。

两块：
  * SliceCanvas   —— 画布本体：灰度/jet 底图 + 半透明掩模叠加 + 多边形/矩形/画笔/橡皮
                     + 撤销 + 滚轮缩放 + 中键平移。**只负责画与显示，不管存盘**。
  * MaskTarget    —— 一个「掩模对象」：默认一张 2D 全层共用；勾了「本层单独保存」
                     的那几层单独存，导出时合成 3D (X,Y,层)。
  * MaskCanvasPanel —— ⑧ 页「参数」区上面的面板（摘要 + 打开画布 + 载入已有掩模）。

坐标约定：画布上的一律是「图像像素坐标」——点 = (列 col, 行 row)，与
cest_post.points_to_mask / roi_plot_song 的入参一致，直接喂给核心层即可。
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QImage, QPainter, QPen, QPixmap,
                           QPolygonF)
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)

# 各掩模对象的默认颜色（与核心层/旧图习惯保持一致）
COLORS = {
    'liverMask':   '#22c55e',   # 组织/肝脏（去噪掩模的主体）—— 绿
    'noiseROI':    '#f59e0b',   # 噪声区（供 SNR 用）—— 橙
    'snrMask':     '#06b6d4',   # SNR 自动掩模 —— 青
    'Mask':        '#ef4444',   # 最终掩模 —— 红
}
ROI_PALETTE = ('#ef4444', '#3b82f6', '#a855f7', '#eab308', '#14b8a6', '#f97316')

# 底图色图（只影响看图的观感，不影响任何计算）——手动选，程序不替你切
CMAP_CHOICES = (
    ('灰度', 'gray'),
    ('彩色 viridis（≈parula）', 'viridis'),
    ('jet（首行黑）', 'jet_black'),
    ('jet', 'jet'),
)


# ===========================================================================
# 图像 → QImage（自己实现，避免任何 matplotlib 后端依赖）
# ===========================================================================
_LUT_CACHE: dict = {}


def _lut(cmap='gray'):
    if cmap in _LUT_CACHE:
        return _LUT_CACHE[cmap]
    if cmap == 'gray':
        v = np.repeat(np.linspace(0, 255, 256).astype(np.uint8)[:, None], 3, axis=1)
    elif cmap.startswith('jet_black'):
        # jet 前 n 行置黑：'jet_black' = 2 行（MATLAB colormap([0 0 0; jet(255)])）；
        # 'jet_black1' = 1 行（MATLAB 3D_new 的 cmap(1,:)=0，= gen_mosaic 默认）
        v = _lut('jet').copy()
        try:
            n = int(cmap[len('jet_black'):] or 2)
        except ValueError:
            n = 2
        v[:max(0, n), :] = 0
    else:
        import matplotlib
        try:
            cm = matplotlib.colormaps[cmap]
        except Exception:                                        # noqa: BLE001
            cm = matplotlib.colormaps['gray']
        v = (np.asarray(cm(np.linspace(0, 1, 256)))[:, :3] * 255).astype(np.uint8)
    _LUT_CACHE[cmap] = v
    return v


def to_qimage(arr, cmap='gray', vmin=None, vmax=None):
    """2D 浮点图 → QImage（1%~99% 分位自适应，与出图模块口径一致）。"""
    a = np.asarray(arr, dtype=float)
    fin = a[np.isfinite(a)]
    if fin.size == 0:
        fin = np.array([0.0])
    if vmin is None:
        vmin = float(np.percentile(fin, 1))
    if vmax is None:
        vmax = float(np.percentile(fin, 99))
    if not np.isfinite(vmin):
        vmin = float(np.nanmin(fin))
    if (not np.isfinite(vmax)) or vmax <= vmin:
        vmax = vmin + 1.0
    t = np.clip((a - vmin) / (vmax - vmin), 0, 1)
    idx = np.nan_to_num(t * 255, nan=0.0).astype(np.uint8)
    rgb = np.ascontiguousarray(_lut(cmap)[idx])                  # (H,W,3)
    h, w, _ = rgb.shape
    return QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


def mask_qimage(mask, color, alpha=110):
    """布尔掩模 → 半透明 RGBA 图（False 处全透明）。"""
    m = np.asarray(mask).astype(bool)
    if m.ndim != 2:
        m = m[:, :, 0]
    h, w = m.shape
    rgba = np.zeros((h, w, 4), np.uint8)
    c = QColor(color)
    rgba[..., 0] = c.red()
    rgba[..., 1] = c.green()
    rgba[..., 2] = c.blue()
    rgba[..., 3] = np.where(m, int(alpha), 0).astype(np.uint8)
    rgba = np.ascontiguousarray(rgba)
    return QImage(rgba.data, w, h, 4 * w, QImage.Format.Format_RGBA8888).copy()


# ===========================================================================
# 掩模对象
# ===========================================================================
class MaskTarget:
    """一个掩模对象：shared(2D 全层共用) + per_layer{层号(0基): 2D 覆盖}。

    MATLAB 口径是「2D 一张全层共用」，本类额外支持「单独层另画一张」：
    导出时若 per_layer 非空 → 合成 3D (X,Y,层)；否则就是 2D。
    """

    def __init__(self, key, name='', color='#22c55e', kind='draw', editable=True):
        self.key = key
        self.name = name or key
        self.color = color
        self.kind = kind                 # draw / derived(只读) / roi
        self.editable = editable
        self.shared = None               # 2D bool
        self.per_layer: dict = {}        # {层(0基): 2D bool}
        self.visible = True
        self.shape = None                # (rows, cols)

    # ---- 读写 ----
    def resolved(self, layer=None):
        """该层实际用的 2D 掩模（没有就 None）。"""
        if layer is not None and layer in self.per_layer:
            return self.per_layer[layer]
        return self.shared

    def set(self, arr, layer=None, per_layer=False):
        arr = None if arr is None else np.asarray(arr).astype(bool)
        if per_layer and layer is not None:
            self.per_layer[layer] = arr
        else:
            self.shared = arr
        if arr is not None:
            self.shape = arr.shape

    def clear(self):
        self.shared = None
        self.per_layer.clear()

    def any_mask(self, n_layer=None):
        if self.shared is not None:
            return self.shared
        if self.per_layer:
            return next(iter(self.per_layer.values()))
        return None

    def n_voxels(self):
        s = set()
        if self.shared is not None:
            s.add(int(self.shared.sum()))
        for _l, m in self.per_layer.items():
            s.add(int(np.asarray(m).sum()))
        return max(s) if s else 0

    def is_empty(self):
        return self.shared is None and not self.per_layer

    def as_array(self, n_layer):
        """导出：2D（全层共用）或 3D (X,Y,n_layer)。"""
        if not self.per_layer:
            return None if self.shared is None else self.shared.astype(float)
        ref = self.any_mask()
        if ref is None:
            return None
        out = np.zeros((ref.shape[0], ref.shape[1], int(n_layer)), dtype=float)
        for k in range(int(n_layer)):
            m = self.resolved(k)
            if m is not None:
                out[:, :, k] = np.asarray(m).astype(float)
        return out


# ===========================================================================
# 画布
# ===========================================================================
class SliceCanvas(QWidget):
    """底图 + 掩模叠加 + 勾画 + **拖动编辑**（对应 MATLAB roipoly / drawpolygon 的手感）。

    掩模是**矢量**的：按「上下文」（= 某个目标 × 共用张 / 某一层）保存一串操作 ——

        polys  = [顶点数组 (N,2), …]            ← 多边形：可拖单个顶点，也可整体平移
        deltas = [(布尔栅格, add?), …]          ← 画笔(补)/橡皮(擦)，按先后顺序叠上去

    当前掩模 = 先把所有多边形并起来，再按顺序叠 deltas。这样「拖多边形」不会把
    之前涂过的笔迹弄丢；撤销是整步回退（顶点拖动、一笔涂抹、一个多边形各算一步）。

    鼠标：左键加点 / 双击·右键·回车闭合 / 滚轮缩放 / 中键平移；
    「选择/拖动」工具下：拖顶点 = 改那个点，点多边形内部拖 = 整体挪，右键顶点 = 删该点。
    """

    edited = Signal()                     # 当前掩模被改动（外部需回写目标）
    polygon_closed = Signal()             # 多边形双击确定了 → 外部切到「选择/拖动」继续细调

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(380, 380)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.img = None                   # 底图 2D float
        self.cmap = 'gray'
        self.mask = None                  # 当前上下文合成出来的 2D bool
        self.color = '#22c55e'
        self.overlays: list = []          # [(mask2d, color, alpha)] 其它目标的栅格叠加
        self.show_overlays = True
        self.alpha = 110
        # 色标区间（只影响显示，不改任何数据）：None = 用 1%~99% 分位自适应
        self.vmin = None
        self.vmax = None

        self.tool = 'select'
        self.brush_r = 3
        self.readonly = False
        self.hit_px = 9                   # 顶点命中半径（屏幕像素）

        # ---- 矢量操作（按上下文存） ----
        self.ctx = None
        self.ops: dict = {}
        self._stroke = None               # 正在画的那一笔（栅格），松手才入 ops
        self._stroke_add = True
        self._pre = None                  # 拖动时：除被拖多边形以外的合成结果
        self._pdrag = None                # 拖动状态
        self._hover_v = None              # 鼠标悬停的顶点 (pi, vi)

        self._qi = None                   # 底图 QImage 缓存
        self._cursor = QPointF(0.0, 0.0)
        self._poly: list = []             # 正在点的多边形顶点
        self._drag = None                 # 矩形拖拽起点
        self._last = None                 # 画笔上一个点
        self._zoom = 1.0
        self._off = QPointF(0.0, 0.0)
        self._pan = None
        self._undo: list = []
        self._pcache_refs = ()            # 上次算多边形并集时用的那些数组（身份比较）
        self._pcache_raster = None        # 对应的并集栅格（没变就直接复用）

    # ------------------------------------------------------------- 设置
    def set_image(self, img, cmap='gray', keep_view=False, vmin=None, vmax=None):
        self.img = None if img is None else np.asarray(img, dtype=float)
        self.cmap = cmap
        self.vmin, self.vmax = vmin, vmax
        self._qi = (None if self.img is None
                    else to_qimage(self.img, cmap, vmin, vmax))
        if not keep_view:
            self._zoom, self._off = 1.0, QPointF(0.0, 0.0)
        self._poly.clear()
        self.update()

    def set_clim(self, vmin, vmax):
        """实时改色标区间（画的时候随时可调；只影响观感，不动任何数据）。"""
        self.vmin, self.vmax = vmin, vmax
        if self.img is not None:
            self._qi = to_qimage(self.img, self.cmap, vmin, vmax)
        self.update()

    def set_context(self, ctx_id, seed=None, reset_undo=True):
        """切到某个上下文（目标 × 共用/某层）。首次进入时可用 seed 当底（比如载入的掩模）。"""
        if self.ctx != ctx_id:
            self.ctx = ctx_id
            if ctx_id not in self.ops:
                self.ops[ctx_id] = {'polys': [], 'deltas': []}
                s = None if seed is None else np.asarray(seed).astype(bool)
                if s is not None and s.any():
                    self.ops[ctx_id]['deltas'].append((s, True))
            self._poly.clear()
            self._pdrag = None
            self._stroke = None
        if reset_undo:
            self._undo = []
        self._compose()

    def drop_ops(self, prefix):
        """丢掉某个目标名下所有上下文（清空/重新载入/覆盖初值时用）。"""
        for k in [k for k in list(self.ops) if k.startswith(prefix)]:
            self.ops.pop(k, None)
        self._pdrag = None
        self._stroke = None
        self._undo = []
        self._pcache_refs, self._pcache_raster = (), None
        self._compose()

    def set_tool(self, tool):
        self.tool = tool
        self._poly.clear()
        self._drag = None
        self._pdrag = None
        self.update()

    def fit(self):
        self._zoom, self._off = 1.0, QPointF(0.0, 0.0)
        self.update()

    # ------------------------------------------------------------- 坐标换算
    def _disp(self):
        h, w = self.img.shape[:2]
        s = min(self.width() / float(w), self.height() / float(h)) * 0.97 * self._zoom
        cx = self.width() / 2.0 + self._off.x()
        cy = self.height() / 2.0 + self._off.y()
        return s, cx - w * s / 2.0, cy - h * s / 2.0

    def to_img(self, pos) -> tuple:
        s, x0, y0 = self._disp()
        return ((pos.x() - x0) / s, (pos.y() - y0) / s)

    def to_widget(self, col, row) -> QPointF:
        s, x0, y0 = self._disp()
        return QPointF(x0 + col * s, y0 + row * s)

    # ------------------------------------------------------------- 矢量操作
    def _ops(self):
        if self.ctx is None:
            return {'polys': [], 'deltas': []}
        if self.ctx not in self.ops:
            self.ops[self.ctx] = {'polys': [], 'deltas': []}
        return self.ops[self.ctx]

    def _shape(self):
        return None if self.img is None else tuple(self.img.shape[:2])

    def _compose(self):
        """把 polys + deltas(+ 正在涂的那一笔) 合成当前掩模。"""
        shape = self._shape()
        if shape is None or self.ctx is None:
            self.mask = None
            self.update()
            return
        o = self._ops()
        m = self._poly_union()
        for dm, add in o['deltas']:
            dm = np.asarray(dm, bool)
            if dm.shape != shape:
                continue
            m = (m | dm) if add else (m & ~dm)
        if self._stroke is not None and self._stroke.shape == shape:
            m = (m | self._stroke) if self._stroke_add else (m & ~self._stroke)
        self.mask = m
        self.update()

    def compose_ctx(self, ctx_id):
        """不切当前上下文，直接算出某个上下文的合成掩模。

        重开画布时用：把上次的 polys/deltas 还原成数组（否则点确定会导出空掩模）。
        """
        shape = self._shape()
        if shape is None or ctx_id not in self.ops:
            return None
        o = self.ops[ctx_id]
        from cest_post import points_to_mask
        m = np.zeros(shape, bool)
        for pts in o['polys']:
            m |= points_to_mask(np.asarray(pts, dtype=float), shape)
        for dm, add in o['deltas']:
            dm = np.asarray(dm, bool)
            if dm.shape != shape:
                continue
            m = (m | dm) if add else (m & ~dm)
        return m

    def dump_ops(self):
        """把矢量操作导出（内存用；deltas 是栅格，只在本进程内传递）。"""
        out = {}
        for k, v in self.ops.items():
            out[k] = {'polys': [np.asarray(p, float).tolist() for p in v['polys']],
                      'deltas': [(np.asarray(m, bool), bool(a)) for m, a in v['deltas']]}
        return out

    def load_ops(self, data):
        """恢复矢量操作（重开画布时还能继续拖，不必重画）。"""
        self.ops = {}
        for k, v in (data or {}).items():
            if k.startswith('none|'):
                continue
            self.ops[k] = {'polys': [np.asarray(p, float) for p in (v.get('polys') or [])],
                           'deltas': [(np.asarray(m, bool), bool(a))
                                      for m, a in (v.get('deltas') or [])]}
        self.ctx = None
        self._pdrag = None
        self._stroke = None
        self._undo = []
        self._pcache_refs, self._pcache_raster = (), None

    def _push_undo(self):
        if self.ctx is None:
            return
        o = self._ops()
        self._undo.append((list(o['polys']), list(o['deltas'])))
        if len(self._undo) > 30:
            self._undo.pop(0)

    def undo(self):
        if not self._undo or self.ctx is None:
            return
        polys, deltas = self._undo.pop()
        self.ops[self.ctx] = {'polys': polys, 'deltas': deltas}
        self._stroke = None
        self._pdrag = None
        self._compose()
        self.edited.emit()

    def add_polygon(self, pts):
        """直接给一组顶点（外部/测试用，等同于画完闭合）。"""
        self._add_poly(pts)

    def _add_poly(self, pts):
        from cest_post import points_to_mask
        pts = np.asarray(pts, dtype=float).reshape(-1, 2)
        # 去掉连着的重复点 / 末点回到首点的情况（双击确定时容易多一个）
        keep = []
        for q in pts:
            if not keep or abs(q[0] - keep[-1][0]) + abs(q[1] - keep[-1][1]) > 1e-6:
                keep.append(q)
        if len(keep) > 2 and abs(keep[0][0] - keep[-1][0]) + abs(keep[0][1] - keep[-1][1]) < 1e-6:
            keep.pop()
        pts = np.asarray(keep, dtype=float)
        if len(pts) < 3:
            return
        if not np.isfinite(pts).all():
            return
        self._push_undo()
        self._ops()['polys'].append(pts)
        _ = points_to_mask(pts, self._shape() or (1, 1))     # 形状保护
        self._compose()
        self.edited.emit()

    def clear_mask(self):
        """清空当前上下文的所有操作。"""
        if self.ctx is None:
            return
        self._push_undo()
        self.ops[self.ctx] = {'polys': [], 'deltas': []}
        self._stroke = None
        self._compose()
        self.edited.emit()

    # ------------------------------------------------------------- 绘制
    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor('#0b1220'))
        if self.img is None:
            p.setPen(QColor('#94a3b8'))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       '还没有底图\n（先选好输入 / 病例，再打开画布）')
            return
        if self._qi is None:
            self._qi = to_qimage(self.img, self.cmap, self.vmin, self.vmax)
        s, x0, y0 = self._disp()
        h, w = self.img.shape[:2]
        rect = QRectF(x0, y0, w * s, h * s)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        p.drawImage(rect, self._qi)
        if self.show_overlays:
            for m, col, al in self.overlays:
                if m is not None:
                    p.drawImage(rect, mask_qimage(m, col, al))
        if self.mask is not None:
            p.drawImage(rect, mask_qimage(self.mask, self.color, self.alpha))

        # ---- 多边形轮廓 + （选择工具下）可拖的顶点手柄 ----
        o = self._ops()
        for pi, pts in enumerate(o['polys']):
            qpts = [self.to_widget(c, r) for c, r in pts]
            pen = QPen(QColor('#f59e0b'), 2.0)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPolygon(QPolygonF(qpts))
            if self.tool == 'select' and not self.readonly:
                for vi, q in enumerate(qpts):
                    hot = (self._hover_v == (pi, vi)) or (
                        self._pdrag is not None and self._pdrag.get('kind') == 'vertex'
                        and self._pdrag.get('pi') == pi and self._pdrag.get('vi') == vi)
                    p.setBrush(QBrush(QColor('#ffffff' if hot else '#f59e0b')))
                    p.setPen(QPen(QColor('#111827'), 1.2))
                    p.drawRect(QRectF(q.x() - 4.0, q.y() - 4.0, 8.0, 8.0))

        # ---- 正在点的多边形 ----
        pen = QPen(QColor('#fbbf24'))
        pen.setWidth(2)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if self.tool == 'polygon' and self._poly:
            pts = [self.to_widget(c, r) for c, r in self._poly]
            if len(pts) > 1:
                p.drawPolyline(QPolygonF(pts))
            for q in pts:
                p.drawEllipse(q, 3.0, 3.0)
        if self.tool == 'rect' and self._drag is not None:
            a = self.to_widget(*self._drag)
            b = self._cursor
            p.drawRect(QRectF(a, b).normalized())
        p.setPen(QPen(QColor('#334155')))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))

    # ------------------------------------------------------------- 命中判定
    def _hit_vertex(self, pos):
        if self.img is None:
            return None
        best = None
        for pi, pts in enumerate(self._ops()['polys']):
            for vi, (c, r) in enumerate(pts):
                q = self.to_widget(c, r)
                d = ((q.x() - pos.x()) ** 2 + (q.y() - pos.y()) ** 2) ** 0.5
                if d <= self.hit_px and (best is None or d < best[0]):
                    best = (d, pi, vi)
        return None if best is None else (best[1], best[2])

    def _hit_poly(self, col, row):
        from matplotlib.path import Path as MplPath
        o = self._ops()
        for pi in range(len(o['polys']) - 1, -1, -1):
            try:
                if MplPath(np.asarray(o['polys'][pi], float)).contains_point((col, row)):
                    return pi
            except Exception:                                    # noqa: BLE001
                continue
        return None

    # ------------------------------------------------------------- 鼠标
    def mousePressEvent(self, e):
        if self.img is None:
            return
        pos = e.position()
        if e.button() == Qt.MouseButton.MiddleButton:
            self._pan = (pos, QPointF(self._off))
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if self.readonly:
            return

        # ---- 选择/拖动工具：拖顶点、整体挪、右键删点 ----
        if self.tool == 'select':
            hit = self._hit_vertex(pos)
            if hit is not None:
                pi, vi = hit
                if e.button() == Qt.MouseButton.RightButton:
                    if len(self._ops()['polys'][pi]) > 3:
                        self._push_undo()
                        pts = np.delete(self._ops()['polys'][pi], vi, axis=0)
                        self._ops()['polys'][pi] = pts
                        self._compose()
                        self.edited.emit()
                    return
                if e.button() == Qt.MouseButton.LeftButton:
                    self._push_undo()
                    self._pre = self._compose_except(pi)
                    self._pdrag = {'kind': 'vertex', 'pi': pi, 'vi': vi,
                                   'pts0': self._ops()['polys'][pi].copy()}
                    self.setCursor(Qt.CursorShape.SizeAllCursor)
                    return
            if e.button() == Qt.MouseButton.LeftButton:
                col, row = self.to_img(pos)
                pi = self._hit_poly(col, row)
                if pi is not None:
                    self._push_undo()
                    self._pre = self._compose_except(pi)
                    self._pdrag = {'kind': 'whole', 'pi': pi,
                                   'pts0': self._ops()['polys'][pi].copy(),
                                   'start': (col, row)}
                    self.setCursor(Qt.CursorShape.ClosedHandCursor)
                    return
            return

        col, row = self.to_img(pos)
        if self.tool == 'polygon':
            if e.button() == Qt.MouseButton.RightButton:
                # 与 MATLAB 一致：右键**不闭合**；这里用它撤掉刚点的那个点
                if self._poly:
                    self._poly.pop()
                    self.update()
                return
            if e.button() == Qt.MouseButton.LeftButton:
                self._poly.append((col, row))
                self.update()
            return
        if self.tool == 'rect':
            if e.button() == Qt.MouseButton.LeftButton:
                self._drag = (col, row)
                self._cursor = pos
                self.update()
            return
        if self.tool in ('brush', 'eraser'):
            if e.button() == Qt.MouseButton.LeftButton:
                shape = self._shape()
                if shape is None:
                    return
                self._push_undo()
                self._stroke = np.zeros(shape, bool)
                self._stroke_add = (self.tool == 'brush')
                self._last = (col, row)
                self._paint(col, row)
            return

    def mouseMoveEvent(self, e):
        self._cursor = e.position()
        if self._pan is not None:
            start, off0 = self._pan
            self._off = off0 + (e.position() - start)
            self.update()
            return
        if self.img is None:
            return

        # ---- 拖动中 ----
        if self._pdrag is not None:
            col, row = self.to_img(e.position())
            o = self._ops()
            pi = self._pdrag['pi']
            p0 = self._pdrag['pts0']
            if self._pdrag['kind'] == 'vertex':
                vi = self._pdrag['vi']
                newp = p0.copy()
                newp[vi] = (col, row)
            else:
                sc, sr = self._pdrag['start']
                newp = p0 + np.array([col - sc, row - sr], float)
            o['polys'][pi] = newp
            from cest_post import points_to_mask
            shape = self._shape()
            base = self._pre if self._pre is not None else np.zeros(shape, bool)
            self.mask = base | points_to_mask(newp, shape)
            self.edited.emit()
            self.update()
            return

        if self.tool == 'rect' and self._drag is not None:
            self.update()
            return
        if self.tool in ('brush', 'eraser') and self._last is not None \
                and (e.buttons() & Qt.MouseButton.LeftButton):
            col, row = self.to_img(e.position())
            self._paint(col, row, self._last)
            self._last = (col, row)
            return
        # 悬停：顶点/多边形高亮 + 光标
        if self.tool == 'select' and not self.readonly:
            hit = self._hit_vertex(e.position())
            if hit != self._hover_v:
                self._hover_v = hit
                self.update()
            if hit is not None:
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                col, row = self.to_img(e.position())
                self.setCursor(Qt.CursorShape.OpenHandCursor
                               if self._hit_poly(col, row) is not None
                               else Qt.CursorShape.ArrowCursor)
        else:
            self.update()

    def mouseReleaseEvent(self, e):
        if self._pan is not None and e.button() == Qt.MouseButton.MiddleButton:
            self._pan = None
            self.unsetCursor()
            return
        if self.img is None or self.readonly:
            return
        if self._pdrag is not None:
            self._pdrag = None
            self._pre = None
            self.unsetCursor()
            self._compose()
            self.edited.emit()
            return
        if self.tool == 'rect' and self._drag is not None and e.button() == Qt.MouseButton.LeftButton:
            col, row = self.to_img(e.position())
            r0, r1 = sorted((self._drag[0], col))
            c0, c1 = sorted((self._drag[1], row))
            self._drag = None
            self._add_rect(r0, r1, c0, c1)
            return
        if self.tool in ('brush', 'eraser') and self._stroke is not None:
            if self._stroke.any():
                self._ops()['deltas'].append((self._stroke, self._stroke_add))
            self._stroke = None
            self._last = None
            self._compose()
            self.edited.emit()

    def mouseDoubleClickEvent(self, e):
        if self.tool == 'polygon' and not self.readonly:
            self._finish_poly()

    def wheelEvent(self, e):
        if self.img is None:
            return
        k = 1.0015 ** e.angleDelta().y()
        k = max(0.3, min(8.0, k))
        before = self.to_img(e.position())
        self._zoom *= k
        after = self.to_img(e.position())
        s, _x0, _y0 = self._disp()
        self._off += QPointF((after[0] - before[0]) * s, (after[1] - before[1]) * s)
        self.update()

    # ------------------------------------------------------------- 勾画工具
    def _poly_union(self, skip_pi=-1):
        """把所有多边形的并集算出来（没变动过就直接复用上次的结果）。

        缓存按「数组对象身份」判断：顶点拖动/删除时是**换一个新数组**（不是原地改），
        所以身份变了 → 自动重算；画笔涂抹只动 deltas，多边形没变 → 直接用缓存。
        """
        shape = self._shape()
        o = self._ops()
        cur = tuple(p for i, p in enumerate(o['polys']) if i != skip_pi)
        if (len(cur) == len(self._pcache_refs)
                and all(a is b for a, b in zip(cur, self._pcache_refs))
                and self._pcache_raster is not None
                and self._pcache_raster.shape == shape):
            return self._pcache_raster
        from cest_post import points_to_mask
        m = np.zeros(shape, bool)
        for pts in cur:
            m |= points_to_mask(np.asarray(pts, dtype=float), shape)
        self._pcache_refs, self._pcache_raster = cur, m
        return m

    def _compose_except(self, skip_pi):
        """除第 skip_pi 个多边形以外，其它多边形 + deltas 的合成结果（拖动时用）。"""
        o = self._ops()
        m = self._poly_union(skip_pi)
        for dm, add in o['deltas']:
            dm = np.asarray(dm, bool)
            if dm.shape != shape:
                continue
            m = (m | dm) if add else (m & ~dm)
        return m

    def _add_rect(self, r0, r1, c0, c1):
        if c1 - c0 < 1 or r1 - r0 < 1:
            return
        pts = np.asarray([[c0, r0], [c1, r0], [c1, r1], [c0, r1]], dtype=float)
        self._add_poly(pts)

    def _brush(self, col, row):
        if self._stroke is None:
            return
        m = self._stroke
        h, w = m.shape
        r = max(1, int(round(self.brush_r)))
        ci, ri = int(round(col)), int(round(row))
        y0, y1 = max(0, ri - r), min(h, ri + r + 1)
        x0, x1 = max(0, ci - r), min(w, ci + r + 1)
        if y0 >= y1 or x0 >= x1:
            return
        yy, xx = np.mgrid[y0:y1, x0:x1]
        disc = (yy - row) ** 2 + (xx - col) ** 2 <= r * r
        m[y0:y1, x0:x1] |= disc
        self._compose()
        self.edited.emit()

    def _paint(self, col, row, prev=None):
        if prev is None:
            self._brush(col, row)
            return
        n = int(max(abs(col - prev[0]), abs(row - prev[1]))) + 1
        for k in range(1, n + 1):
            t = k / float(n)
            self._brush(prev[0] + (col - prev[0]) * t, prev[1] + (row - prev[1]) * t)

    def _finish_poly(self):
        if len(self._poly) < 3:
            self._poly.clear()
            self.update()
            return
        pts = np.asarray(self._poly, dtype=float)
        self._poly.clear()
        self._add_poly(pts)
        self.polygon_closed.emit()          # 双击确定后 → 外部切到「选择/拖动」继续细调

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._finish_poly()
        elif e.key() == Qt.Key.Key_Escape:
            self._poly.clear()
            self.update()
        elif e.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            # 与 MATLAB 一致：Backspace / Delete 撤掉刚点的那个点
            if self._poly:
                self._poly.pop()
                self.update()
        else:
            super().keyPressEvent(e)


class MaskCanvasPanel(QGroupBox):
    """摘要 + 【打开掩模画布…】【载入已有掩模…】【从输入目录找掩模】。

    逻辑全在 app/modules/m_roi.py，这里只摆控件、把点击事件转出去。
    """

    def __init__(self, parent=None):
        super().__init__('去噪掩模 + 器官 ROI（Mask = 组织 ROI × 噪声 SNR 掩模）', parent)
        self.on_open = None          # callable()
        self.on_load = None          # callable()
        self.on_scan = None          # callable()
        self.on_redraw_roi = None    # callable()

        v = QVBoxLayout(self)
        v.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.btn_open = QPushButton('打开掩模画布…')
        self.btn_open.setObjectName('primary')
        self.btn_open.clicked.connect(lambda: self.on_open and self.on_open())
        self.btn_load = QPushButton('载入已有掩模…')
        self.btn_load.setToolTip('从现成的 .mat 读入 Mask / Thmask / liverMask / snrMask，拷进 00_掩模')
        self.btn_load.clicked.connect(lambda: self.on_load and self.on_load())
        self.btn_scan = QPushButton('从输入目录找')
        self.btn_scan.setToolTip('在输入目录里自动找 Mask.mat / liverMask.mat / Thmask.mat')
        self.btn_scan.clicked.connect(lambda: self.on_scan and self.on_scan())
        row.addWidget(self.btn_open)
        row.addWidget(self.btn_load)
        row.addWidget(self.btn_scan)
        row.addStretch(1)
        v.addLayout(row)

        row2 = QHBoxLayout()
        row2.setSpacing(6)
        self.btn_roi = QPushButton('重新画 ROI…')
        self.btn_roi.setToolTip('打开画布、直接到器官 ROI：默认沿用已有的 ROI_total.mat；\n'
                                '点这个按钮 = 强制覆盖重画（不用手删文件）')
        self.btn_roi.clicked.connect(lambda: self.on_redraw_roi and self.on_redraw_roi())
        row2.addWidget(self.btn_roi)
        row2.addStretch(1)
        v.addLayout(row2)

        self.state = QLabel('还没画。点【打开掩模画布…】开始。')
        self.state.setObjectName('muted')
        self.state.setWordWrap(True)
        v.addWidget(self.state)

    def set_state(self, lines):
        from app.widgets.autosize import fit_label
        # ★ 用 fit_label：可滚动区里自动换行的 QLabel 会被截掉一行
        fit_label(self.state, '\n'.join(lines) if lines else '还没画。点【打开掩模画布…】开始。')
