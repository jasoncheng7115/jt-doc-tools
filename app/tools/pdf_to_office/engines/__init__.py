"""PDF → docx / odt 轉換引擎封裝。"""
from .jtdt_reform import convert_via_jtdt_native, convert_via_jtdt_reform
from .libreoffice_engine import convert_via_libreoffice, docx_to_odt
from .pdf2docx_engine import convert_via_pdf2docx

__all__ = [
    "convert_via_pdf2docx",
    "convert_via_jtdt_reform",
    "convert_via_jtdt_native",   # alias 向後相容
    "convert_via_libreoffice",
    "docx_to_odt",
]
