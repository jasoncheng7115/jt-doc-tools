"""Postprocess fixers — Sprint 1 三個基礎 fixer。"""
from .cleanup import fix_cleanup
from .font_normalize import fix_font_normalize
from .paragraph_merge import fix_paragraph_merge

__all__ = ["fix_cleanup", "fix_font_normalize", "fix_paragraph_merge"]
