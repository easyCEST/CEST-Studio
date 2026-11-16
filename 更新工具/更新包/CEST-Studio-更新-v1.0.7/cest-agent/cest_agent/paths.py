# -*- coding: utf-8 -*-
"""cest_agent.paths —— 找到 GUI 与核心层（唯一真源，绝不复制）。

agent 是**独立目录**（和 cest-gui 平级），但必须 import cest-gui 里的模块与
03_Python_全链路\\函数库。这里只做一件事：把它们的路径挂进 sys.path。

查找顺序（第一个命中的就用）：
    1) 环境变量 CEST_GUI_HOME
    2) 本文件所在目录的上一级里的 cest-gui        （默认：…\\频率重要性通用\\cest-gui）
    3) 环境变量 CEST_GUI_HOME 的兄弟目录里的 cest-gui
    4) 用户目录下常见的几个位置（兜底，找不到就明确报错，不猜）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_AGENT_ROOT = Path(__file__).resolve().parent.parent      # …\cest-agent
_GUI_CACHE = None


def _looks_like_gui(d: Path) -> bool:
    """认得 CEST Studio 的标志：有 CEST_Studio\\app\\modules\\registry.py。"""
    try:
        return (d / 'CEST_Studio' / 'app' / 'modules' / 'registry.py').is_file()
    except OSError:
        return False


def candidates() -> list:
    out = []
    env = os.environ.get('CEST_GUI_HOME', '').strip()
    if env:
        out.append(Path(env))
    # ① 塞在 cest-gui 里（交付包就是这么放的：cest-gui\cest-agent）
    out.append(_AGENT_ROOT.parent)
    # ② 与 cest-gui 平级（开发时的布局）
    out.append(_AGENT_ROOT.parent / 'cest-gui')
    out += [
        _AGENT_ROOT / 'cest-gui',
        Path.cwd() / 'cest-gui',
        Path.cwd(),
    ]
    seen, uniq = set(), []
    for p in out:
        k = str(p).lower()
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


def gui_home() -> Path:
    """返回 cest-gui 根目录；找不到直接抛，把找过的位置全列出来。"""
    global _GUI_CACHE
    if _GUI_CACHE is not None:
        return _GUI_CACHE
    for d in candidates():
        if _looks_like_gui(d):
            _GUI_CACHE = d.resolve()
            return _GUI_CACHE
    tried = '\n'.join(f'  · {d}' for d in candidates())
    raise FileNotFoundError(
        '找不到 cest-gui（CEST Studio 主程序）—— agent 是独立目录，需要知道 GUI 在哪。\n'
        '设置环境变量 CEST_GUI_HOME 指向 cest-gui 根目录即可。\n找过这些位置：\n' + tried)


def install() -> dict:
    """把 GUI 壳与核心层挂进 sys.path（幂等）。返回路径信息。"""
    gui = gui_home()
    shell = gui / 'CEST_Studio'
    core = gui / '03_Python_全链路' / '函数库'
    for p in (str(shell), str(shell / 'app'), str(core)):
        if Path(p).is_dir() and p not in sys.path:
            sys.path.insert(0, p)
    return dict(gui=str(gui), shell=str(shell), core=str(core))


def python_exe() -> str:
    """同源解释器：GUI 自带的 runtime\\python.exe；没有就用当前解释器。"""
    try:
        rt = gui_home() / 'runtime' / 'python.exe'
        if rt.is_file():
            return str(rt)
    except FileNotFoundError:
        pass
    return sys.executable


def agent_state_dir() -> Path:
    """agent 自己的状态目录（报告 / 日志 / 会话），不进 GUI 交付包。"""
    d = _AGENT_ROOT / '_state'
    d.mkdir(parents=True, exist_ok=True)
    return d
