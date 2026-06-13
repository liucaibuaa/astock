"""Generate PDF reports from analysis results."""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from fpdf import FPDF

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
                # TrueType Collection – fpdf2 may load the wrong sub-font.
                # Skip unless it's the only option.
                continue
            return str(p)
        except Exception:
            continue
    # Last resort: return the Noto path even if it's TTC; fpdf2 will still
    # embed *some* glyphs, which is better than Helvetica (no CJK at all).
    return "/usr/share/fonts/truetype/noto/NotoSansSC-Regular.ttf"


# Default Chinese font
DEFAULT_FONT_PATH = _pick_font()
FONT_NAME = "NotoSansSC"


class ReportPDF(FPDF):
    """PDF report generator with Chinese font support."""

    def __init__(self, font_path: str = DEFAULT_FONT_PATH):
        super().__init__()
        self._font_path = font_path
        if Path(font_path).exists():
            self.add_font(FONT_NAME, "", font_path, uni=True)
            self.add_font(FONT_NAME, "B", font_path, uni=True)
        else:
            # Fallback to standard font if Chinese font missing
            self.set_font("Helvetica", "", 10)

    def header(self):
        if Path(self._font_path).exists():
            self.set_font(FONT_NAME, "B", 14)
        self.cell(0, 10, "Vibe-Trading 投研报告", border=0, align="C")
        self.ln(6)
        if Path(self._font_path).exists():
            self.set_font(FONT_NAME, "", 9)
        self.cell(0, 6, f"生成时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", border=0, align="C")
        self.ln(10)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(5)

    def footer(self):
        self.set_y(-15)
        if Path(self._font_path).exists():
            self.set_font(FONT_NAME, "", 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, f"第 {self.page_no()} 页", align="C")

    def chapter_title(self, title: str):
        if Path(self._font_path).exists():
            self.set_font(FONT_NAME, "B", 12)
        else:
            self.set_font("Helvetica", "B", 12)
        self.set_text_color(0, 0, 0)
        self.cell(0, 8, title, ln=True)
        self.ln(2)

    def chapter_body(self, body: str):
        if Path(self._font_path).exists():
            self.set_font(FONT_NAME, "", 10)
        else:
            self.set_font("Helvetica", "", 10)
        self.set_text_color(50, 50, 50)
        # Clean markdown for PDF
        text = _clean_markdown(body)
        self.multi_cell(0, 5.5, text)
        self.ln(3)

    def add_divider(self):
        self.ln(2)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(3)


def _clean_markdown(text: str) -> str:
    """Strip simple markdown formatting for cleaner PDF output."""
    # Remove bold/italic markers
    text = re.sub(r"\*\*", "", text)
    text = re.sub(r"\*", "", text)
    # Remove code backticks
    text = re.sub(r"`", "", text)
    # Replace headers with plain text
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    # Replace horizontal rules
    text = re.sub(r"^---+\s*$", "-" * 50, text, flags=re.MULTILINE)
    # Reduce multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def generate_pdf(
    content: str,
    title: str = "分析报告",
    stock: str = "",
    output_dir: str = "/home/liucai/Vibe-Trading/reports",
) -> Path:
    """Generate a PDF report from analysis content.

    Args:
        content: The analysis text (markdown supported).
        title: Report title.
        stock: Stock name/code for filename.
        output_dir: Directory to save PDF.

    Returns:
        Path to the generated PDF file.
    """
    pdf = ReportPDF()
    pdf.add_page()

    # Title section
    if stock:
        pdf.chapter_title(f"{title} — {stock}")
    else:
        pdf.chapter_title(title)
    pdf.add_divider()

    # Body
    pdf.chapter_body(content)

    # Save
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_stock = re.sub(r'[\\/:*?"<>|]', "_", stock)[:30] if stock else "analysis"
    filename = out_dir / f"{ts}_{safe_stock}.pdf"
    pdf.output(str(filename))
    print(f"[PDF] Report saved to {filename}")
    return filename
