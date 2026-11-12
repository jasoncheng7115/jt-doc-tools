"""使用者（或經 MCP 的 AI）自己寫的正規式：先擋明顯會很慢的寫法，再放到獨立的執行緒池跑。

Python 的 `re` 沒有逾時。`(a+)+$` 這種「重複裡面又有重複」的寫法，碰到一行不符合的字串就會
指數爆炸（ReDoS），那條執行緒永遠回不來。原本放在 `asyncio.to_thread`（全站共用的執行緒池）裡，
送幾次就把整個服務的背景工作都卡死。

這裡做兩件事：

1. `compile_user()`：長度上限，並用 `re` 自己的解析器找「無上限的重複裡面還有無上限的重複」，
   找到就拒絕（這是 ReDoS 最常見的形狀；不是全部，所以還有第 2 道）。
2. `run()`：放到**這支專用**的小執行緒池（2 條）。真的有一個卡住，最多卡住正規式這一類工具，
   其他工具與網頁照常；池子滿了直接回「忙碌中」，不排隊。
"""
from __future__ import annotations

import asyncio
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

try:  # Python 3.11+
    import re._constants as _c
    import re._parser as _p
except ImportError:  # pragma: no cover —— 3.10
    import sre_constants as _c  # type: ignore[no-redef]
    import sre_parse as _p  # type: ignore[no-redef]

MAX_LEN = 500
WORKERS = 2

_pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="user-regex")
_slots = threading.BoundedSemaphore(WORKERS)
_REPEATS = {_c.MAX_REPEAT, _c.MIN_REPEAT}
_POSS = getattr(_c, "POSSESSIVE_REPEAT", None)


class RegexBusy(Exception):
    """專用執行緒池滿了（多半是前面有正規式卡住）。"""


def _unbounded(op, av) -> bool:
    return op in _REPEATS and av[1] == _c.MAXREPEAT


def _walk(items, inside_unbounded: bool) -> bool:
    """回 True＝找到「無上限重複裡面又有無上限重複」。"""
    for op, av in items:
        if op in _REPEATS:
            unb = av[1] == _c.MAXREPEAT
            if unb and inside_unbounded:
                return True
            if _walk(av[2], inside_unbounded or unb):
                return True
        elif op == _POSS:
            continue                      # 佔有式重複不會回溯
        elif op == _c.SUBPATTERN:
            if _walk(av[-1], inside_unbounded):
                return True
        elif op == _c.BRANCH:
            if any(_walk(b, inside_unbounded) for b in av[1]):
                return True
        elif op in (_c.ASSERT, _c.ASSERT_NOT):
            if _walk(av[1], inside_unbounded):
                return True
        elif op == _c.GROUPREF_EXISTS:
            if any(b is not None and _walk(b, inside_unbounded) for b in av[1:]):
                return True
    return False


def compile_user(pattern: str, flags: int = 0, max_len: int = MAX_LEN) -> re.Pattern:
    """編譯使用者的正規式；太長、寫錯、或是「重複裡面又有重複」都丟 ValueError（訊息可以直接給使用者看）。"""
    if not isinstance(pattern, str) or not pattern:
        raise ValueError("正規式是空的")
    if len(pattern) > max_len:
        raise ValueError(f"正規式太長（上限 {max_len} 字）")
    try:
        parsed = _p.parse(pattern, flags)
        rx = re.compile(pattern, flags)
    except re.error as e:
        raise ValueError(f"正規式寫法有誤：{e}") from None
    except RecursionError:
        raise ValueError("正規式太複雜") from None
    try:
        nested = _walk(list(parsed), False)
    except RecursionError:
        nested = True
    if nested:
        raise ValueError("這個正規式可能非常慢（重複裡面又有重複，例如 (a+)+）；請改寫")
    return rx


async def run(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """在專用執行緒池跑會用到使用者正規式的工作；池子滿了丟 `RegexBusy`（不排隊）。"""
    if not _slots.acquire(blocking=False):
        raise RegexBusy()

    def _job():
        try:
            return fn(*args, **kwargs)
        finally:
            _slots.release()

    try:
        fut = _pool.submit(_job)
    except BaseException:
        _slots.release()
        raise
    return await asyncio.wrap_future(fut)
