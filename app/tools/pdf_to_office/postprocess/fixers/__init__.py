"""Postprocess fixers — Sprint 1 + Sprint 2。"""
from .cjk_typography import fix_cjk_typography
from .cleanup import fix_cleanup
from .fake_table_remove import fix_fake_table_remove
from .font_normalize import fix_font_normalize
from .header_footer import fix_header_footer
from .heading_detect import fix_heading_detect
from .image_position_fix import fix_image_position_fix
from .link_text_recovery import fix_link_text_recovery
from .list_detect import fix_list_detect
from .paragraph_line_split import fix_paragraph_line_split
from .paragraph_merge import fix_paragraph_merge
from .paragraph_split import fix_paragraph_split
from .table_autofit import fix_table_autofit
from .table_bbox_width import fix_table_bbox_width
from .table_borders_from_image import fix_table_borders_from_image
from .table_cell_dedup_text import fix_table_cell_dedup_text
from .table_cell_repair import fix_table_cell_repair
from .table_dedup_cells import fix_table_dedup_cells
from .table_empty_cell_recovery import fix_table_empty_cell_recovery
from .table_normalize import fix_table_normalize
from .table_unmerge_with_pdf_labels import fix_table_unmerge_with_pdf_labels
from .text_recovery import fix_text_recovery
from .title_split import fix_title_split

__all__ = [
    "fix_cjk_typography", "fix_cleanup", "fix_fake_table_remove",
    "fix_font_normalize", "fix_header_footer", "fix_heading_detect",
    "fix_image_position_fix", "fix_link_text_recovery", "fix_list_detect",
    "fix_paragraph_line_split", "fix_paragraph_merge", "fix_paragraph_split",
    "fix_table_autofit", "fix_table_bbox_width", "fix_table_borders_from_image",
    "fix_table_cell_dedup_text", "fix_table_cell_repair",
    "fix_table_dedup_cells", "fix_table_empty_cell_recovery",
    "fix_table_normalize", "fix_table_unmerge_with_pdf_labels",
    "fix_text_recovery", "fix_title_split",
]
