# -*- coding: utf-8 -*-
"""cest_agent.grants —— 权限层：决定智能体能碰哪些路径、能不能跑代码。

四档权限（用户在「大模型设置」里选，存 _state/config.json）：
    proj     仅项目目录（默认）—— 只能看 PROJ
    workspace 工作区 + 同级数据 —— 桌面工作区、PROJ 同盘的数据目录
    user      用户目录 —— 你的 C:\\Users\\<用户名>
    disk      全盘可读

另外三个开关：
    allow_python    允许跑 Python 代码
    confirm_python  每次跑代码前弹窗给用户看（默认开）
    allow_write_out 允许写 PROJ 以外的文件（默认关）

★ 一条绝对的硬限制：**智能体永远不能改源代码**。
  它只能「调用」已有代码。写文件一律只允许写数据产物目录，
  对 cest-gui / cest-agent 里的 .py .pyc 一律拒绝——哪怕用户把权限开到全盘。
"""
from __future__ import annotations

import os
from pathlib import Path

LEVELS = ('proj', 'workspace', 'user', 'disk')
LEVEL_LABEL = {
    'proj': '仅项目目录（最保守）',
    'workspace': '工作区 + 同级数据目录',
    'user': '整个用户目录',
    'disk': '全盘可读（最灵活）',
}
LEVEL_TIP = {
    'proj': '只能读 PROJ 内部。找数据、看日志都受限，但最安全。',
    'workspace': '能读 PROJ 所在盘的数据目录，适合「原始数据在别的文件夹」。',
    'user': '能读 C:\\Users\\<你> 下任何位置。日常用这个基本够。',
    'disk': '任意路径都能读。除非你确实需要，否则不要开。',
}

DEFAULTS = {
    'level': 'proj',
    'allow_python': False,
    'confirm_python': True,
    'allow_write_out': False,
}

# 源代码目录（相对 cest-gui 根）—— 任何写操作到这里都要拒
_CODE_DIRS = ('CEST_Studio', '03_Python_全链路', 'cest-agent')
_CODE_EXT = ('.py', '.pyc', '.pyo', '.pyi', '.pyd', '.so', '.dll')


def cfg_of(llm_or_root: dict) -> dict:
    """从 config 里取权限配置（容忍旧配置没有这一段）。

    两种调用形态都要认：
        {'grants': {...}}                  ← 设置对话框里直接给一段
        {'llm': {'grants': {...}}, ...}    ← 整份 config.json（Session.cfg_all）
    原先只认第一种，导致 agent 侧永远读到 DEFAULTS（= 仅项目目录），
    用户在界面上调到「全盘」也不生效。
    """
    src = llm_or_root or {}
    g = src.get('grants')
    if not isinstance(g, dict):
        llm = src.get('llm')
        if isinstance(llm, dict):
            g = llm.get('grants')
    d = dict(DEFAULTS)
    if isinstance(g, dict):
        for k, v in g.items():
            if k == 'level' and v not in LEVELS:
                continue
            d[k] = v
    return d


def _roots(level: str, proj: str) -> list:
    """这一档权限允许的根目录列表。"""
    out = []
    p = str(proj or '').strip()
    if p:
        out.append(Path(p))
    if level == 'proj':
        return out
    # workspace：PROJ 所在盘 + 桌面
    for src in (p, str(Path.home() / 'Desktop')):
        if not src:
            continue
        try:
            q = Path(src)
            if q.exists():
                # PROJ 往上三级以内当工作区根（演示运行产物 → 演示视频 → 10 → 2026）
                for anc in list(q.parents)[:4]:
                    out.append(anc)
                out.append(q.anchor and Path(q.anchor) or q)
        except Exception:                                # noqa: BLE001
            pass
    if level in ('workspace', 'user', 'disk'):
        out.append(Path.home() / 'Desktop')
    if level in ('user', 'disk'):
        out.append(Path.home())
    if level == 'disk':
        for letter in 'CDEFGH':
            d = Path(f'{letter}:\\')
            if d.exists():
                out.append(d)
    return [r for r in out if str(r).strip()]


def _is_code_dir(p: Path) -> bool:
    s = str(p).replace('\\', '/').lower()
    return any(f'/{d.lower()}/' in s or s.endswith(f'/{d.lower()}') for d in _CODE_DIRS)


def check_read(path, level: str, proj: str) -> tuple:
    """能不能读。返回 (ok, 规范化路径, 拒绝原因)。"""
    try:
        p = Path(str(path)).expanduser()
    except Exception as e:                               # noqa: BLE001
        return False, '', f'路径不合法：{e}'
    if not p.is_absolute():
        base = Path(proj) if proj else Path.home()
        p = (base / p)
    try:
        rp = p.resolve()
    except Exception:                                    # noqa: BLE001
        rp = p
    if not rp.exists():
        return False, str(rp), f'路径不存在：{rp}'
    if level == 'disk':
        return True, str(rp), ''
    for root in _roots(level, proj):
        try:
            if str(rp).lower().startswith(str(root.resolve()).lower()):
                return True, str(rp), ''
        except Exception:                                # noqa: BLE001
            continue
    return False, str(rp), (
        f'这个路径不在当前授权范围内（权限档：{LEVEL_LABEL.get(level, level)}）。'
        f'如果需要访问，请让用户在「大模型设置 → 权限」里把档位调高。')


def check_write(path, level: str, proj: str, allow_write_out: bool) -> tuple:
    """能不能写。返回 (ok, 路径, 原因)。

    ★ 源代码永远不许写，跟权限档位无关。
    """
    ok, rp, why = check_read(path, level if allow_write_out else 'proj', proj)
    if not ok and Path(str(path)).parent.exists():
        # 目标文件可能还不存在，用父目录判定
        parent_ok, parent, why2 = check_read(Path(str(path)).parent,
                                            level if allow_write_out else 'proj', proj)
        if parent_ok:
            ok, rp, why = True, str(Path(parent) / Path(str(path)).name), ''
    if not ok:
        return False, rp, why
    q = Path(rp)
    if _is_code_dir(q) or q.suffix.lower() in _CODE_EXT:
        return False, rp, (
            '拒绝写入源代码目录/文件。智能体只能调用现有代码，不能修改程序本身——'
            '这是硬限制，跟权限档位无关。要改代码请在对话里说明需求，由人来做。')
    return True, rp, ''


def check_python(g: dict) -> tuple:
    """能不能跑 Python。返回 (ok, 原因)。"""
    if not g.get('allow_python'):
        return False, ('当前未开启「允许执行 Python 代码」。'
                       '需要的话请在「大模型设置 → 权限」里勾选。')
    return True, ''
