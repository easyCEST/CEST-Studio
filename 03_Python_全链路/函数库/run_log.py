# -*- coding: utf-8 -*-
"""run_log —— 运行日志：把控制台输出同时写进「日志/」下带时间戳的文件。

主脚本开头调用一次 ``start_log('03_逐体素LD')`` 即可，日志不再和代码混在一起。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / '日志'


class _Tee:
    """同时写多个流（屏幕 + 文件）。"""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            try:
                st.write(s)
                st.flush()
            except Exception:                                    # noqa: BLE001
                pass
        return len(s)

    def flush(self):
        for st in self.streams:
            try:
                st.flush()
            except Exception:                                    # noqa: BLE001
                pass


def start_log(name, log_dir=None) -> Path:
    """开始记录日志，返回日志文件路径（stdout/stderr 会同时写入该文件）。"""
    d = Path(log_dir) if log_dir else LOG_DIR
    d.mkdir(parents=True, exist_ok=True)
    f = d / f'{name}_{time.strftime("%Y%m%d_%H%M%S")}.log'
    fh = open(f, 'w', encoding='utf-8')
    sys.stdout = _Tee(sys.__stdout__, fh)
    sys.stderr = _Tee(sys.__stderr__, fh)
    print(f'#### 本次运行日志：{f}')
    return f
