"""台灣電子發票報帳期計算。

期別規則：兩個月為一期 (1-2 / 3-4 / 5-6 / 7-8 / 9-10 / 11-12)
報帳月：該期結束後的下個月 1-15 日

| 期 | 月份 | 報帳月 |
|---|---|---|
| 1 | 1-2  | 3 月 1-15 |
| 2 | 3-4  | 5 月 1-15 |
| 3 | 5-6  | 7 月 1-15 |
| 4 | 7-8  | 9 月 1-15 |
| 5 | 9-10 | 11 月 1-15 |
| 6 | 11-12 | 隔年 1 月 1-15 |

「最近需要報帳的一期」(預設規則)：
- 在報帳月 1-15 日（如 5/1-5/15）→ 報「上一期」(3-4 月)
- 在報帳月 16 日 ~ 下個月底（如 5/16-6/30）→ 仍報「上一期」(3-4 月)，已過繳費截止但仍是最近結束的期
- 在新報帳月 1-15 日（如 7/1-7/15）→ 報「上上期」(5-6 月)

簡化邏輯：找「end_date <= today」內最近結束的期。
"""
from __future__ import annotations

from datetime import date
from typing import Optional


def period_for(year: int, period_no: int) -> tuple[date, date]:
    """回傳指定 (年, 期) 的起訖日期。
    period_no: 1~6
    """
    if not (1 <= period_no <= 6):
        raise ValueError(f"period_no 必須在 1-6，得到 {period_no}")
    start_month = (period_no - 1) * 2 + 1   # 1, 3, 5, 7, 9, 11
    end_month = start_month + 1              # 2, 4, 6, 8, 10, 12
    start = date(year, start_month, 1)
    # end = end_month 的最後一天（用下月 1 日 - 1）
    if end_month == 12:
        end = date(year, 12, 31)
    else:
        from datetime import timedelta
        end = date(year, end_month + 1, 1) - timedelta(days=1)
    return start, end


def latest_filing_period(today: Optional[date] = None) -> dict:
    """回「最近需要報帳的一期」資訊。

    規則：找含 (today.month - 1) 的期。實務經驗：
    - 4/28 → today.month-1=3 → 3-4 月期（正在收的期）✓
    - 5/10 → today.month-1=4 → 3-4 月期（報帳月中）✓
    - 5/20 → today.month-1=4 → 3-4 月期（剛過 5/15 截止但仍是這一期）✓
    - 6/1  → today.month-1=5 → 5-6 月期（5-6 期正在收）
    - 7/15 → today.month-1=6 → 5-6 月期（5-6 期報帳月中）
    - 1/15 → today.month-1=0 → 去年 11-12 月期 (跨年特例)

    Returns dict: {year, period_no, start, end, label}
    """
    today = today or date.today()
    target_year = today.year
    target_month = today.month - 1
    if target_month == 0:  # 1 月 → 去年 12 月
        target_year -= 1
        target_month = 12
    # 找含 target_month 的期：(target_month + 1) // 2 = period_no
    period_no = (target_month + 1) // 2
    s, e = period_for(target_year, period_no)
    return {
        "year": target_year,
        "period_no": period_no,
        "start": s.isoformat(),
        "end": e.isoformat(),
        "label": _format_label(target_year, period_no),
    }


def all_recent_periods(today: Optional[date] = None, n: int = 6) -> list[dict]:
    """回最近 n 期（含當前進行中的期），最新在前。給 UI 下拉選別期用。
    只包含已開始的期（start <= today），不列未來期。"""
    today = today or date.today()
    out = []
    seen = set()
    for y in (today.year, today.year - 1, today.year - 2):
        for p in range(1, 7):
            s, e = period_for(y, p)
            if (y, p) in seen:
                continue
            if s > today:
                continue  # 還沒開始的期不列
            seen.add((y, p))
            out.append({
                "year": y, "period_no": p,
                "start": s.isoformat(), "end": e.isoformat(),
                "label": _format_label(y, p) + (" (進行中)" if e >= today else ""),
            })
    out.sort(key=lambda x: x["start"], reverse=True)
    return out[:n]


def _format_label(year: int, period_no: int) -> str:
    """例：2026 年 第 3 期 (5-6 月)"""
    sm = (period_no - 1) * 2 + 1
    em = sm + 1
    return f"{year} 年第 {period_no} 期 ({sm}-{em} 月)"


def is_in_period(invoice_date_str: str, start_str: str, end_str: str) -> bool:
    """檢查發票日期是否在期內。
    invoice_date_str: ISO 'YYYY-MM-DD' 或 'YYYY/MM/DD' 或民國 'NNN/MM/DD'
    """
    if not invoice_date_str:
        return False
    d = _parse_date(invoice_date_str)
    if not d:
        return False
    s = _parse_date(start_str)
    e = _parse_date(end_str)
    if not s or not e:
        return False
    return s <= d <= e


def _parse_date(s: str) -> Optional[date]:
    """嘗試多種格式 parse 日期。"""
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    # ISO YYYY-MM-DD
    for sep in ("-", "/"):
        parts = s.split(sep)
        if len(parts) == 3:
            try:
                y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
                if y < 1911:
                    # 民國年 → 西元
                    y += 1911
                return date(y, m, d)
            except (ValueError, TypeError):
                continue
    return None
