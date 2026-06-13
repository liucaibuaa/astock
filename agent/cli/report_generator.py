"""Generate PDF/Markdown reports from CLI analysis results.

Mirrors the Feishu bot report flow so that terminal conversations also
persist structured reports under ``reports/``.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Optional

# Project root is two levels above this file: agent/cli -> agent -> project_root
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_REPORTS_DIR = _PROJECT_ROOT / "reports"

# Try to reuse the Feishu bot generator when fpdf2 is available.
try:
    from fpdf import FPDF

    _HAS_FPDF = True
except Exception:  # pragma: no cover
    _HAS_FPDF = False

def _pick_font() -> str:
    """Pick a suitable Chinese font that fpdf2 can handle correctly.

    NotoSansSC-Regular.ttf on some systems is actually a TrueType Collection
    (TTC) where index 0 is "Thin". fpdf2 loads index 0, causing readers to
    render text with the wrong glyph set or show garbled characters.
    We detect TTC by the 'ttcf' magic header and fall back to a plain TTF.
    """
    candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansSC-Regular.ttf",
        "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    ]
    for path in candidates:
        p = Path(path)
        if not p.exists():
            continue
        try:
            with p.open("rb") as f:
                magic = f.read(4)
            if magic == b"ttcf":
                continue
            return str(p)
        except Exception:
            continue
    return "/usr/share/fonts/truetype/noto/NotoSansSC-Regular.ttf"


# Default Chinese font – matches the Feishu bot config
_DEFAULT_FONT_PATH = _pick_font()
_FONT_NAME = "NotoSansSC"


class ReportPDF(FPDF):
    """PDF report generator with Chinese font support."""

    def __init__(self, font_path: str = _DEFAULT_FONT_PATH):
        super().__init__()
        self._font_path = font_path
        if Path(font_path).exists():
            self.add_font(_FONT_NAME, "", font_path, uni=True)
            self.add_font(_FONT_NAME, "B", font_path, uni=True)

    def header(self):
        if Path(self._font_path).exists():
            self.set_font(_FONT_NAME, "B", 14)
        else:
            self.set_font("Helvetica", "B", 14)
        self.cell(0, 10, "Vibe-Trading 投研报告", border=0, align="C")
        self.ln(6)
        if Path(self._font_path).exists():
            self.set_font(_FONT_NAME, "", 9)
        else:
            self.set_font("Helvetica", "", 9)
        self.cell(
            0,
            6,
            f"生成时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            border=0,
            align="C",
        )
        self.ln(10)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(5)

    def footer(self):
        self.set_y(-15)
        if Path(self._font_path).exists():
            self.set_font(_FONT_NAME, "", 8)
        else:
            self.set_font("Helvetica", "", 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, f"第 {self.page_no()} 页", align="C")

    def chapter_title(self, title: str):
        if Path(self._font_path).exists():
            self.set_font(_FONT_NAME, "B", 12)
        else:
            self.set_font("Helvetica", "B", 12)
        self.set_text_color(0, 0, 0)
        self.cell(0, 8, title, ln=True)
        self.ln(2)

    def chapter_body(self, body: str):
        if Path(self._font_path).exists():
            self.set_font(_FONT_NAME, "", 10)
        else:
            self.set_font("Helvetica", "", 10)
        self.set_text_color(50, 50, 50)
        text = _clean_markdown(body)
        self.multi_cell(0, 5.5, text)
        self.ln(3)

    def add_divider(self):
        self.ln(2)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(3)


def _clean_markdown(text: str) -> str:
    """Strip simple markdown formatting for cleaner PDF output."""
    text = re.sub(r"\*\*", "", text)
    text = re.sub(r"\*", "", text)
    text = re.sub(r"`", "", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^---+\s*$", "-" * 50, text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def generate_turn_report(
    content: str,
    title: str = "分析报告",
    stock: str = "",
    run_id: Optional[str] = None,
    elapsed_seconds: float = 0.0,
    output_dir: Optional[str] = None,
) -> Optional[Path]:
    """Save a turn result as Markdown + PDF.

    Args:
        content: The assistant's answer text (markdown supported).
        title: Report title.
        stock: Stock name/code for filename.
        run_id: Optional run identifier to embed in the report.
        elapsed_seconds: How long the turn took.
        output_dir: Directory to save reports. Defaults to ``reports/`` under
            the project root.

    Returns:
        Path to the generated PDF, or *None* when fpdf2 is unavailable.
    """
    out_dir = Path(output_dir) if output_dir else _REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_stock = re.sub(r'[\\/:*?"<>|]', "_", stock)[:30] if stock else "analysis"
    base_name = f"{ts}_{safe_stock}"

    # --- 1. Markdown report ---------------------------------------------------
    md_header = f"## Vibe-Trading 终端投研报告\n\n"
    md_header += f"**生成时间**: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    if elapsed_seconds:
        md_header += f"**耗时**: {int(elapsed_seconds // 60)}分{int(elapsed_seconds % 60)}秒\n"
    if run_id:
        md_header += f"**Run ID**: `{run_id}`\n"
    md_header += "\n---\n\n"
    md_text = md_header + content

    md_path = out_dir / f"{base_name}.md"
    try:
        md_path.write_text(md_text, encoding="utf-8")
        print(f"[Report] Markdown saved to {md_path}")
    except Exception as exc:
        print(f"[Report] Failed to save Markdown: {exc}")

    # --- 2. PDF report --------------------------------------------------------
    if not _HAS_FPDF:
        print(
            "[Report] fpdf2 is not installed; skipping PDF generation. "
            "Install it with: pip install fpdf2"
        )
        return None

    pdf = ReportPDF()
    pdf.add_page()
    display_title = f"{title} — {stock}" if stock else title
    pdf.chapter_title(display_title)
    pdf.add_divider()
    pdf.chapter_body(content)

    pdf_path = out_dir / f"{base_name}.pdf"
    try:
        pdf.output(str(pdf_path))
        print(f"[Report] PDF saved to {pdf_path}")
        return pdf_path
    except Exception as exc:
        print(f"[Report] Failed to save PDF: {exc}")
        return None
