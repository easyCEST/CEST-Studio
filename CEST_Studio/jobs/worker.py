# -*- coding: utf-8 -*-
"""jobs/worker.py —— QThread 里跑模块：界面不卡，日志实时回传，可取消。

★ 子线程只算数；出图走 matplotlib 的 Agg（结果落 png 文件），不在子线程建界面。
"""
from __future__ import annotations

import sys
import traceback

from PySide6.QtCore import QThread, Signal

from .ctx import Cancelled, JobContext


class _StreamToLog:
    """把 C 层/核心层的 print 重定向到日志面板。

    ★ 必须防「自己喂自己」：
        write → emit(日志信号) → 界面的日志槽里只要有一句 print（或 warnings
        输出到 stderr），就会再次进 write → 再 emit → 无限递归 → 栈溢出，
        进程当场无提示地死掉（表现为日志只剩一行、进度永远 0%）。
      所以：emit 期间加**重入锁**，重入的写直接丢给原始 stdout，绝不再 emit。
    """

    def __init__(self, emit, fallback=None):
        self._emit = emit
        self._fallback = fallback      # 原始 stdout（重入时用，可为 None）
        self._buf = ''
        self._busy = False

    def write(self, s):
        if self._busy:                 # 已在发日志的链里 → 只落原始 stdout，不再 emit
            if self._fallback is not None:
                try:
                    self._fallback.write(str(s))
                except Exception:                            # noqa: BLE001
                    pass
            return len(s)
        self._busy = True
        try:
            self._buf += s
            while '\n' in self._buf:
                line, self._buf = self._buf.split('\n', 1)
                self._emit(line)
        finally:
            self._busy = False
        return len(s)

    def flush(self):
        if self._busy or not self._buf:
            return
        self._busy = True
        try:
            self._emit(self._buf)
            self._buf = ''
        finally:
            self._busy = False

    def isatty(self):
        return False


class JobThread(QThread):
    sig_log = Signal(str)
    sig_progress = Signal(int, int, str)
    sig_done = Signal(object)
    sig_fail = Signal(str, str)

    def __init__(self, module, params, proj=None, det=None, parent=None):
        super().__init__(parent)
        self.module = module
        self.params = params
        self.ctx = JobContext(proj=proj, det=det)
        self.ctx.bind(sink=self.sig_log.emit, progress=self.sig_progress.emit)

    def cancel(self):
        self.ctx.cancelled = True

    def run(self):
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = _StreamToLog(self.sig_log.emit, fallback=old_out)
        try:
            self.ctx.log(f'▶ 开始：{self.module.badge} {self.module.title}')
            res = self.module.run(self.params, self.ctx)
            sys.stdout.flush()
            self.ctx.log(f'✔ 完成：{self.module.badge} {self.module.title}')
            self.sig_done.emit(res)
        except Cancelled:
            sys.stdout.flush()
            self.ctx.log('■ 已取消')
            try:
                self.ctx.mark_cancelled(self.module.out_subdir)
            except Exception:                        # noqa: BLE001
                pass
            self.sig_fail.emit('已取消', '')
        except Exception as e:                       # noqa: BLE001
            sys.stdout.flush()
            tb = traceback.format_exc()
            self.ctx.log(f'× 失败：{type(e).__name__}: {e}')
            self.ctx.log(tb)
            self.sig_fail.emit(f'{type(e).__name__}: {e}', tb)
        finally:
            sys.stdout, sys.stderr = old_out, old_err

