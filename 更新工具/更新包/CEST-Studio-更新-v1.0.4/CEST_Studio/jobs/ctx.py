# -*- coding: utf-8 -*-
"""jobs/ctx.py —— 任务上下文：模块通过它「说话」（日志 / 进度 / 取消），不直接碰界面。

核心层函数不知道 ctx 的存在；只有本软件的壳（app/modules/*.py）用。
"""
from __future__ import annotations

from pathlib import Path

try:                                    # ASCII 键 → 中文目录名
    from detect.rules import cn_name
except Exception:                                        # noqa: BLE001
    def cn_name(x):
        return x


class Cancelled(Exception):
    """用户点了取消。"""


class JobContext:
    def __init__(self, proj=None, det=None):
        self.proj = str(proj) if proj else None      # 输出总目录 PROJ
        self.det = det                               # 当前识别结果（可能为 None）
        self.cancelled = False
        self._sink = None                            # callable(str) → 日志面板
        self._prog = None                            # callable(cur, total, text)

    # ---- 绑定（由界面层调用） ----
    def bind(self, sink=None, progress=None):
        self._sink = sink
        self._prog = progress
        return self

    # ---- 给模块用 ----
    def log(self, msg: str = '') -> None:
        msg = str(msg)
        if self._sink:
            self._sink(msg)
        else:
            print(msg)

    def progress(self, cur: int, total: int = 0, text: str = '') -> None:
        if self._prog:
            self._prog(int(cur), int(total), str(text))

    def check_cancel(self) -> None:
        if self.cancelled:
            raise Cancelled()

    # ---- 目录便捷方法（模块常用） ----
    def out_dir(self, sub: str) -> Path:
        if not self.proj:
            raise RuntimeError('还没选输出总目录（PROJ）')
        d = Path(self.proj) / cn_name(sub)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def mark_cancelled(self, sub: str) -> None:
        try:
            (Path(self.proj) / cn_name(sub)).mkdir(parents=True, exist_ok=True)
            (Path(self.proj) / sub / '_cancelled.txt').write_text(
                '本次运行被取消\n', encoding='utf-8')
        except Exception:                            # noqa: BLE001
            pass
