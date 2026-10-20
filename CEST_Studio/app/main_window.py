# -*- coding: utf-8 -*-
"""app/main_window.py —— 主窗口：按模块的输入根 + 识别 + 输出总目录 + 模块导航 + 全局日志。

输入目录「下沉」到每一页：
  * ① DICOM 重建        → 输入根 = 原始 DICOM 目录
  * ⑧④② 等数据处理模块 → 输入根 = 输出总目录 PROJ（不设就默认用它）
  切页各自载入、互不覆盖；拖拽区改的是**当前模块**的输入根，
  旁边的「应用到所有数据处理模块」勾上就一次改完下游。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMessageBox, QPushButton, QSplitter,
                               QStackedWidget, QVBoxLayout, QWidget)

from app import core_path
from app.modules import registry
from app.pages.base_page import ModulePage
from app.widgets.drop_zone import DropZone
from app.widgets.log_panel import LogPanel
from config import settings
from detect import chain
from detect.scanner import Detection, detect
from jobs.ctx import JobContext


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self._ver = core_path.version()
        self.setWindowTitle('CEST Studio —— CEST 处理链桌面版'
                            + (f'   v{self._ver}' if self._ver else ''))
        self.det: Detection | None = None          # 当前模块的识别结果
        self._cfg = settings.load()
        self._start_module = str(self._cfg.get('module') or '')
        # 按模块存输入根：{模块 id: 目录}
        self._inputs = {str(k): str(v) for k, v in (self._cfg.get('inputs') or {}).items()}
        if not self._inputs and self._cfg.get('input_root'):
            # 兼容旧的「单一输入根」配置：归到上次停的那个模块
            _mid = str(self._cfg.get('module') or registry.ALL[0].id)
            self._inputs[_mid] = self._cfg['input_root']
        self._dets: dict = {}                      # {模块 id: Detection}（切页复用）
        self._cur_mid = registry.ALL[0].id
        self._build()
        core_path.install()
        self.log('CEST Studio 已启动。' + (f'（v{self._ver}）' if self._ver else ''))
        self.log('核心层：' + core_path.fingerprint())
        # 恢复上次的输出总目录（触发 _on_proj_changed）
        if self._cfg.get('out_root'):
            self.out_edit.setText(self._cfg['out_root'])
        # 恢复上次停在哪一页（触发 _on_nav → 按该模块的输入根识别）
        row = next((i for i, m in enumerate(registry.ALL)
                    if m.id == self._start_module), 0)
        self.nav.setCurrentRow(row)

    # ------------------------------------------------------------------ UI
    def _build(self):
        central = QWidget()
        central.setObjectName('central')
        self.setCentralWidget(central)
        v = QVBoxLayout(central)
        v.setContentsMargins(14, 12, 14, 10)
        v.setSpacing(8)

        # ---- 顶部：拖拽（= 设置当前模块的输入） + 输出总目录 ----
        top = QHBoxLayout()
        top.setSpacing(12)
        left_col = QVBoxLayout()
        left_col.setSpacing(4)
        self.drop_title = QLabel('当前模块输入')
        self.drop_title.setObjectName('sectionTitle')
        left_col.addWidget(self.drop_title)
        self.drop = DropZone()
        self.drop.path_selected.connect(self.set_input)
        left_col.addWidget(self.drop, 1)
        top.addLayout(left_col, 3)

        right = QVBoxLayout()
        right.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(6)
        lab_out = QLabel('输出总目录(PROJ)：')
        lab_out.setObjectName('sectionTitle')
        row.addWidget(lab_out)
        self.out_edit = QLineEdit()
        self.out_edit.setPlaceholderText(r'如 C:\Users\Administrator\Desktop\2026\10\结果目录')
        self.out_edit.textChanged.connect(self._on_proj_changed)
        b = QPushButton('选择…')
        b.clicked.connect(self._pick_proj)
        b_rst = QPushButton('初始化')
        b_rst.setToolTip('清空识别结果 / 页面参数 / 记忆的目录，回到刚启动的样子（下次打开也是初始状态）')
        b_rst.clicked.connect(self.reset_all)
        row.addWidget(self.out_edit, 1)
        row.addWidget(b)
        row.addWidget(b_rst)
        right.addLayout(row)
        self.summary = QLabel('（拖入输入总目录后自动识别）')
        self.summary.setObjectName('summary')
        self.summary.setWordWrap(True)
        right.addWidget(self.summary)

        self.chk_all = QCheckBox('应用到所有数据处理模块（⑧③④⑤②）')
        self.chk_all.setToolTip('勾上后，这里设的输入目录会同时写到 ⑧ 去噪掩模 / ④ 逐体素LD '
                                '/ ② 多层图谱 等模块（① 不受影响）。\n'
                                '不勾则只改当前模块——各模块输入互不覆盖。')
        right.addWidget(self.chk_all)

        # 上级目录时：列出下级数据集，供直接往下选
        self.child_row = QWidget()
        ch = QHBoxLayout(self.child_row)
        ch.setContentsMargins(0, 0, 0, 0)
        ch.setSpacing(6)
        lab_k = QLabel('下级数据集：')
        lab_k.setObjectName('sectionTitle')
        self.child_box = QComboBox()
        self.child_box.setMinimumWidth(260)
        self.child_box.activated.connect(self._on_child_pick)
        ch.addWidget(lab_k)
        ch.addWidget(self.child_box, 1)
        right.addWidget(self.child_row)
        self.child_row.setVisible(False)
        top.addLayout(right, 4)
        v.addLayout(top)

        # ---- 中部：导航 + 页面 ----
        mid = QSplitter(Qt.Orientation.Horizontal)
        self.nav = QListWidget()
        self.nav.setObjectName('nav')
        self.nav.setFixedWidth(178)
        self.nav.currentRowChanged.connect(self._on_nav)
        self.pages = QStackedWidget()
        for m in registry.ALL:
            self.nav.addItem(QListWidgetItem(f'{m.badge} {m.title}'))
            self.pages.addWidget(ModulePage(m, self))
        mid.addWidget(self.nav)
        mid.addWidget(self.pages)
        mid.setStretchFactor(0, 0)
        mid.setStretchFactor(1, 1)
        mid.setChildrenCollapsible(False)     # 导航/页面都不允许被拖到 0 宽
        mid.setCollapsible(0, False)
        mid.setCollapsible(1, False)

        # ---- 底部：全局日志（竖向分隔条，可上下拖高；不再锁 130）----
        self.log_title = QLabel('全局日志')
        self.log_title.setObjectName('sectionTitle')
        self.log_panel = LogPanel()
        log_box = QWidget()
        log_box.setObjectName('logBox')
        lbv = QVBoxLayout(log_box)
        lbv.setContentsMargins(0, 0, 0, 0)
        lbv.setSpacing(4)
        lbv.addWidget(self.log_title)
        lbv.addWidget(self.log_panel, 1)
        vs_log = QSplitter(Qt.Orientation.Vertical)
        vs_log.setObjectName('vsplitLog')
        vs_log.addWidget(mid)
        vs_log.addWidget(log_box)
        vs_log.setChildrenCollapsible(False)
        vs_log.setCollapsible(0, False)
        vs_log.setCollapsible(1, False)
        vs_log.setStretchFactor(0, 1)
        vs_log.setStretchFactor(1, 0)
        vs_log.setSizes([620, 130])
        v.addWidget(vs_log, 1)

        self.nav.setCurrentRow(0)
        self.refresh_modules()

    # -------------------------------------------------------------- 输入根
    def current_id(self) -> str:
        r = self.nav.currentRow()
        if 0 <= r < len(registry.ALL):
            return registry.ALL[r].id
        return self._cur_mid

    def input_root_for(self, mid=None) -> str:
        """某模块的输入根；数据处理模块没设过就默认 = 输出总目录 PROJ。"""
        mid = mid or self.current_id()
        root = self._inputs.get(mid) or ''
        if not root and mid in chain.DATA_MODULES:
            root = self.proj()
        return root

    def input_root(self) -> str:
        return self.input_root_for()

    def _det_for(self, mid):
        """某模块输入根的识别结果（缓存；set_input / 改 PROJ 时清掉）。"""
        if mid in self._dets:
            return self._dets[mid]
        root = self.input_root_for(mid)
        d = detect(root) if root else None
        self._dets[mid] = d
        return d

    def _save_cfg(self):
        self._cfg['inputs'] = dict(self._inputs)
        self._cfg['input_root'] = self._inputs.get(self.current_id(), '')
        settings.save(self._cfg)

    def _apply_input_ui(self, det):
        """把当前模块的识别结果画到顶部（拖拽区文字 / 摘要 / 下级数据集）。"""
        self._dets[self.current_id()] = det
        self.det = det
        if det is None:
            self.drop.set_text('把「输入总目录」拖到这里，或点此选择')
            self.summary.setText('（拖入输入总目录后自动识别）')
            self.child_row.setVisible(False)
            self.child_box.clear()
            return
        self.drop.set_text(det.path)
        self.summary.setText(f'<b>{det.label}</b><br>' + '<br>'.join(det.lines))
        kids = (det.meta or {}).get('children') or []
        self.child_box.clear()
        for k in kids:
            self.child_box.addItem(f'{k["name"]}　—　{k["label"]}', k['path'])
        self.child_row.setVisible(bool(kids))
        if kids:
            self.child_box.setCurrentIndex(0)

    # -------------------------------------------------------------- 事件
    def _on_child_pick(self, idx):
        if idx < 0:
            return
        p = self.child_box.itemData(idx)
        if p:
            self.log(f'往下进入：{p}')
            self.set_input(p, confirm=False)

    # ------------------------------------------------------- 选完总目录先确认
    def _confirm_input(self, det: Detection, mid=None) -> bool:
        m = registry.get(mid or self.current_id())
        lines = [f'为「{m.badge} {m.title}」设置输入根：', f'识别：{det.label}', '']
        lines += [f'　{x}' for x in det.lines]
        if det.kind == 'container':
            lines += ['', '（这是上级目录 → 确认后在下面的「下级数据集」里再选一个）']
        elif det.kind == 'unknown':
            lines += ['', '（没认出来 —— 仍可继续，比如用【浏览 / 选择序列…】手动挑）']
        if mid and mid in chain.DATA_MODULES:
            lines += ['', f'（这是数据处理模块 → 建议直接用项目目录 PROJ：{self.proj() or "还没选"}）']
        else:
            lines += ['', f'输出总目录：{self.proj() or "（还没选）"}']
        box = QMessageBox(self)
        box.setWindowTitle('确认输入根')
        box.setIcon(QMessageBox.Icon.Question)
        box.setText('\n'.join(lines))
        b_ok = box.addButton('确认，就用它', QMessageBox.ButtonRole.AcceptRole)
        box.addButton('取消', QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is b_ok

    def _pick_proj(self):
        d = QFileDialog.getExistingDirectory(self, '选择输出总目录（PROJ）',
                                            self.out_edit.text() or '')
        if d:
            self.out_edit.setText(d)

    def _on_nav(self, row):
        if row < 0:
            return
        self.pages.setCurrentIndex(row)
        m = registry.ALL[row]
        self._cur_mid = m.id
        det = self._det_for(m.id)
        self.det = det
        self._apply_input_ui(det)
        is_data = m.id in chain.DATA_MODULES
        _what = (getattr(m, 'input_label', '') or
                 ('项目目录 PROJ' if is_data else '原始 DICOM'))
        self.drop_title.setText(f'当前模块输入：{m.badge} {m.title}（{_what}）')
        if det is not None and m.id not in det.modules and m.accepts:
            self.log(f'提示：当前识别（{det.label}）下 {m.badge} {m.title} 不直接可用，'
                     f'参数可能不全。')
        self.pages.currentWidget().attach(det, self.proj())
        self._cfg['module'] = m.id
        self._save_cfg()

    def _on_proj_changed(self):
        new = self.out_edit.text().strip()
        old = getattr(self, '_prev_proj', '')
        if old and new and old != new:
            # 显式指向旧 PROJ 的模块 → 跟着改（不然会留着老路径）
            for k, v in list(self._inputs.items()):
                if v == old:
                    self._inputs[k] = new
                    self._dets.pop(k, None)
        self._prev_proj = new
        self._cfg['out_root'] = new
        settings.save(self._cfg)
        proj = new
        self.refresh_modules()
        if not proj or not Path(proj).is_dir():
            return
        # 数据处理模块的输入根 = PROJ（默认）→ 识别结果全变，重算
        self._dets.clear()
        self.det = self._det_for(self.current_id())
        self._apply_input_ui(self.det)
        self.refresh_modules()
        w = self.pages.currentWidget()
        w.refresh_chain()
        w.refresh_preview(self.proj())
        w.refresh_extra()

    # ------------------------------------------------------------ 公共方法
    def proj(self) -> str:
        return self.out_edit.text().strip()

    def job_ctx(self, proj) -> JobContext:
        return JobContext(proj=proj, det=self.det)

    def log(self, s: str):
        self.log_panel.append_line(s)

    def goto(self, mid: str):
        """跳到某个模块页（数据链自检里点一下用）。"""
        for i, m in enumerate(registry.ALL):
            if m.id == mid:
                self.nav.setCurrentRow(i)
                return

    def set_input(self, path: str, confirm: bool = True, mid=None):
        path = str(path).strip()
        if not path:
            return
        mid = mid or self.current_id()
        det = detect(path)
        if confirm and not self._confirm_input(det, mid):
            self.log('已取消：输入根未变更。')
            return
        targets = [mid]
        if self.chk_all.isChecked() and mid in chain.DATA_MODULES:
            targets = list(chain.DATA_MODULES)
        for t in targets:
            self._inputs[t] = path
            self._dets.pop(t, None)
        self._save_cfg()
        self.log('—' * 40)
        self.log(f'设置输入根（{"、".join(targets)}）：{path}')
        for ln in det.lines:
            self.log('  ' + ln)
        if mid == self.current_id():
            self._apply_input_ui(self._det_for(mid))
        self.refresh_modules()
        self.pages.currentWidget().attach(self.det, self.proj())

    def re_detect(self):
        mid = self.current_id()
        root = self.input_root_for(mid)
        if not root:
            self.log('这一页还没有输入目录。')
            return
        self._dets.pop(mid, None)
        self.set_input(root, confirm=True, mid=mid)

    def reset_all(self):
        """初始化：清掉识别结果 / 各页参数 / 记忆的目录 → 回到刚启动的样子。

        同时把 config\\settings.json 重置为默认值，所以**下次启动也是初始的**。
        """
        r = QMessageBox.question(
            self, '初始化',
            '确定要初始化吗？\n\n会把：\n'
            '　· 各模块的输入目录 / 识别结果\n'
            '　· 输出总目录（PROJ）\n'
            '　· 各页参数与结果面板\n'
            '　· 记忆的目录（settings.json）\n'
            '全部清空。不会删任何数据文件。',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if r != QMessageBox.StandardButton.Yes:
            return

        self.det = None
        self._inputs = {}
        self._dets = {}
        self.chk_all.setChecked(False)
        self.drop.set_text('把「输入总目录」拖到这里，或点此选择')
        self.summary.setText('（拖入输入总目录后自动识别）')
        self.child_row.setVisible(False)
        self.child_box.clear()
        self.out_edit.blockSignals(True)
        self.out_edit.setText('')
        self.out_edit.blockSignals(False)
        for i in range(self.pages.count()):
            w = self.pages.widget(i)
            if hasattr(w, 'reset'):
                w.reset()
        self._cfg = dict(settings.DEFAULTS)
        settings.save(self._cfg)
        self.refresh_modules()
        self.log_panel.clear()
        self.log('已初始化：识别结果 / 参数 / 记忆的目录都清空了，回到刚启动的样子。')

    def refresh_modules(self):
        """按识别结果 + PROJ 现有内容，刷新导航标记。"""
        avail = set(self.det.modules) if self.det else {m.id for m in registry.ALL}
        proj = self.proj()
        for i, m in enumerate(registry.ALL):
            try:
                from detect.rules import cn_name, find_subdir
                _d = (find_subdir(proj, m.out_subdir)
                      or Path(proj) / cn_name(m.out_subdir)) if proj else None
            except Exception:                            # noqa: BLE001
                _d = (Path(proj) / m.out_subdir) if proj else None
            done = bool(_d) and _d.exists()
            mark = '√' if done else ('●' if m.id in avail else '○')
            self.nav.item(i).setText(f'{m.badge} {mark} {m.title}')
