# -*- coding: utf-8 -*-
"""cest_agent.gui_page —— 智能体窗口（PySide6）。

★ 全局只有**一个**智能体窗口：从任何模块页点「智能体」打开的都是它，
  只是把「当前模块」作为起点带进去。窗口里可以改模块、也可以串整链。

放在 agent 目录里（独立目录原则），GUI 侧只留几行挂载点，交付包不带
cest-agent 时 GUI 会优雅提示「未安装智能体」，不会崩。

布局：
    左：输入/输出目录（自动识别） + 模式（单模块 / 整链） + 病例 + 操作
    右：对话区（说明 / 需要你做的事 / 诊断） + 明细日志 + 追问
"""
from __future__ import annotations

import threading
import time
import traceback
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFileDialog, QFormLayout,
                               QFrame, QGridLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                               QProgressBar, QPushButton, QRadioButton,
                               QScrollArea, QSizePolicy, QSplitter,
                               QTextBrowser, QVBoxLayout, QWidget)

from . import agent, chat, config, intent, layout, plan, report, runner
from .report import STAGE_NAME

STAGE_ORDER = ('m_import', 'm1_recon', 'm_roi', 'm3_post', 'm4_ld', 'm2_display')
STAGE_TITLE = {
    'm_import': '⓪ 导入重建', 'm1_recon': '① 重建', 'm_roi': '⑧ 去噪掩模',
    'm3_post': '③ 后处理', 'm4_ld': '④ 逐体素LD', 'm2_display': '② 展示',
}
ST_COLOR = {
    'done': ('#1e8449', 'filled'), 'todo': ('#b9770e', 'ring'),
    'blocked': ('#c0392b', 'filled'), 'fail': ('#c0392b', 'filled'),
    'partial': ('#b9770e', 'half'), 'running': ('#185FA5', 'filled'),
    'idle': ('#8a8a8a', 'dot'),
}
CASE_CN = {'ok': '全部完成', 'partial': '产物不全', 'fail': '失败',
           'blocked': '卡在前置', 'cancelled': '已取消'}

# 可以单独跑模块时提供的入口（⑧ 要手画掩模，不作为可执行模块）
RUNNABLE = ('m_import', 'm1_recon', 'm3_post', 'm4_ld', 'm5_prf',
            'm6_prf_analysis', 'm2_display', 'm7_compare', 'm0_check')


def _module_title(mid: str) -> str:
    try:
        from app.modules import registry
        m = registry.get(mid)
        if m is not None:
            return f'{m.badge} {m.title}'
    except Exception:                                    # noqa: BLE001
        pass
    return STAGE_TITLE.get(mid, mid)


class AgentWorker(QThread):
    """工作线程：跑 runner（整链或单模块），事件通过信号回主线程。"""

    sig_event = Signal(object)
    sig_done = Signal(object)
    sig_freq = Signal(str, object)          # 需要人工核对频率表

    def __init__(self, mode, proj, cases, stages, overrides, force, io,
                 mid=None, parent=None):
        super().__init__(parent)
        self.mode = mode                    # 'chain' | 'single'
        self.proj = proj
        self.cases = cases
        self.stages = stages
        self.overrides = overrides
        self.force = force
        self.io = io or {}
        self.mid = mid
        self._cancel = False
        self._freq_gate: dict = {}
        self._freq_wait: dict = {}
        self.result = None

    def cancel(self):
        self._cancel = True
        for ev in list(self._freq_wait.values()):
            ev.set()

    def set_freq_answer(self, case, answer):
        self._freq_gate[case] = answer
        ev = self._freq_wait.get(case)
        if ev is not None:
            ev.set()

    def _confirm(self, case, info):
        if self._cancel:
            return None
        gate = threading.Event()
        self._freq_wait[case] = gate
        self._freq_gate.pop(case, None)
        self.sig_freq.emit(case, info)
        gate.wait()
        return self._freq_gate.get(case)

    def run(self):
        try:
            if self.mode == 'single':
                self.result = runner.run_module(
                    self.mid, self.proj, self.cases[0],
                    overrides=self.overrides,
                    on_event=self.sig_event.emit,
                    cancel_flag=lambda: self._cancel, io=self.io)
            else:
                self.result = runner.run_chain(
                    self.proj, self.cases, self.stages,
                    overrides=self.overrides, on_event=self.sig_event.emit,
                    force=self.force, cancel_flag=lambda: self._cancel,
                    confirm_freq=self._confirm, io=self.io)
        except Exception as e:                           # noqa: BLE001
            self.result = dict(
                type='done', ok=False, run_id='', proj=self.proj, sec=0,
                summary=f'智能体自己出错了：{type(e).__name__}: {e}',
                cases=[], pending_human=[], diagnoses=[],
                failures=[dict(case='', stage='',
                               error=traceback.format_exc())])
        self.sig_done.emit(self.result)


class AgentChatWorker(QThread):
    """大模型对话线程：模型决定调什么工具，工具在**本线程**里真跑。

    ★ 长任务（③ 后处理几分钟）也在这个线程里跑，所以界面不会卡。
    频率表核对仍复用 AgentWorker 那套信号：发出去、阻塞等主线程回填。
    """

    sig_status = Signal(str)                # 思考/工具进度（一句话）
    sig_say = Signal(str)                   # 最终人话
    sig_event = Signal(object)              # runner 事件（日志/进度）
    sig_freq = Signal(str, object)          # 频率表核对
    sig_code = Signal(str)                  # 要跑代码，请主线程确认
    sig_fail = Signal(str)                  # 出错（GUI 据此退回本地规则）
    sig_proj = Signal(str, object)          # 模型改了 PROJ，请主线程同步界面

    def __init__(self, sess, text, parent=None):
        super().__init__(parent)
        self.sess = sess
        self.text = text
        self._cancel = False
        self._freq_gate: dict = {}
        self._freq_wait: dict = {}
        self._code_gate = None
        self._code_answer = None
        # 把 session 的回调接到本线程信号
        sess.emit = self.sig_event.emit
        sess.cancel = lambda: self._cancel
        sess.confirm_freq = self._confirm
        sess.confirm_python = self._confirm_code
        # ★ 必须走信号：本线程重写了 run()，没有事件循环，
        #   QTimer.singleShot 在里面永远不会触发（之前界面不同步就是这个原因）
        sess.on_project = lambda p, src: self.sig_proj.emit(str(p or ''), dict(src or {}))

    def cancel(self):
        self._cancel = True
        for ev in list(self._freq_wait.values()):
            ev.set()
        if self._code_gate is not None:
            self._code_gate.set()

    def set_freq_answer(self, case, answer):
        self._freq_gate[case] = answer
        ev = self._freq_wait.get(case)
        if ev is not None:
            ev.set()

    def set_code_answer(self, ok: bool):
        self._code_answer = bool(ok)
        if self._code_gate is not None:
            self._code_gate.set()

    def _confirm_code(self, code: str) -> bool:
        if self._cancel:
            return False
        gate = threading.Event()
        self._code_gate = gate
        self._code_answer = None
        self.sig_code.emit(str(code))
        gate.wait()
        return bool(self._code_answer)

    def _confirm(self, case, info):
        if self._cancel:
            return None
        gate = threading.Event()
        self._freq_wait[case] = gate
        self._freq_gate.pop(case, None)
        self.sig_freq.emit(case, info)
        gate.wait()
        return self._freq_gate.get(case)

    def run(self):
        try:
            text = self.sess.send(self.text, on_say=self.sig_status.emit)
            self.sig_say.emit(text)
        except Exception as e:                           # noqa: BLE001
            self.sig_fail.emit(f'{type(e).__name__}: {e}')


# ============================================================ 聊天区（气泡式）
def _h_unescape(s: str) -> str:
    import html as _h
    return _h.unescape(str(s or ''))


def _md_inline(s: str) -> str:
    """极简 Markdown 行内 → HTML：先转义，再还原 **粗体** `代码` 和路径。"""
    import html as _h
    import re as _re
    out = _h.escape(str(s))
    out = _re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', out)
    out = _re.sub(r'`([^`]+?)`',
                  r'<span style="background:#eef1f6;padding:0 3px;'
                  r'border-radius:3px;">\1</span>', out)
    return out


def _md_block(text: str) -> str:
    """把一段模型输出转成气泡内的 HTML（支持 ```代码块``` / - 列表 / 空行分段）。"""
    import html as _h
    import re as _re
    s = str(text or '').replace('\r\n', '\n').replace('\r', '\n')
    parts = _re.split(r'```', s)
    buf = []
    for i, seg in enumerate(parts):
        if i % 2 == 1:                      # 代码块
            body = _h.escape(seg.strip('\n'))
            buf.append(
                '<pre style="margin:6px 0;padding:8px 10px;background:#f4f6fa;'
                'border-left:3px solid #b9c4d6;border-radius:5px;'
                'font-family:Consolas,\'Cascadia Mono\',monospace;font-size:12px;'
                'white-space:pre-wrap;color:#1f2937;">' + body + '</pre>')
            continue
        for line in seg.split('\n'):
            if not line.strip():
                buf.append('<div style="height:6px;"></div>')
                continue
            m = _re.match(r'^(\s*)[·•\-*]\s+(.*)$', line)
            if m:
                ind = 14 + len(m.group(1)) * 6
                buf.append(
                    f'<div style="margin:2px 0 2px {ind}px;text-indent:-12px;">'
                    f'· {_md_inline(m.group(2))}</div>')
                continue
            m = _re.match(r'^(\s*)(#{1,4})\s+(.*)$', line)
            if m:
                buf.append('<div style="margin:8px 0 3px 0;font-weight:600;">'
                           + _md_inline(m.group(3)) + '</div>')
                continue
            buf.append('<div style="margin:2px 0;">' + _md_inline(line) + '</div>')
    return ''.join(buf)


class ChatBubble(QFrame):
    """一条消息。role: user / agent / sys / tool。"""

    _STYLE = {
        'user':  ('#3b6ea5', '#eaf1fa'),      # 你：右侧浅蓝
        'agent': ('#334155', '#ffffff'),      # 智能体：左侧白卡
        'sys':   ('#6b7280', '#f6f7f9'),      # 系统/过程：灰
        'tool':  ('#7b8794', '#fbfcfd'),      # 工具调用：更淡
        'warn':  ('#8a6120', '#fff8e8'),
        'err':   ('#a33a2c', '#fdf0ee'),
        'ok':    ('#1e7a4d', '#eefaf2'),
    }
    _WHO = {'user': '你', 'agent': 'CEST 智能体', 'sys': '系统',
            'tool': '工具', 'warn': '提示', 'err': '错误', 'ok': '完成'}

    def __init__(self, role: str, html_body: str, stamp: str = '',
                 parent=None):
        super().__init__(parent)
        fg, bg = self._STYLE.get(role, self._STYLE['agent'])
        self.setObjectName('chatBubble')
        self.setStyleSheet(
            f'QFrame#chatBubble{{background:{bg};border:1px solid #e3e8ef;'
            f'border-radius:10px;}}')
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 8, 12, 10)
        v.setSpacing(3)
        head = QHBoxLayout()
        head.setSpacing(6)
        who = QLabel(self._WHO.get(role, role))
        who.setStyleSheet(f'color:{fg};font-size:12px;font-weight:600;'
                          f'background:transparent;border:none;')
        head.addWidget(who)
        head.addStretch(1)
        if stamp:
            t = QLabel(stamp)
            t.setStyleSheet('color:#9aa4b2;font-size:11px;background:transparent;'
                            'border:none;')
            head.addWidget(t)
        v.addLayout(head)
        body = QLabel()
        body.setObjectName('bubbleBody')
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setWordWrap(True)
        body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setStyleSheet(
            'QLabel#bubbleBody{background:transparent;border:none;padding:0;'
            'font-family:"Microsoft YaHei UI","Segoe UI",sans-serif;'
            f'font-size:13px;color:{fg};}}')
        body.setText(f'<div style="line-height:1.75;">{html_body}</div>')
        body.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Minimum)
        self._body = body
        v.addWidget(body)
        self._fitted = 0
        # 用于估算「这条消息需要多宽」：把 HTML 标签剥掉，按最长行量一遍
        import re as _re
        self._plain = _re.sub(r'<[^>]+>', '', str(html_body)).replace('&nbsp;', ' ')
        self._plain = _h_unescape(self._plain)

    def _need_width(self) -> int:
        """文字不换行时需要的宽度（最多取最长那行），用来决定气泡宽窄。"""
        from PySide6.QtGui import QFontMetrics
        fm = QFontMetrics(self._body.font())
        lines = [ln for ln in self._plain.split('\n') if ln.strip()] or ['']
        widest = max(fm.horizontalAdvance(ln) for ln in lines)
        return widest + 34

    def apply_width(self, avail: int, frac: float):
        want = min(int(avail * frac), max(240, self._need_width()))
        self.setFixedWidth(want)
        self._fit()

    def _fit(self):
        """按当前宽度重算文字高度（QLabel 换行后必须显式给 min-height）。"""
        w = max(120, self.width() - 26)
        if w == self._fitted:
            return
        h = self._body.heightForWidth(w)
        if h > 0:
            self._body.setMinimumHeight(h)
            self._fitted = w

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()


class ChatView(QScrollArea):
    """滚动区 + 垂直堆叠的气泡列表。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._host = QWidget()
        self._host.setStyleSheet('background:#f7f9fc;')
        self._v = QVBoxLayout(self._host)
        self._v.setContentsMargins(10, 10, 10, 10)
        self._v.setSpacing(8)
        self._v.addStretch(1)
        self.setWidget(self._host)
        self._n = 0

    # ---- 追加一条 ----
    def add(self, role: str, text: str, html: bool = False):
        stamp = time.strftime('%H:%M')
        body = text if html else _md_block(text)
        b = ChatBubble(role, body, stamp)
        b._role = role
        # 用户消息靠右、智能体靠左，像正常聊天软件
        wrap = QWidget()
        wrap.setStyleSheet('background:transparent;')
        hv = QHBoxLayout(wrap)
        hv.setContentsMargins(0, 0, 0, 0)
        hv.setSpacing(0)
        if role == 'user':
            hv.addStretch(1)
            hv.addWidget(b, 0)
        else:
            hv.addWidget(b, 0)
            hv.addStretch(1)
        b._wrap = wrap
        self._apply_width(b)
        self._v.insertWidget(self._v.count() - 1, wrap)
        self._n += 1
        QTimer.singleShot(0, self._scroll_end)
        return b

    def _apply_width(self, b: ChatBubble):
        avail = max(260, self.viewport().width() - 20)
        frac = 0.70 if getattr(b, '_role', '') == 'user' else 0.82
        b.apply_width(avail, frac)

    def _scroll_end(self):
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        for i in range(self._v.count()):
            w = self._v.itemAt(i).widget()
            if w is None:
                continue
            b = w.findChild(ChatBubble)
            if b is not None:
                self._apply_width(b)
                b._fit()

    def clear_all(self):
        while self._v.count() > 1:
            it = self._v.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        self._n = 0


class _StatusDot(QWidget):
    """纯绘制的状态点（不走字体，避免字形回退成方框）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self._color = '#8a8a8a'
        self._kind = 'dot'

    def set_state(self, color, kind):
        self._color, self._kind = color, kind
        self.update()

    def paintEvent(self, _e):
        from PySide6.QtCore import Qt as _Qt
        from PySide6.QtGui import QColor, QPainter, QPen
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        c = QColor(self._color)
        cx, cy, r = 7.0, 7.0, 4.2
        k = self._kind
        if k == 'filled':
            p.setPen(_Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawEllipse(int(cx - r), int(cy - r), int(2 * r), int(2 * r))
        elif k == 'ring':
            p.setBrush(_Qt.BrushStyle.NoBrush)
            p.setPen(QPen(c, 1.6))
            p.drawEllipse(int(cx - r), int(cy - r), int(2 * r), int(2 * r))
        elif k == 'half':
            p.setPen(_Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawEllipse(int(cx - r), int(cy - r), int(2 * r), int(2 * r))
            p.setBrush(QColor('#ffffff'))
            p.drawPie(int(cx - r), int(cy - r), int(2 * r), int(2 * r), 0, 180 * 16)
        else:
            p.setPen(_Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawEllipse(int(cx - 2), int(cy - 2), 4, 4)
        p.end()


class StageRow(QWidget):
    def __init__(self, mid, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 1, 0, 1)
        h.setSpacing(6)
        self.dot = _StatusDot()
        self.name = QLabel(STAGE_TITLE.get(mid, mid))
        self.name.setMinimumWidth(80)
        self.detail = QLabel('')
        self.detail.setObjectName('muted')
        self.detail.setWordWrap(True)
        h.addWidget(self.dot)
        h.addWidget(self.name)
        h.addWidget(self.detail, 1)
        self.mid = mid
        self.set_status('idle')

    def set_status(self, status, detail=''):
        color, kind = ST_COLOR.get(status, ST_COLOR['idle'])
        self.dot.set_state(color, kind)
        self.dot.setToolTip(status)
        self.detail.setText(detail)
        self.detail.setStyleSheet(f'color:{color};' if detail else '')


class AgentPanel(QWidget):
    """智能体面板。main = MainWindow。"""

    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.main = main
        self._worker = None
        self._last_result = None
        self._rows = {}
        self._io = {}
        self._cfg = config.load()
        self._pending_loose = []      # 链跑完后要接着跑的 ⑤⑥⑦
        self._loose_queue = []        # 纯 ⑤⑥⑦ 的逐个执行队列
        self._loose_all = []
        self._chat: agent.Session | None = None
        self._chat_worker = None
        self._build()

    # ------------------------------------------------------------------ UI
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(10)
        title = QLabel('智能体')
        title.setObjectName('pageTitle')
        head.addWidget(title)
        self.lbl_env = QLabel('')
        self.lbl_env.setObjectName('muted')
        head.addWidget(self.lbl_env, 1)
        root.addLayout(head)

        split = QSplitter(Qt.Orientation.Horizontal)

        # ---------------- 左栏 ----------------
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 4, 0)
        lv.setSpacing(6)

        # ---- 目录 ----
        g_io = QGroupBox('目录（自动识别）')
        iv = QVBoxLayout(g_io)
        iv.setSpacing(4)

        r1 = QHBoxLayout()
        r1.setSpacing(6)
        lab1 = QLabel('输入')
        lab1.setMinimumWidth(38)
        r1.addWidget(lab1)
        self.ed_in = QLineEdit()
        self.ed_in.setPlaceholderText('原始 DICOM 目录，或某个病例/结果目录')
        self.ed_in.editingFinished.connect(self._on_io_changed)
        r1.addWidget(self.ed_in, 1)
        b_in = QPushButton('选择…')
        b_in.setFixedWidth(58)
        b_in.clicked.connect(self._pick_in)
        r1.addWidget(b_in)
        iv.addLayout(r1)

        r2 = QHBoxLayout()
        r2.setSpacing(6)
        lab2 = QLabel('输出')
        lab2.setMinimumWidth(38)
        r2.addWidget(lab2)
        self.ed_proj = QLineEdit()
        self.ed_proj.setPlaceholderText('输出总目录 PROJ（留空则用主窗口的）')
        self.ed_proj.editingFinished.connect(self._on_io_changed)
        r2.addWidget(self.ed_proj, 1)
        b_out = QPushButton('选择…')
        b_out.setFixedWidth(58)
        b_out.clicked.connect(self._pick_out)
        r2.addWidget(b_out)
        iv.addLayout(r2)

        self.lbl_detect = QLabel('')
        self.lbl_detect.setObjectName('muted')
        self.lbl_detect.setWordWrap(True)
        iv.addWidget(self.lbl_detect)

        hc = QHBoxLayout()
        hc.setSpacing(6)
        labc = QLabel('病例')
        labc.setMinimumWidth(38)
        hc.addWidget(labc)
        self.cmb_case = QComboBox()
        self.cmb_case.currentTextChanged.connect(lambda _t: self._refresh_preview())
        hc.addWidget(self.cmb_case, 1)
        b_all = QPushButton('全部')
        b_all.setToolTip('不选单个病例，处理 PROJ 下的全部病例')
        b_all.setFixedWidth(48)
        b_all.clicked.connect(lambda: self.cmb_case.setCurrentIndex(0))
        hc.addWidget(b_all)
        iv.addLayout(hc)
        lv.addWidget(g_io)

        # ---- 模式 ----
        g_mode = QGroupBox('跑什么')
        mv = QVBoxLayout(g_mode)
        mv.setSpacing(4)
        hmm = QHBoxLayout()
        hmm.setSpacing(10)
        self.rb_single = QRadioButton('只跑一个模块')
        self.rb_chain = QRadioButton('串整条链')
        self.rb_single.setChecked(True)
        self._grp = QButtonGroup(self)
        self._grp.addButton(self.rb_single)
        self._grp.addButton(self.rb_chain)
        self.rb_single.toggled.connect(self._on_mode_changed)
        hmm.addWidget(self.rb_single)
        hmm.addWidget(self.rb_chain)
        hmm.addStretch(1)
        mv.addLayout(hmm)

        self.row_single = QWidget()
        rs = QHBoxLayout(self.row_single)
        rs.setContentsMargins(0, 0, 0, 0)
        rs.setSpacing(6)
        rs.addWidget(QLabel('模块'))
        self.cmb_mod = QComboBox()
        for mid in RUNNABLE:
            self.cmb_mod.addItem(_module_title(mid), mid)
        self.cmb_mod.currentIndexChanged.connect(
            lambda _i: self._refresh_preview())
        rs.addWidget(self.cmb_mod, 1)
        mv.addWidget(self.row_single)

        self.box_chain = QWidget()
        cv = QVBoxLayout(self.box_chain)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(2)
        grid = QGridLayout()
        grid.setSpacing(4)
        self.chk_stages = {}
        for i, mid in enumerate(STAGE_ORDER):
            c = QCheckBox(STAGE_TITLE[mid])
            c.setChecked(mid in (self._cfg.get('stages') or ['m3_post', 'm4_ld']))
            c.toggled.connect(lambda _v: self._refresh_preview())
            self.chk_stages[mid] = c
            grid.addWidget(c, i // 2, i % 2)
        cv.addLayout(grid)
        for mid in STAGE_ORDER:
            row = StageRow(mid)
            self._rows[mid] = row
            cv.addWidget(row)
        mv.addWidget(self.box_chain)

        self.chk_force = QCheckBox('强制重跑（不跳过已完成）')
        self.chk_force.setChecked(bool(self._cfg.get('force')))
        mv.addWidget(self.chk_force)

        hb = QHBoxLayout()
        hb.setSpacing(6)
        self.btn_check = QPushButton('只预检')
        self.btn_check.clicked.connect(self._on_check)
        self.btn_run = QPushButton('开始执行')
        self.btn_run.setObjectName('primary')
        self.btn_run.clicked.connect(self._on_run)
        self.btn_stop = QPushButton('停止')
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._on_stop)
        hb.addWidget(self.btn_check)
        hb.addWidget(self.btn_run)
        hb.addWidget(self.btn_stop)
        mv.addLayout(hb)
        lv.addWidget(g_mode)

        self.lbl_next = QLabel('')
        self.lbl_next.setWordWrap(True)
        self.lbl_next.setObjectName('muted')
        lv.addWidget(self.lbl_next)
        lv.addStretch(1)

        scroll_l = QScrollArea()
        scroll_l.setWidgetResizable(True)
        scroll_l.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_l.setWidget(left)
        scroll_l.setMinimumWidth(400)

        foot = QWidget()
        fv = QVBoxLayout(foot)
        fv.setContentsMargins(0, 4, 4, 0)
        fv.setSpacing(5)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.lbl_step = QLabel('就绪')
        self.lbl_step.setObjectName('stepLabel')
        self.lbl_step.setWordWrap(True)
        fv.addWidget(self.bar)
        fv.addWidget(self.lbl_step)

        hb2 = QHBoxLayout()
        hb2.setSpacing(6)
        b_rep = QPushButton('导出报告')
        b_rep.clicked.connect(self._on_export)
        b_cfg = QPushButton('大模型设置…')
        b_cfg.clicked.connect(self._on_llm_cfg)
        hb2.addWidget(b_rep)
        hb2.addWidget(b_cfg)
        hb2.addStretch(1)
        fv.addLayout(hb2)

        lcol = QWidget()
        lcv = QVBoxLayout(lcol)
        lcv.setContentsMargins(0, 0, 0, 0)
        lcv.setSpacing(0)
        lcv.addWidget(scroll_l, 1)
        lcv.addWidget(foot, 0)
        split.addWidget(lcol)

        # ---------------- 右栏 ----------------
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(4, 0, 0, 0)
        rv.setSpacing(6)

        # 顶部：会话标题 + 操作
        top = QHBoxLayout()
        top.setSpacing(6)
        self.lbl_chat_title = QLabel('对话')
        self.lbl_chat_title.setStyleSheet(
            'font-size:14px;font-weight:600;color:#111827;')
        top.addWidget(self.lbl_chat_title)
        self.lbl_chat_sub = QLabel('')
        self.lbl_chat_sub.setObjectName('muted')
        top.addWidget(self.lbl_chat_sub, 1)
        self.b_log = QPushButton('明细')
        self.b_log.setCheckable(True)
        self.b_log.setFixedWidth(56)
        self.b_log.setToolTip('展开/收起运行日志与产物校验明细')
        self.b_log.toggled.connect(self._on_toggle_log)
        top.addWidget(self.b_log)
        b_new = QPushButton('新对话')
        b_new.setFixedWidth(70)
        b_new.setToolTip('清空对话，开始新的一轮（不动项目文件）')
        b_new.clicked.connect(self._on_new_chat)
        top.addWidget(b_new)
        rv.addLayout(top)

        # 对话区（气泡）
        self.chat_view = ChatView()
        rv.addWidget(self.chat_view, 1)

        # 明细日志（默认折叠）
        self.detail = QPlainTextEdit()
        self.detail.setObjectName('result')
        self.detail.setReadOnly(True)
        self.detail.setMaximumBlockCount(5000)
        self.detail.setPlaceholderText('运行日志与产物校验明细')
        self.detail.setVisible(False)
        self.detail.setMaximumHeight(180)
        rv.addWidget(self.detail, 0)

        # 输入框（多行，Ctrl+Enter 或 Enter 发送）
        box = QFrame()
        box.setObjectName('composer')
        box.setStyleSheet(
            'QFrame#composer{background:#ffffff;border:1px solid #d6dde8;'
            'border-radius:10px;}')
        cv2 = QVBoxLayout(box)
        cv2.setContentsMargins(8, 6, 8, 6)
        cv2.setSpacing(4)
        self.edit_q = QPlainTextEdit()
        self.edit_q.setObjectName('composerEdit')
        self.edit_q.setPlaceholderText(
            '⑥ 用组间模式分析 ／ 跑到 LD ／ 还缺什么 ／ 刚才那例重跑　（Enter 发送，Shift+Enter 换行）')
        self.edit_q.setFixedHeight(46)
        self.edit_q.setStyleSheet(
            'QPlainTextEdit#composerEdit{background:transparent;border:none;'
            'font-family:"Microsoft YaHei UI","Segoe UI",sans-serif;'
            'font-size:13px;padding:2px;}')
        self.edit_q.installEventFilter(self)
        cv2.addWidget(self.edit_q)
        hq = QHBoxLayout()
        hq.setSpacing(6)
        self.lbl_hint = QLabel('')
        self.lbl_hint.setObjectName('muted')
        hq.addWidget(self.lbl_hint, 1)
        self.b_ask = QPushButton('发送')
        self.b_ask.setObjectName('primary')
        self.b_ask.setFixedWidth(78)
        self.b_ask.clicked.connect(self._on_ask)
        hq.addWidget(self.b_ask)
        cv2.addLayout(hq)
        rv.addWidget(box)

        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setChildrenCollapsible(False)
        split.setCollapsible(0, False)
        split.setCollapsible(1, False)
        root.addWidget(split, 1)

        self._on_mode_changed()
        self._greet(config.llm_cfg(self._cfg))

    # ------------------------------------------------------------ 开场白
    def _greet(self, cfg_llm: dict):
        if cfg_llm:
            self.lbl_chat_title.setText('CEST 智能体')
            self.lbl_chat_sub.setText(cfg_llm.get('model', ''))
            self._say(
                f'我是 CEST 智能体（大模型：{cfg_llm.get("model", "")}）。'
                '**直接跟我说人话就行**：\n\n'
                '· 「⑥ 用组间模式重新分析一遍」——我会去看参数表，把 mode 设成组间再跑\n'
                '· 「跑到 LD」「只跑后处理」「刚才那例重跑」\n'
                '· 「还缺什么」「ctrl 现在到哪一步了」\n\n'
                '两条红线我不越：**掩模/ROI 必须你手画**（缺了把你送到 ⑧ 页）；'
                '**频率表**必须你核对过才写进去。')
        else:
            self.lbl_chat_title.setText('CEST 智能体（本地规则）')
            self.lbl_chat_sub.setText('未启用大模型')
            self._say(
                '我是 CEST 智能体。**可以只跑一个模块**，也可以串整条链——'
                '左边选「只跑一个模块」，就只动那一页，不跑上游。\n\n'
                '两点先说清楚：\n'
                '· **掩模和器官 ROI 必须你手画**——缺了我停下，把你送到 ⑧ 页，不代生成。\n'
                '· 手上只有扫描原始数据（S0 + Z 谱）就先跑 ⓪ 导入重建，由它组装成 ① 的输入。\n'
                '· **频率表**我先自动检索，检索到了也要你核对过才写进去。\n\n'
                '（当前没启用大模型，只能按关键词识别指令。填了 key 我就能听懂人话了。）')

    # -------------------------------------------------------------- 小工具
    def _say(self, text: str, kind: str = 'info'):
        """智能体说话。kind: info/ok/warn/err；info 走正常气泡，其余带色。"""
        role = {'ok': 'ok', 'warn': 'warn', 'err': 'err'}.get(kind, 'agent')
        self.chat_view.add(role, str(text))
        self.lbl_chat_sub.setText(time.strftime('最后更新 %H:%M'))

    def _say_user(self, text: str):
        self.chat_view.add('user', str(text))

    def _on_toggle_log(self, on: bool):
        self.detail.setVisible(bool(on))

    def _on_new_chat(self):
        """清空对话与模型上下文，项目文件一律不动。"""
        self.chat_view.clear_all()
        self.detail.clear()
        self._chat = None
        self._last_result = None
        self.lbl_chat_sub.setText('')
        self.lbl_hint.setText('')
        cfg_llm = config.llm_cfg(self._cfg)
        self._greet(cfg_llm)

    def eventFilter(self, obj, e):
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent
        if obj is self.edit_q and e.type() == QEvent.Type.KeyPress:
            k = e
            if isinstance(k, QKeyEvent) and k.key() in (
                    Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if k.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    return False                     # Shift+Enter 换行
                self._on_ask()
                return True
        return super().eventFilter(obj, e)

    def _log(self, s: str):
        self.detail.appendPlainText(str(s))

    # ---------------------------------------------------------- 目录 / 模式
    def _main_proj(self) -> str:
        try:
            return str(self.main.proj() or '').strip()
        except Exception:                                # noqa: BLE001
            return ''

    def _pick_in(self):
        d = QFileDialog.getExistingDirectory(self, '选择输入目录（DICOM 或病例目录）',
                                             self.ed_in.text().strip() or self._main_proj())
        if d:
            self.ed_in.setText(d)
            self._on_io_changed()

    def _pick_out(self):
        d = QFileDialog.getExistingDirectory(self, '选择输出总目录（PROJ）',
                                             self.ed_proj.text().strip() or self._main_proj())
        if d:
            self.ed_proj.setText(d)
            self._on_io_changed()

    def _on_io_changed(self):
        self._reload_io()

    def _reload_io(self, keep_case=True):
        src = self.ed_in.text().strip()
        proj_hint = self.ed_proj.text().strip() or self._main_proj()
        cur = self.cmb_case.currentText().strip() if keep_case else ''
        if not src and not proj_hint:
            self.lbl_detect.setText('先选输入目录，或在上方设输出总目录(PROJ)。')
            return
        try:
            io = layout.resolve_io(src or proj_hint, proj_hint)
        except Exception as e:                           # noqa: BLE001
            self.lbl_detect.setText(f'识别出错：{type(e).__name__}: {e}')
            return
        self._io = io
        # 输入只有 PROJ 时，把主窗口的 PROJ 填进来显示
        if io.get('proj') and not self.ed_proj.text().strip():
            self.ed_proj.setPlaceholderText(io['proj'])
        self.lbl_detect.setText(io.get('note', '') or '')
        if io.get('problems'):
            self.lbl_detect.setText(
                (io.get('note', '') + '　') +
                '；'.join(p.get('text', '') for p in io['problems']))
        self._reload_cases(io.get('proj') or proj_hint, io.get('cases') or [], cur)

    def _reload_cases(self, proj=None, cases=None, cur=''):
        proj = proj if proj is not None else self._proj()
        if cases is None:
            try:
                cases = plan.cases_of(proj) if proj else []
            except Exception:                            # noqa: BLE001
                cases = []
        if not cur:
            cur = self.cmb_case.currentText().strip()
        self.cmb_case.blockSignals(True)
        self.cmb_case.clear()
        self.cmb_case.addItem('（全部病例）')
        self.cmb_case.addItems(list(cases))
        cand = [cur, self._io.get('case') or '']
        for c in cand:
            if c and c in cases:
                self.cmb_case.setCurrentText(c)
                break
        self.cmb_case.blockSignals(False)
        self._refresh_preview()

    def _on_mode_changed(self):
        single = self.rb_single.isChecked()
        self.row_single.setVisible(single)
        self.box_chain.setVisible(not single)
        self._refresh_preview()

    def _now_mid(self) -> str:
        if self.rb_single.isChecked():
            return str(self.cmb_mod.currentData() or 'm3_post')
        return ''

    def _sel_case(self) -> str:
        t = self.cmb_case.currentText().strip()
        return '' if t.startswith('（') else t

    def _sel_stages(self) -> list:
        return [m for m in STAGE_ORDER if self.chk_stages[m].isChecked()]

    # --------------------------------------------------------------- 外部调用
    def seed(self, mid=None, case=None, src='', proj=''):
        """由主窗口带进来的上下文：当前模块 / 当前病例 / 当前输入目录。"""
        if proj:
            self.ed_proj.setText(proj)
        if src:
            self.ed_in.setText(src)
        if mid and mid in RUNNABLE:
            i = self.cmb_mod.findData(mid)
            if i >= 0:
                self.cmb_mod.setCurrentIndex(i)
            self.rb_single.setChecked(True)
        self._reload_io(keep_case=False)
        if case:
            i = self.cmb_case.findText(case)
            if i >= 0:
                self.cmb_case.setCurrentIndex(i)
        self._refresh_preview()

    def attach(self, det=None, proj=None):
        self._reload_io(keep_case=True)

    def refresh_chain(self, det=None, proj=None):
        self._reload_io(keep_case=True)

    def refresh_preview(self, proj=None):
        self._refresh_preview()

    def refresh_extra(self):
        return

    def reset(self):
        self.ed_in.clear()
        self.ed_proj.clear()
        self.cmb_case.blockSignals(True)
        self.cmb_case.clear()
        self.cmb_case.addItem('（全部病例）')
        self.cmb_case.blockSignals(False)
        self.chat_view.clear_all()
        self.detail.clear()
        self.bar.setValue(0)
        self.lbl_step.setText('就绪')
        self._last_result = None
        for r in self._rows.values():
            r.set_status('idle')

    def _proj(self) -> str:
        return (self.ed_proj.text().strip()
                or (self._io.get('proj') or '')
                or self._main_proj())

    # ------------------------------------------------------------- 预检预览
    def _refresh_preview(self):
        if self._worker is not None and self._worker.isRunning():
            return
        proj = self._proj()
        case = self._sel_case()
        t = (self._io or {}).get('stages') or {}

        if self.rb_single.isChecked():
            mid = self._now_mid()
            d = t.get(mid) or {}
            bits = []
            if d.get('inp'):
                bits.append(f'读 {d["inp"]}')
            if d.get('out'):
                bits.append(f'写 {d["out"]}')
            if mid == 'm1_recon' and not (self.ed_in.text().strip()):
                bits.append('还没选 DICOM 输入目录')
            self.lbl_next.setText('　·　'.join(bits) or '选好输入目录后这里显示读写位置。')
            for r in self._rows.values():
                r.set_status('idle')
            return

        if not proj:
            self.lbl_next.setText('先设「输出总目录(PROJ)」。')
            for r in self._rows.values():
                r.set_status('idle')
            return
        try:
            if case:
                res = plan.check_case(proj, case, self._sel_stages(),
                                      force=self.chk_force.isChecked(), io=self._io)
                self._paint([res])
                self.lbl_next.setText(res.get('next_action', {}).get('text', ''))
            else:
                res = plan.check(proj, '', self._sel_stages(),
                                 force=self.chk_force.isChecked(), io=self._io)
                if res.get('error'):
                    self.lbl_next.setText(res['error'])
                    return
                self._paint(res.get('cases') or [])
                self.lbl_next.setText(
                    f'共 {len(res.get("cases") or [])} 个病例：'
                    f'{len(res.get("ready_cases") or [])} 个能直接跑，'
                    f'{len(res.get("stuck_cases") or [])} 个需先处理。')
        except Exception as e:                           # noqa: BLE001
            self.lbl_next.setText(f'预检出错：{type(e).__name__}: {e}')

    def _paint(self, case_results):
        if not case_results:
            for r in self._rows.values():
                r.set_status('idle')
            return
        if len(case_results) == 1:
            for s in case_results[0].get('stages') or []:
                row = self._rows.get(s['id'])
                if row is None:
                    continue
                detail = ''
                probs = s.get('problems') or []
                if probs:
                    detail = str(probs[0].get('text', ''))[:70]
                row.set_status(s.get('status', 'idle'), detail)
            return
        for mid in STAGE_ORDER:
            stats = {}
            for cr in case_results:
                for s in cr.get('stages') or []:
                    if s['id'] == mid:
                        k = s.get('status', 'idle')
                        stats[k] = stats.get(k, 0) + 1
            if not stats:
                self._rows[mid].set_status('idle')
                continue
            n = sum(stats.values())
            done = stats.get('done', 0)
            todo = stats.get('todo', 0)
            blk = stats.get('blocked', 0)
            if done == n:
                self._rows[mid].set_status('done', f'{done}/{n} 已完成')
            elif blk and not todo:
                self._rows[mid].set_status('blocked', f'{blk}/{n} 被卡住')
            else:
                self._rows[mid].set_status(
                    'todo', f'{done}/{n} 已完成，{todo} 个待跑')

    # --------------------------------------------------------------- 预检
    def _on_check(self):
        proj = self._proj()
        case = self._sel_case()
        if self.rb_single.isChecked():
            mid = self._now_mid()
            d = (self._io or {}).get('stages', {}).get(mid) or {}
            lines = [f'模块：{_module_title(mid)}']
            for k, lab in (('inp', '读'), ('out', '写'), ('mask', '掩模')):
                if d.get(k):
                    p = Path(str(d[k]))
                    lines.append(f'{lab}：{d[k]}　{"存在" if p.exists() else "不存在"}')
            if mid == 'm3_post':
                gate = plan.check_case(proj, case or self._io.get('case') or '',
                                       ('m_roi',)) if proj else {}
                probs = gate.get('needs_human') or []
                if probs:
                    lines.append('')
                    lines.append('前置有问题：')
                    lines += [f'　· {p.get("text", "")}' for p in probs]
            self._say('\n'.join(lines))
            return

        if not proj:
            QMessageBox.information(self, '还没选目录',
                                    '请先选输出总目录（PROJ）。')
            return
        try:
            if case:
                res = plan.check_case(proj, case, self._sel_stages(),
                                      force=self.chk_force.isChecked())
                self._paint([res])
                self._say(chat.describe_check(res))
            else:
                res = plan.check(proj, '', self._sel_stages(),
                                 force=self.chk_force.isChecked())
                self._paint(res.get('cases') or [])
                self._say(f'共 {len(res.get("cases") or [])} 个病例：'
                          f'{len(res.get("ready_cases") or [])} 个能直接跑，'
                          f'{len(res.get("stuck_cases") or [])} 个需先处理。')
        except Exception as e:                           # noqa: BLE001
            self._say(f'预检出错：{type(e).__name__}: {e}', 'err')

    # --------------------------------------------------------------- 执行
    def _on_run(self):
        proj = self._proj()
        case = self._sel_case()
        cfg_stages = self._sel_stages()
        self._cfg['stages'] = cfg_stages
        self._cfg['force'] = self.chk_force.isChecked()
        config.save(self._cfg)

        if self.rb_single.isChecked():
            mid = self._now_mid()
            if mid == 'm_import' and not self.ed_in.text().strip():
                QMessageBox.information(
                    self, '没选输入目录',
                    '⓪ 导入重建要读原始扫描目录（含 S0 与 Z 谱），请先选输入目录。')
                return
            if not case:
                case = self._io.get('case') or ''
            if not case and mid != 'm7_compare':     # ⑩ 用 A/B 两个病例，不吃单个病例
                QMessageBox.information(
                    self, '没选病例', '只跑单个模块时，请指定一个病例。')
                return
            if not proj:
                QMessageBox.information(
                    self, '没选输出总目录',
                    '还需要「输出总目录(PROJ)」——产物按它的固定子目录存放。')
                return
            self.bar.setRange(0, 0)
            self.lbl_step.setText(f'{_module_title(mid)} · {case} 启动…')
            self._set_running(True)
            self._say(f'只跑 {_module_title(mid)}｜病例 {case}')
            w = AgentWorker('single', proj, [case], [mid], {}, 
                            self.chk_force.isChecked(), self._io,
                            mid=mid, parent=self)
        else:
            if not proj:
                QMessageBox.information(self, '还没选目录',
                                        '请先选输出总目录（PROJ）。')
                return
            if not cfg_stages:
                QMessageBox.information(self, '没勾阶段', '至少勾一个阶段。')
                return
            cases = [case] if case else (
                plan.cases_of(proj) or list((self._io or {}).get('cases') or []))
            if not cases:
                QMessageBox.information(
                    self, '没有病例',
                    '还没认出任何病例。请在上方「输入」里选数据/掩模/DICOM 总目录。')
                return
            self.bar.setRange(0, 100)
            self.bar.setValue(0)
            self.lbl_step.setText('启动…')
            self._set_running(True)
            self._say(f'开始执行：{len(cases)} 个病例，阶段 '
                      f'{" → ".join(STAGE_TITLE[s] for s in cfg_stages)}')
            w = AgentWorker('chain', proj, cases, cfg_stages, {},
                            self.chk_force.isChecked(), self._io, parent=self)

        self._worker = w
        w.sig_event.connect(self._on_event)
        w.sig_freq.connect(self._on_freq_ask)
        w.sig_done.connect(self._on_done)
        w.start()

    def _on_stop(self):
        if self._worker is not None:
            self._worker.cancel()
            self.lbl_step.setText('正在停止…（当前这一步跑完就停）')

    def _set_running(self, on: bool):
        self.btn_run.setEnabled(not on)
        self.btn_check.setEnabled(not on)
        self.btn_stop.setEnabled(on)

    # ---- 频率表核对（信号必须在主线程弹窗）----
    def _on_freq_ask(self, case, info):
        answer = None
        try:
            dlg = FreqConfirmDialog(self, case, info)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                answer = dict(values=dlg.values(),
                              source=f'agent 检索：{info.get("source", "")}')
                if not answer['values']:
                    answer = None
            if answer is None:
                self._say(f'你取消了 {case} 的频率表核对，这一例先跳过。', 'warn')
        except Exception as e:                           # noqa: BLE001
            self._say(f'频率表核对对话框出错：{type(e).__name__}: {e}', 'err')
        finally:
            w = None
            if self._chat_worker is not None and self._chat_worker.isRunning():
                w = self._chat_worker
            elif self._worker is not None:
                w = self._worker
            if w is not None:
                w.set_freq_answer(case, answer)

    # --------------------------------------------------------------- 事件
    def _on_event(self, e: dict):
        t = e.get('type')
        if t == 'log':
            self._log(e.get('text', ''))
        elif t == 'progress':
            tot = e.get('total') or 0
            if tot:
                self.bar.setRange(0, tot)
                self.bar.setValue(int(e.get('cur') or 0))
            if e.get('text'):
                self.lbl_step.setText(str(e['text'])[:130])
        elif t == 'stage':
            mid = e.get('stage')
            if mid in self._rows:
                self._rows[mid].set_status('running',
                                           f'{e.get("case", "")} 运行中…')
            self.lbl_step.setText(f'{STAGE_NAME.get(mid, mid)} · {e.get("case", "")}')
        elif t == 'verify':
            v = e.get('result') or {}
            mid = e.get('stage')
            if mid in self._rows:
                if e.get('skipped'):
                    self._rows[mid].set_status('done', '已完成，跳过')
                elif v.get('ok'):
                    self._rows[mid].set_status('done', '产物校验通过')
                else:
                    self._rows[mid].set_status(
                        'partial', str(v.get('reason', '产物不全'))[:70])
            self._log(f'[校验] {e.get("case", "")} {mid}: '
                      f'{"OK" if v.get("ok") else v.get("reason", "")}')
        elif t == 'need_human':
            mid = e.get('stage')
            self._say(f'需要你操作 · {STAGE_NAME.get(mid, "")}\n'
                      f'{e.get("case", "")}：{e.get("text", "")}', 'warn')
            if mid in self._rows:
                self._rows[mid].set_status('blocked', str(e.get('text', ''))[:70])
        elif t == 'fail':
            d = e.get('diag') or {}
            head = '产物不全' if e.get('partial') else '失败'
            body = (f'{head} · {STAGE_NAME.get(e.get("stage"), "")} · '
                    f'{e.get("case", "")}\n{e.get("text", "")}')
            if d.get('cause'):
                body += f'\n判断：{d["cause"]}\n怎么办：{d.get("action", "")}'
            self._say(body, 'err')
            self._log(f'[失败] {e.get("case", "")} {e.get("stage")}: '
                      f'{e.get("text", "")}')
            if e.get('traceback'):
                self._log(e['traceback'])
        elif t == 'case_done':
            st = e.get('status', '')
            # 逐例结果只进明细，不刷屏（36 例会瞬间把对话冲掉）
            self._log(f'[完成] {e.get("case", "")}：{CASE_CN.get(st, st)}')
            self.lbl_step.setText(f'{e.get("case", "")} {CASE_CN.get(st, st)}')

    def _on_done(self, res):
        self._set_running(False)
        self._last_result = res
        self.bar.setRange(0, 100)
        self.bar.setValue(100 if res.get('ok') else 0)
        self.lbl_step.setText(res.get('summary', ''))
        self._say(chat.describe_run(res), 'ok' if res.get('ok') else 'warn')
        try:
            p = report.save(res)
            self._log(f'报告：{p["markdown"]}')
        except Exception as e:                           # noqa: BLE001
            self._log(f'写报告失败：{e}')
        self._refresh_preview()
        try:
            self.main.log(f'[智能体] {res.get("summary", "")}')
        except Exception:                                # noqa: BLE001
            pass
        # 链跑完后，接着跑指令里剩下的 ⑤⑥⑦（或纯 ⑤⑥ 队列的下一项）
        q = list(getattr(self, '_loose_queue', None) or [])
        if q:
            QTimer.singleShot(200, self._next_loose)
        elif getattr(self, '_pending_loose', None):
            pend = list(self._pending_loose)
            self._pending_loose = []
            self._run_loose(pend)

    # --------------------------------------------------------------- 追问
    def _on_ask(self):
        q = self.edit_q.toPlainText().strip()
        if not q:
            return
        cfg_llm = config.llm_cfg(self._cfg)
        if cfg_llm:
            self.edit_q.clear()
            self._say_user(q)
            # ★ 「把输入/输出改成 X」这种指令不需要模型思考，直接做。
            #   走模型要绕 3~5 次工具调用（读日志、跑 python、找数据），几十秒才完。
            if self._try_fast_path(q):
                return
            self._ask_llm(q, cfg_llm)
            return
        # 没配大模型 → 老路（本地规则）
        self.edit_q.clear()
        self._say_user(q)
        self._on_ask_local(q)

    # ---- 快捷指令：改目录 / 查状态，本地直接办，不走模型 ----
    _RE_SETDIR = None

    def _try_fast_path(self, q: str) -> bool:
        """命中「改输入/输出目录」这类机械指令就直接执行，返回 True 表示已处理。"""
        import re as _re
        import os as _os
        s = str(q or '').strip()
        if not s:
            return False
        if not _re.search(r'输入|输出|目录|项目|路径|改成|换成|切到|切换到', s):
            return False
        # ★ 路径里可能有中文（…\输出结果），不能把中文当分隔符
        cands = []
        for m in _re.finditer(r'[A-Za-z]:[\\/][^\s，。；;、？?！!“”"\'）)]*', s):
            p = m.group(0).replace('/', '\\').rstrip('\\')
            if p and _os.path.isdir(p) and p not in cands:
                cands.append(p)
        if not cands:
            return False

        # 挑一个 PROJ；没有 PROJ 就取最长那个
        from . import layout as _ly
        proj_pick = ''
        for c in sorted(cands, key=len, reverse=True):
            try:
                if _ly.sniff(c).get('kind') == 'proj':
                    proj_pick = c
                    break
            except Exception:                            # noqa: BLE001
                continue
        target = proj_pick or max(cands, key=len)

        want_in = bool(_re.search(r'输入', s))
        want_out = bool(_re.search(r'输出|项目|PROJ', s, _re.I))
        both = bool(_re.search(r'都|全部|一起', s))
        if not (want_in or want_out):
            want_in = want_out = True
        if both and proj_pick:
            # 「输入输出都是 <PROJ>」——PROJ 不能当原始输入，只设输出
            want_in, want_out = False, True
        elif both:
            return False                 # 没有 PROJ 可认，交给模型判断更稳
        if proj_pick:
            want_out, want_in = True, False
        elif want_out and not want_in:
            return False                 # 指定的不是 PROJ，不适合当输出目录

        if want_out:
            self.ed_proj.setText(target)
            try:
                mw = self.main
                if hasattr(mw, 'out_edit'):
                    mw.out_edit.setText(target)
            except Exception:                            # noqa: BLE001
                pass
        if want_in:
            self.ed_in.setText(target)
        try:
            self._reload_io(keep_case=False)
        except Exception:                                # noqa: BLE001
            pass

        # 同步会话上下文，后续对话直接用新目录
        if self._chat is not None:
            self._chat.bind(proj=self._proj(), io=self._io)

        cases = []
        try:
            cases = plan.cases_of(self._proj()) if self._proj() else []
        except Exception:                                # noqa: BLE001
            pass
        parts = []
        if want_out:
            parts.append(f'输出（PROJ）= `{target}`')
        if want_in:
            parts.append(f'输入 = `{target}`')
        msg = '已改好：\n\n· ' + '\n· '.join(parts)
        if proj_pick and both:
            msg += '\n\n（这个路径本身是项目输出目录，不能同时作为原始输入——输入按 PROJ 里的病例自动取）'
        if cases:
            msg += f'\n\n识别到 {len(cases)} 例：' + '、'.join(cases[:8])
        self._say(msg)
        return True

    # ---- 大模型主导：模型自己决定调什么工具、传什么参数 ----
    def _ask_llm(self, q: str, cfg_llm: dict):
        if self._chat is None:
            self._chat = agent.Session(cfg_llm, cfg_all=self._cfg)
        self._chat.cfg = cfg_llm
        self._chat.cfg_all = self._cfg
        # ★ 模型上一轮自己 set_project 过的话，以会话里的 PROJ 为准，
        #   不要拿界面上的旧目录把它覆盖回去（那会让模型以为没设成功、反复重设）
        keep = (self._chat.proj or '').strip()
        proj_now = keep or self._proj()
        self._chat.bind(proj=proj_now, io=self._io)

        self.edit_q.setEnabled(False)
        self.b_ask.setEnabled(False)
        self.b_ask.setText('…')
        self.lbl_hint.setText('模型正在思考')
        self.lbl_step.setText('模型正在思考…')
        self._typing = self.chat_view.add(
            'agent', '<span style="color:#8b95a3;">正在思考…</span>', html=True)

        w = AgentChatWorker(self._chat, q, parent=self)
        w.sig_status.connect(self._on_chat_status)
        w.sig_say.connect(self._on_chat_answer)
        w.sig_event.connect(self._on_event)
        w.sig_freq.connect(self._on_freq_ask)
        w.sig_code.connect(self._on_code_ask)
        w.sig_proj.connect(self._on_agent_set_project)
        w.sig_fail.connect(lambda e: self._on_chat_fallback(q, e))
        self._chat_worker = w
        w.start()

    def _on_agent_set_project(self, proj: str, src: dict):
        """模型改了工作区 → 同步所有界面（信号已在主线程，直接改）。"""
        self._sync_boxes(proj, dict(src or {}))

    def _sync_boxes(self, proj: str, src: dict):
        if proj:
            self.ed_proj.setText(proj)
            # ★ 关键：主窗口顶部那个「输出总目录(PROJ)」也要跟着改。
            #   只改智能体面板的框，其它页面还是旧项目，切了等于没切。
            try:
                mw = self.main
                if hasattr(mw, 'out_edit'):
                    mw.out_edit.setText(proj)
                elif hasattr(mw, 'proj'):
                    pass
            except Exception:                            # noqa: BLE001
                pass
        if src.get('dicom_src'):
            self.ed_in.setText(src['dicom_src'])
        elif src.get('data_src'):
            self.ed_in.setText(src['data_src'])
        elif src.get('mask_src'):
            self.ed_in.setText(src['mask_src'])
        try:
            self._reload_io(keep_case=bool(src.get('case')))
            if src.get('case'):
                i = self.cmb_case.findText(src['case'])
                if i >= 0:
                    self.cmb_case.setCurrentIndex(i)
        except Exception:                                # noqa: BLE001
            pass
        self._refresh_preview()

    def _drop_typing(self):
        t = getattr(self, '_typing', None)
        if t is not None:
            try:
                t.parentWidget().setParent(None)
                t.setParent(None)
                t.deleteLater()
            except Exception:                            # noqa: BLE001
                pass
            self._typing = None

    # ---- 跑代码前确认（信号必须在主线程弹窗）----
    def _on_code_ask(self, code: str):
        ok = False
        try:
            ok = CodeConfirmDialog.ask(self, code)
        except Exception as e:                           # noqa: BLE001
            self._say(f'代码确认对话框出错：{e}', 'err')
        finally:
            w = self._chat_worker
            if w is not None:
                w.set_code_answer(ok)

    def _on_chat_status(self, s: str):
        if s == 'thinking':
            self.lbl_hint.setText('模型正在思考')
            self.lbl_step.setText('模型正在思考…')
        elif s == 'tool':
            self.lbl_hint.setText('模型正在调工具')
            self.lbl_step.setText('模型正在调工具…')
        elif s:
            self._log(f'[模型] {s}')
            self.lbl_hint.setText(str(s)[:60])
            self.lbl_step.setText(str(s)[:130])

    def _on_chat_answer(self, text: str):
        self._drop_typing()
        self.edit_q.setEnabled(True)
        self.b_ask.setEnabled(True)
        self.b_ask.setText('发送')
        self.lbl_hint.setText('')
        self.edit_q.setFocus()
        self.lbl_step.setText('')
        self._say(text, 'info')
        self._refresh_preview()
        try:
            self.main.log(f'[智能体] {str(text)[:200]}')
        except Exception:                                # noqa: BLE001
            pass

    def _on_chat_fallback(self, q: str, err: str):
        """接口挂了 / 没 key / 网络断 → 退回本地规则，别让界面变成死的。"""
        self._drop_typing()
        self.edit_q.setEnabled(True)
        self.b_ask.setEnabled(True)
        self.b_ask.setText('发送')
        self.lbl_hint.setText('')
        self.lbl_step.setText('')
        self._say(f'大模型调用失败（{err}），已退回本地规则。', 'warn')
        self._on_ask_local(q)

    # ---- 本地规则（降级路径，原逻辑保持不动）----
    def _on_ask_local(self, q: str):
        req = intent.parse(q, {})          # ★ 只走正则，不再让 LLM 兜底
        if req.get('action') == 'run' and req.get('stages'):
            st = req['stages']
            if req.get('cases'):
                for c in req['cases']:
                    i = self.cmb_case.findText(c)
                    if i >= 0:
                        self.cmb_case.setCurrentIndex(i)
                        break
            if req.get('all_cases'):
                # 指令说「全部/所有/都跑」→ 病例框切回（全部病例）
                self.cmb_case.setCurrentIndex(0)
            self.chk_force.setChecked(bool(req.get('force')))

            # ⑤⑥⑦ 不进链条：它是跨病例/自检模块，链里没有这两个阶段。
            # 单跑一个，或按顺序逐个跑（不许把 ⑤⑥ 塞进 chk_stages —— 那六格没有它们）
            chainable = [s for s in st if s in STAGE_ORDER]
            loose = [s for s in st if s in RUNNABLE and s not in STAGE_ORDER]

            if loose and not chainable:
                # 全是可以单跑的模块：一个就单跑，多个按顺序逐个跑
                self._run_loose(loose, st)
                return

            if len(st) == 1 and st[0] in RUNNABLE:
                i = self.cmb_mod.findData(st[0])
                if i < 0:
                    i = self.cmb_mod.findData(_ALIAS_MOD.get(st[0], st[0]))
                if i >= 0:
                    self.cmb_mod.setCurrentIndex(i)
                self.rb_single.setChecked(True)
            else:
                self.rb_chain.setChecked(True)
                sel = chainable or list(STAGE_ORDER)
                for mid in STAGE_ORDER:
                    if mid in self.chk_stages:
                        self.chk_stages[mid].setChecked(mid in sel)
            self._say('按你的话，准备跑：'
                      + ' → '.join(STAGE_TITLE.get(s, _module_title(s)) for s in st)
                      + ('　（强制重跑）' if req.get('force') else ''))
            self._refresh_preview()
            self._on_run()
            if loose:
                # 链跑完再接着跑 ⑤⑥（_on_done 里用 _pending_loose 接着跑）
                self._pending_loose = list(loose)
            return
        if req.get('action') == 'check':
            self._on_check()
            return
        self._say(chat.answer(q, self._last_result, config.llm_cfg(self._cfg)))

    # ---- ⑤⑥⑦ 这类不进链的模块：按顺序逐个单跑 ----
    def _run_loose(self, mids, all_stages=None):
        self._loose_queue = list(mids)
        self._loose_all = list(all_stages or mids)
        self._say('按你的话，准备跑：'
                  + ' → '.join(_module_title(s) for s in self._loose_all))
        self._next_loose()

    def _next_loose(self):
        if not self._loose_queue:
            self._loose_queue = []
            self._refresh_preview()
            return
        mid = self._loose_queue.pop(0)
        self.rb_single.setChecked(True)
        i = self.cmb_mod.findData(mid)
        if i >= 0:
            self.cmb_mod.setCurrentIndex(i)
        if self._loose_queue:
            self._say(f'接着跑 {_module_title(mid)}（还剩 {len(self._loose_queue)} 个）')
        self._on_run()

    # --------------------------------------------------------------- 其他
    def _on_export(self):
        if not self._last_result:
            QMessageBox.information(self, '还没有结果', '先跑一次再导出。')
            return
        p = report.save(self._last_result)
        QMessageBox.information(self, '报告已导出',
                                f'JSON：{p["json"]}\n\nMarkdown：{p["markdown"]}')
        try:
            import os
            os.startfile(str(Path(p['markdown']).parent))     # noqa: S606
        except Exception:                                # noqa: BLE001
            pass

    def _on_llm_cfg(self):
        dlg = LLMSettingsDialog(self, self._cfg.get('llm') or {})
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._cfg['llm'] = dlg.result_cfg()
            config.save(self._cfg)
            on = (self._cfg['llm']['enabled']
                  and bool(self._cfg['llm']['api_key']))
            self._say('大模型已' + ('启用。' if on else '关闭，走本地规则。'))


# 单模块模式下，意图里的阶段 → 模块 id
_ALIAS_MOD = {'m_import': 'm_import', 'm1_recon': 'm1_recon', 'm3_post': 'm3_post',
              'm4_ld': 'm4_ld', 'm2_display': 'm2_display',
              'm7_compare': 'm7_compare'}


class _TestThread(QThread):
    """后台测连接，避免 UI 卡在 20 秒超时上。"""

    sig = Signal(object)

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg

    def run(self):
        try:
            self.sig.emit(intent.test_conn(self.cfg))
        except Exception as e:                           # noqa: BLE001
            self.sig.emit(dict(ok=False, ms=0, error=f'{type(e).__name__}: {e}',
                               hint='', model='', reply=''))


class LLMSettingsDialog(QDialog):
    """大模型设置：填完能点「测试连接」当场验通不通。"""

    def __init__(self, parent, llm: dict):
        super().__init__(parent)
        self.setWindowTitle('大模型设置（可选）')
        self.setMinimumWidth(560)
        self._t = None
        v = QVBoxLayout(self)
        tip = QLabel('不填就纯本地规则：指令解析、诊断、报告全部离线可用，'
                     '这也是默认形态。\n'
                     '填了只用于「规则认不出的说法」和自由追问；'
                     '数值与结论仍取自真实运行结果，模型不参与计算。')
        tip.setWordWrap(True)
        tip.setObjectName('muted')
        v.addWidget(tip)

        form = QFormLayout()
        self.c_en = QCheckBox('启用大模型')
        self.c_en.setChecked(bool(llm.get('enabled')))
        form.addRow('', self.c_en)

        self.e_base = QLineEdit(str(llm.get('base_url') or 'https://api.openai.com/v1'))
        self.e_base.setPlaceholderText('https://api.deepseek.com/v1')
        form.addRow('接口地址', self.e_base)

        keyrow = QHBoxLayout()
        keyrow.setSpacing(6)
        self.e_key = QLineEdit(str(llm.get('api_key') or ''))
        self.e_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.e_key.setPlaceholderText('sk-…')
        keyrow.addWidget(self.e_key, 1)
        self.b_test = QPushButton('测试连接')
        self.b_test.clicked.connect(self._on_test)
        keyrow.addWidget(self.b_test)
        form.addRow('API Key', keyrow)

        self.e_model = QLineEdit(str(llm.get('model') or ''))
        self.e_model.setPlaceholderText('如 deepseek-chat / gpt-4o-mini')
        form.addRow('模型名', self.e_model)
        v.addLayout(form)

        # ---------------- 权限 ----------------
        from . import grants as _G
        g = _G.cfg_of({'grants': llm.get('grants')})
        gp = QGroupBox('智能体权限（决定它能碰什么）')
        gv = QFormLayout(gp)

        self.cb_level = QComboBox()
        for lv in _G.LEVELS:
            self.cb_level.addItem(_G.LEVEL_LABEL[lv], lv)
        i = self.cb_level.findData(g.get('level') or 'proj')
        self.cb_level.setCurrentIndex(max(0, i))
        gv.addRow('可访问范围', self.cb_level)
        self.lbl_level_tip = QLabel(_G.LEVEL_TIP.get(g.get('level') or 'proj', ''))
        self.lbl_level_tip.setWordWrap(True)
        self.lbl_level_tip.setObjectName('muted')
        gv.addRow('', self.lbl_level_tip)
        self.cb_level.currentIndexChanged.connect(self._on_level_changed)

        self.c_py = QCheckBox('允许执行 Python 代码')
        self.c_py.setChecked(bool(g.get('allow_python')))
        gv.addRow('', self.c_py)

        self.c_pycf = QCheckBox('每次执行代码前先问我')
        self.c_pycf.setChecked(bool(g.get('confirm_python', True)))
        gv.addRow('', self.c_pycf)

        self.c_wo = QCheckBox('允许写项目目录以外的文件')
        self.c_wo.setChecked(bool(g.get('allow_write_out')))
        gv.addRow('', self.c_wo)

        lock = QLabel('🔒 硬限制：智能体只能调用代码，不能修改程序源代码。'
                      '这一条与上面权限无关，永远生效。')
        lock.setWordWrap(True)
        lock.setStyleSheet('color:#c0392b;')
        gv.addRow('', lock)
        v.addWidget(gp)

        self.lbl_test = QLabel('')
        self.lbl_test.setWordWrap(True)
        v.addWidget(self.lbl_test)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._on_ok)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _cfg(self) -> dict:
        return dict(base_url=self.e_base.text().strip(),
                    api_key=self.e_key.text().strip(),
                    model=self.e_model.text().strip(), timeout=20)

    def _on_test(self):
        c = self._cfg()
        if not (c['base_url'] and c['api_key'] and c['model']):
            self.lbl_test.setText('接口地址、API Key、模型名三项都填了再测。')
            self.lbl_test.setStyleSheet('color:#b9770e;')
            return
        self.b_test.setEnabled(False)
        self.lbl_test.setStyleSheet('color:#1f2937;')
        self.lbl_test.setText('正在连接…')
        self._t = _TestThread(c, self)
        self._t.sig.connect(self._on_test_done)
        self._t.start()

    def _on_test_done(self, r: dict):
        self.b_test.setEnabled(True)
        if r.get('ok'):
            self.lbl_test.setStyleSheet('color:#1e8449;')
            self.lbl_test.setText(
                f'连接成功（{r.get("ms", 0)} ms）　模型 {r.get("model", "")} 回复：'
                f'{r.get("reply") or "（空）"}')
        else:
            self.lbl_test.setStyleSheet('color:#c0392b;')
            msg = f'连接失败：{r.get("error", "")}'
            if r.get('hint'):
                msg += f'\n{r["hint"]}'
            self.lbl_test.setText(msg)

    def _on_level_changed(self):
        from . import grants as _G
        lv = self.cb_level.currentData()
        self.lbl_level_tip.setText(_G.LEVEL_TIP.get(lv, ''))

    def _on_ok(self):
        c = self._cfg()
        if self.c_en.isChecked() and not (c['base_url'] and c['api_key'] and c['model']):
            QMessageBox.information(self, '还差东西',
                                    '要启用大模型，接口地址 / API Key / 模型名都得填。')
            return
        self.accept()

    def result_cfg(self) -> dict:
        c = self._cfg()
        c['enabled'] = bool(self.c_en.isChecked())
        c['grants'] = dict(
            level=self.cb_level.currentData() or 'proj',
            allow_python=bool(self.c_py.isChecked()),
            confirm_python=bool(self.c_pycf.isChecked()),
            allow_write_out=bool(self.c_wo.isChecked()))
        return c


class CodeConfirmDialog(QDialog):
    """跑 Python 代码前给人看一遍。代码只读展示，人确认才执行。"""

    def __init__(self, parent, code: str):
        super().__init__(parent)
        self.setWindowTitle('智能体要执行代码')
        self.setMinimumWidth(680)
        v = QVBoxLayout(self)
        tip = QLabel('智能体想在项目解释器里跑下面这段 Python（工作目录 = PROJ）。\n'
                     '看一遍再决定：看不懂或觉得不对就点取消。')
        tip.setWordWrap(True)
        v.addWidget(tip)
        self.view = QPlainTextEdit()
        self.view.setPlainText(code)
        self.view.setReadOnly(True)
        self.view.setMinimumHeight(260)
        v.addWidget(self.view)
        bb = QDialogButtonBox()
        self.b_ok = bb.addButton('允许执行', QDialogButtonBox.ButtonRole.AcceptRole)
        bb.addButton('取消', QDialogButtonBox.ButtonRole.RejectRole)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    @staticmethod
    def ask(parent, code: str) -> bool:
        d = CodeConfirmDialog(parent, code)
        return d.exec() == QDialog.DialogCode.Accepted


class FreqConfirmDialog(QDialog):
    """频率表人工核对。检索到的值先填好，用户改完并勾「已核对」才能确定。"""

    def __init__(self, parent, case, info):
        super().__init__(parent)
        self.setWindowTitle('频率表核对')
        self.setMinimumWidth(540)
        v = QVBoxLayout(self)
        vals = info.get('values') or []
        from detect import freq_doc as FD
        s = FD.summarize(vals, 0, exact=False) if vals else {}
        head = QLabel(f'病例 {case}　检索到 {len(vals)} 个频偏'
                      f'（去重后 {s.get("n_unique", len(vals))} 个）')
        head.setStyleSheet('font-weight:600;')
        v.addWidget(head)
        src = QLabel(f'来源：{info.get("source", "")}　{info.get("note", "")}')
        src.setWordWrap(True)
        src.setObjectName('muted')
        v.addWidget(src)

        self.edit = QPlainTextEdit()
        self.edit.setPlainText(', '.join(f'{x:g}' for x in vals))
        self.edit.setMaximumHeight(120)
        v.addWidget(self.edit)

        lab = QLabel('一行一个或用逗号分隔；也支持 -12:0.1:12 这种区间写法。'
                     '改完必须勾下面的确认。')
        lab.setWordWrap(True)
        lab.setObjectName('muted')
        v.addWidget(lab)

        self.chk = QCheckBox('我已核对过这张频率表，确认写入')
        v.addWidget(self.chk)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        self.bb = bb
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.chk.toggled.connect(lambda on: bb.button(
            QDialogButtonBox.StandardButton.Ok).setEnabled(bool(on)))
        bb.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)

    def values(self) -> list:
        from detect import freq_doc as FD
        try:
            v = FD.parse_spec(self.edit.toPlainText())
            return list(v or [])
        except Exception:                                # noqa: BLE001
            return []


class AgentWindow(QWidget):
    """全局唯一的智能体窗口。"""

    def __init__(self, main):
        super().__init__(None)
        self.setWindowTitle('CEST 智能体')
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.resize(1180, 820)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        self.panel = AgentPanel(main, self)
        v.addWidget(self.panel)

    def seed(self, mid=None, case=None, src='', proj=''):
        self.panel.seed(mid=mid, case=case, src=src, proj=proj)

    def closeEvent(self, e):
        # 关窗口不影响正在跑的线程，但主窗口下次打开会 reuse 同一个对象
        super().closeEvent(e)


def open_agent(main, mid=None, case=None, src='', proj=''):
    """从任何模块页打开（复用同一个窗口）。"""
    w = getattr(main, '_agent_win', None)
    if w is None:
        w = AgentWindow(main)
        main._agent_win = w
    try:
        w.seed(mid=mid, case=case, src=src, proj=proj)
    except Exception:                                    # noqa: BLE001
        pass
    w.show()
    w.raise_()
    w.activateWindow()
    return w
