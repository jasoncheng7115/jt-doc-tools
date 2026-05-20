"""jtdt-reform — Jason Tools 自家開發 PDF → docx 引擎（v1.8.72 起，原 jtdt-native）。

「Re-form」字面：從 placed format（PDF 絕對座標）重新成形為 flow format（docx
段落 / 表格）。不依賴 pdf2docx；直接讀 PyMuPDF 抽出的 PDFTruth（blocks /
drawings / images / 超連結）按通用幾何規則重建。

對比 jtdt-refine（A engine）：
- jtdt-refine = 修補 pdf2docx 上游輸出（refine 既有結果）
- jtdt-reform = 從零重建（reform 整份文件）

設計動機：pdf2docx 在「無框線排版 / 跨欄合 cell / 超連結 / 絕對位置標題」等
情境會出結構性錯誤，後處理 fixer 無法治本。jtdt-reform 按 PDF 真值順序重建，能
根治上游結構錯誤。

主要演算法（v1-v9）：
- table_detector：drawings 水平 / 垂直線 cluster 成 grid
- virtual_table_detector：同 Y 短 line 群聚成虛擬 row
- paragraph_grouper：line-level 配對 cell；非 table line 用 Y 距群聚成段落
- docx_builder：Y 序混排；line 級 hyperlink / color / bold / italic / alignment
"""
from .engine import convert_via_jtdt_reform, convert_via_jtdt_reform_to_odt

# 舊名 alias（保留 v1.8.63-71 命名相容性）
convert_via_jtdt_native = convert_via_jtdt_reform

__all__ = ["convert_via_jtdt_reform",
           "convert_via_jtdt_reform_to_odt",
           "convert_via_jtdt_native"]
