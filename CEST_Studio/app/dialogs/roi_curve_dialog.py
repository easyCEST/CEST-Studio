# -*- coding: utf-8 -*-
"""app/dialogs/roi_curve_dialog.py —— ③ CEST 后处理 的「ROI 定量曲线」对话框（不单独占导航）。

    ┌ 左：层 + ROI 勾选 ┐ ┌ 中：曲线图（Z 谱+拟合 / LD 残差）┐ ┌ 右：选项 + 导出 ┐

取数与出图**和页面【开始】完全同源**（都走核心层 cest_roi.run_curves）：
预览看到的 = 导出的。
★ 默认把「③ 已跑完的所有层」都算一遍（可只勾几层）。
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QSpinBox, QVBoxLayout, QWidget)

from detect import rules as R

B0_TAGS = ('No_corr', 'B0map_corr', 'Self_corr')
B0_MAP = {'不校正': 'No_corr', 'B0 图谱校正': 'B0map_corr', '自校正': 'Self_corr'}
NEG_MAP = {'正频偏': 0, '负频偏': 1, '全频偏': 2}


def _f(v, d=None):
    try:
        t = str(v).strip()
        return d if t == '' else float(t)
    except (TypeError, ValueError):
        return d


def _b(v, d='-inf'):
    v = str(v if v is not None else '').strip().lower()
    if v in ('', 'none'):
        return d
    if v in ('inf', '+inf', '∞'):
        return 'inf'
    if v in ('-inf', '−inf', '-∞'):
        return '-inf'
    return v


class RoiCurveDialog(QDialog):
    """从 ③ 页打开：给这个病例的 ROI 出 Z 谱 / LD 曲线。"""

    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.setWindowTitle('ROI 定量曲线')
        self.resize(1220, 800)
        self.data = None
        self._loading = True
        self.out_root = None
        self._build()
        self._load()

    # ================================================================== UI
    def _build(self):
        root = QVBoxLayout(self)
        self.head = QLabel('')
        self.head.setObjectName('muted')
        self.head.setWordWrap(True)
        root.addWidget(self.head)
        body = QHBoxLayout()
        body.setSpacing(8)

        left = QWidget()
        left.setFixedWidth(186)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        g1 = QGroupBox('层（留空=自动全勾）')
        v1 = QVBoxLayout(g1)
        self.ed_layers = QLineEdit('')
        self.ed_layers.setPlaceholderText('如 1,3,5；留空=全部已完成层')
        v1.addWidget(self.ed_layers)
        self.lst_layers = QListWidget()
        self.lst_layers.setToolTip('点一层看一层（预览）；左侧方框勾选决定导出哪些层')
        self.lst_layers.currentRowChanged.connect(self._on_layer_row)
        v1.addWidget(self.lst_layers, 1)
        lv.addWidget(g1, 1)
        g2 = QGroupBox('ROI')
        v2 = QVBoxLayout(g2)
        self.lst_rois = QListWidget()
        v2.addWidget(self.lst_rois, 1)
        lv.addWidget(g2, 1)
        body.addWidget(left, 0)

        mid = QWidget()
        mv = QVBoxLayout(mid)
        mv.setContentsMargins(0, 0, 0, 0)
        self.lab = QLabel('（还没画）')
        self.lab.setAlignment(Qt.AlignCenter)
        self.lab.setMinimumSize(600, 600)
        self.lab.setStyleSheet('background:#111;color:#888;')
        mv.addWidget(self.lab, 1)
        self.hint = QLabel('')
        self.hint.setObjectName('muted')
        self.hint.setWordWrap(True)
        mv.addWidget(self.hint)
        body.addWidget(mid, 1)

        right = QWidget()
        right.setFixedWidth(292)
        rv = QVBoxLayout(right)
        g = QGroupBox('选项')
        fg = QFormLayout(g)
        self.cb_trim = QCheckBox('边缘裁剪（连续 3 点递增剔除）')
        self.cb_trim.setChecked(True)
        fg.addRow(self.cb_trim)
        self.cb_neg = QComboBox()
        self.cb_neg.addItems(['正频偏', '负频偏', '全频偏'])
        fg.addRow('频偏方向', self.cb_neg)
        self.sp_dpi = QSpinBox()
        self.sp_dpi.setRange(72, 600)
        self.sp_dpi.setValue(150)
        fg.addRow('出图分辨率', self.sp_dpi)
        rv.addWidget(g)
        b_ref = QPushButton('刷新预览（当前层）')
        b_ref.setObjectName('primary')
        b_ref.clicked.connect(self._draw)
        rv.addWidget(b_ref)
        b_exp = QPushButton('导出选中层…')
        b_exp.setToolTip('把勾选的每一层各出一套（png/mat/xlsx），走与预览同一条链')
        b_exp.clicked.connect(self._export)
        rv.addWidget(b_exp)
        rv.addStretch(1)
        self.state = QLabel('')
        self.state.setObjectName('muted')
        self.state.setWordWrap(True)
        rv.addWidget(self.state)
        body.addWidget(right, 0)
        root.addLayout(body, 1)

    # ================================================================== 取数
    def _params(self):
        return dict(self.page.params())

    def _locate(self):
        """→ dict(proj, case, tag, version, layers{层号: V_exp_mask 路径}, roi_file)"""
        p = self._params()
        proj = self.page.window.proj()
        case = str(p.get('case') or '').strip()
        bc = str(p.get('b0correct') or '自校正')
        tag = B0_MAP.get(str(p.get('b0correct') or '自校正'), 'Self_corr')
        if not proj or not case:
            raise RuntimeError('③ 页先填好「病例名」和输出总目录 PROJ')
        rd = R.find_subdir(proj, '00_roi')
        roi_file = (rd / case / 'ROI_total.mat') if rd else None
        if roi_file is None or not roi_file.exists():
            raise RuntimeError(f'找不到 00_掩模\\{case}\\ROI_total.mat'
                               f'（先在 ⑧ 的掩模画布画器官 ROI）')
        d3 = R.find_subdir(proj, '03_post')
        cd = (d3 / case) if d3 else None
        if cd is None or not cd.is_dir():
            raise RuntimeError(f'找不到 03_CEST后处理\\{case}\\（先在 ③ 里跑完这一层）')
        layers = {}
        version = None
        for sub in sorted(cd.glob('*_interp_*')):
            for sl in sorted(sub.glob('slice*')):
                f = sl / tag / 'V_exp_mask.mat'
                if f.exists():
                    try:
                        n = int(sl.name[5:])
                    except ValueError:
                        continue
                    layers[n] = f
                    version = sub.name
        if not layers:
            raise RuntimeError(f'03_CEST后处理\\{case}\\ 里没有 slice*\\{tag}\\V_exp_mask.mat\n'
                               f'（先在 ③ 里把这个病例跑出来）')
        return dict(proj=Path(proj), case=case, tag=tag, version=version,
                    layers=layers, roi_file=roi_file)

    def _load(self):
        import cest_roi as CR
        try:
            d = self._locate()
        except Exception as e:                                   # noqa: BLE001
            self.data = None
            self.head.setText(f'⚠ {type(e).__name__}: {e}')
            self.hint.setText('补齐上面的缺项后重新打开本窗口（先 ⑧ 画 ROI，再在 ③ 跑出层）。')
            return
        self.data = d
        self._loading = True
        ld = sorted(d['layers'])
        self.lst_layers.clear()
        for n in ld:
            it = QListWidgetItem(f'slice{n}')
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            self.lst_layers.addItem(it)
        self.lst_layers.setCurrentRow(0)
        try:
            rois = CR.load_roi_total(d['roi_file'])
            self.lst_rois.clear()
            for k, r in enumerate(rois):
                it = QListWidgetItem(f'ROI{k + 1}（{np.asarray(r).size} 体素）')
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked)
                self.lst_rois.addItem(it)
        except Exception as e:                                   # noqa: BLE001
            self.head.setText(f'⚠ ROI_total 读不出：{e}')
            self._loading = False
            return
        self._loading = False
        self.head.setText(f'{d["case"]} · {d["version"]} · {d["tag"]} · '
                          f'已完成 {len(ld)} 层（{ld[0]}..{ld[-1]}）')
        self._draw()

    # ================================================================== 选择
    def _checked(self, lst, prefix=''):
        out = []
        for i in range(lst.count()):
            if lst.item(i).checkState() == Qt.Checked:
                txt = lst.item(i).text()
                out.append(int(txt.replace(prefix, '').split('（')[0].strip()
                               .replace('slice', '').replace('ROI', '')))
        return out

    def _sel_layers(self):
        txt = str(self.ed_layers.text() or '').replace('，', ',')
        if txt.strip():
            want = [int(v) for v in txt.split(',') if v.strip()]
            return [n for n in want if n in self.data['layers']] or sorted(self.data['layers'])
        got = [int(self.lst_layers.item(i).text().replace('slice', ''))
               for i in range(self.lst_layers.count())
               if self.lst_layers.item(i).checkState() == Qt.Checked]
        return got or sorted(self.data['layers'])

    def _sel_rois(self):
        got = [i + 1 for i in range(self.lst_rois.count())
               if self.lst_rois.item(i).checkState() == Qt.Checked]
        return got or [1]

    def _cfg(self, rois, save_mat, save_xlsx, save_png):
        p = self._params()
        return dict(trim=bool(self.cb_trim.isChecked()),
                    negorpos=NEG_MAP.get(self.cb_neg.currentText(), 0),
                    ppm=dict(l1=_f(p.get('l1', -0.6), -0.6), l2=-6.0, l3=-10.0,
                             h1=0.6, h2=6.0, h3=10.0),
                    par0=[0.0, -50.0, 20.0, 1.0],
                    bounds=([-np.inf, -np.inf, 0.0, -np.inf], [0.0, np.inf, 1.8, np.inf]),
                    rois=rois, dpi=int(self.sp_dpi.value()),
                    save_mat=save_mat, save_xlsx=save_xlsx, save_png=save_png)

    # ================================================================== 计算
    def _one_layer(self, layer, rois, cfg):
        import cest_roi as CR
        from cest_io import load_first
        f = self.data['layers'][layer]
        sl = f.parent.parent                          # ...\sliceN
        w = np.asarray(load_first(sl / 'w_offset.mat', ('w_offset', 'w')), float).ravel()
        z = np.asarray(load_first(f, ('V_exp_mask', 'V_exp')), float)
        if z.ndim == 2:
            z = z[:, :, None]
        va, xa = None, None
        fa = f.parent / 'V_as_mask.mat'
        if fa.exists():
            va = np.asarray(load_first(fa, ('V_as_mask', 'V_as')), float)
            fx = sl / 'x_as.mat'
            if fx.exists():
                xa = np.asarray(load_first(fx, ('x_as',)), float).ravel()
            else:
                va = None
        return CR.run_curves(z, va, w, xa, self.data['roi_file'], cfg['out'],
                             cfg, log=lambda s: None, progress=None)

    # ================================================================== 画
    def _on_layer_row(self, row):
        """点哪层看哪层（与画掩膜的层览一致）。"""
        if getattr(self, '_loading', False) or row < 0 or not self.data:
            return
        n = self._layer_of_row(row)
        if n is not None:
            self._draw(layer=n)

    def _layer_of_row(self, row):
        try:
            return int(self.lst_layers.item(row).text().replace('slice', ''))
        except Exception:                                        # noqa: BLE001
            return None

    def _draw(self, layer=None):
        if not self.data:
            return
        import tempfile
        lay = self._sel_layers()
        n = layer if layer in self.data['layers'] else (lay[0] if lay else None)
        if n is None:
            return
        cfg = self._cfg(self._sel_rois(), False, False, True)
        cfg['out'] = Path(tempfile.mkdtemp(prefix='cest_curve_'))
        try:
            res = self._one_layer(n, self._sel_rois(), cfg)
        except Exception as e:                                   # noqa: BLE001
            QMessageBox.warning(self, '画图失败', f'{type(e).__name__}: {e}')
            return
        pm = QPixmap(res['png'])
        if not pm.isNull():
            self.lab.setPixmap(pm.scaled(self.lab.size(), Qt.KeepAspectRatio,
                                         Qt.SmoothTransformation))
        self.hint.setText(f'预览：slice{n}（左侧点哪层看哪层；已勾选 {len(lay)} 层，'
                          f'导出时逐层各出一套）')
        self.state.setText('\n'.join(
            f'ROI{r["roi"]}：{r["n_vox"]} 体素，{r["ppm"]} ppm，'
            f'par={[round(v, 4) for v in r["par"]] if r["par"] else "拟合失败"}'
            for r in res['meta']['per_roi']))

    # ================================================================== 导出
    def _export(self):
        if not self.data:
            return
        import cest_roi as CR
        lay = self._sel_layers()
        rois = self._sel_rois()
        stamp = time.strftime('%Y%m%d_%H%M%S')
        base = (self.data['proj'] / R.cn_name('03_post') / self.data['case']
                / 'ROI曲线' / f'{stamp}_{self.data["tag"]}')
        n_ok, n_bad, outs = 0, 0, []
        for n in lay:
            cfg = self._cfg(rois, True, True, True)
            cfg['out'] = base / f'slice{n}'
            try:
                r = self._one_layer(n, rois, cfg)
                n_ok += 1
                outs.append(r['out_dir'])
            except Exception:                                    # noqa: BLE001
                n_bad += 1
        QMessageBox.information(
            self, '导出完成',
            f'共 {len(lay)} 层：成功 {n_ok}，失败 {n_bad}\n\n{base}')
