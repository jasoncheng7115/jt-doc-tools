"""Postprocess fixers — Sprint 1 三個基礎 fixer + 假表格移除 (從 Sprint 2 借過來救急)。"""
from .cleanup import fix_cleanup
from .fake_table_remove import fix_fake_table_remove
from .font_normalize import fix_font_normalize
from .paragraph_merge import fix_paragraph_merge

__all__ = [
    "fix_cleanup", "fix_fake_table_remove",
    "fix_font_normalize", "fix_paragraph_merge",
]
