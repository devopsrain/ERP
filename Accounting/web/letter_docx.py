"""
Letter DOCX Generator

Produces a formatted .docx letter by either:
  1. Filling the company's Templates.docx (if present), or
  2. Building a clean letter from scratch using python-docx.

Embedded base-64 signatures are placed at the bottom.
"""

import base64
import io
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Where the official template lives (root of the repo)
_REPO_ROOT     = Path(__file__).parent.parent
_TEMPLATE_DOCX = _REPO_ROOT / "Templates.docx"   # the file found in the workspace
_OUTPUT_DIR    = Path(__file__).parent / "data" / "letters" / "docx"
_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _add_sig_image(para, data_url: str, width_cm: float = 4.0):
    """Insert a signature image (data-URL) into a paragraph."""
    try:
        from docx.shared import Cm
        from docx.oxml.ns import qn
        header, b64data = data_url.split(",", 1)
        img_bytes = base64.b64decode(b64data)
        img_stream = io.BytesIO(img_bytes)
        run = para.add_run()
        run.add_picture(img_stream, width=Cm(width_cm))
    except Exception as e:
        logger.warning("Could not embed signature image: %s", e)
        para.add_run("[Signature image unavailable]")


def generate_letter_docx(letter: dict, signatures: dict) -> Optional[str]:
    """
    Generate a .docx file for the given letter dict.

    Args:
        letter:     Letter record dict from letter_data_store.
        signatures: {role: {data_url, saved_at, ...}} from get_all_signatures().

    Returns:
        Absolute path to the generated .docx file, or None on failure.
    """
    try:
        from docx import Document
        from docx.shared import Pt, Cm, RGBColor
        from docx.enum.text import WD_ALIGN_PARAGRAPH

        # Start from template if available, else blank
        if _TEMPLATE_DOCX.exists():
            doc = Document(str(_TEMPLATE_DOCX))
            # Clear all paragraphs from template body — we'll write fresh content
            # but keep styles/headers/footers defined in the template
            for para in doc.paragraphs:
                para.clear()
            # Remove all but the first paragraph (needed for structure)
            while len(doc.paragraphs) > 1:
                p = doc.paragraphs[-1]._element
                p.getparent().remove(p)
        else:
            doc = Document()
            # Set page margins
            for section in doc.sections:
                section.top_margin    = Cm(2.5)
                section.bottom_margin = Cm(2.5)
                section.left_margin   = Cm(3.0)
                section.right_margin  = Cm(2.5)

        def _para(text="", bold=False, size=11, align=WD_ALIGN_PARAGRAPH.LEFT,
                  space_after=6, color=None):
            p = doc.add_paragraph()
            p.alignment = align
            p.paragraph_format.space_after = Pt(space_after)
            run = p.add_run(text)
            run.bold = bold
            run.font.size = Pt(size)
            if color:
                run.font.color.rgb = RGBColor(*color)
            return p

        # ── Header: reference & date ─────────────────────────────────────
        ref_para = doc.add_paragraph()
        ref_para.paragraph_format.space_after = Pt(4)
        r_run = ref_para.add_run(f"Ref: {letter.get('ref_number', '')}")
        r_run.bold = True
        r_run.font.size = Pt(10)

        date_para = doc.add_paragraph()
        date_para.paragraph_format.space_after = Pt(12)
        date_run = date_para.add_run(f"Date: {letter.get('date', datetime.now().strftime('%B %d, %Y'))}")
        date_run.font.size = Pt(10)

        # ── Addressee ────────────────────────────────────────────────────
        if letter.get("to"):
            _para(f"To: {letter['to']}", bold=True, size=11)
        if letter.get("to_address"):
            for line in letter["to_address"].splitlines():
                _para(line, size=10)

        doc.add_paragraph()  # spacer

        # ── Subject ──────────────────────────────────────────────────────
        subj_p = doc.add_paragraph()
        subj_p.paragraph_format.space_after = Pt(14)
        subj_run = subj_p.add_run(f"Subject: {letter.get('subject', '')}")
        subj_run.bold = True
        subj_run.font.size = Pt(11)
        subj_run.underline = True

        # ── Salutation ───────────────────────────────────────────────────
        _para("Dear Sir / Madam,", size=11, space_after=10)

        # ── Body ─────────────────────────────────────────────────────────
        for para_text in (letter.get("body") or "").split("\n\n"):
            _para(para_text.strip(), size=11, space_after=10)

        # ── Closing ──────────────────────────────────────────────────────
        _para("Yours faithfully,", size=11, space_after=24)

        # ── CC ───────────────────────────────────────────────────────────
        if letter.get("cc"):
            _para(f"CC: {letter['cc']}", size=10, color=(100, 100, 100), space_after=4)

        doc.add_paragraph()  # spacer

        # ── Signatures ───────────────────────────────────────────────────
        # Arrange PM, FM, MD side by side using a 3-column table
        sig_table = doc.add_table(rows=3, cols=3)
        sig_table.style = "Table Grid"
        col_labels = ["Project Manager (PM)", "Finance Manager (FM)", "Managing Director (MD)"]
        role_keys  = ["PM", "FM", "MD"]

        # Row 0: role title
        for j, label in enumerate(col_labels):
            cell = sig_table.cell(0, j)
            cell.paragraphs[0].clear()
            run = cell.paragraphs[0].add_run(label)
            run.bold = True
            run.font.size = Pt(9)

        # Row 1: signature images
        for j, role in enumerate(role_keys):
            cell = sig_table.cell(1, j)
            cell.paragraphs[0].clear()
            sig = signatures.get(role)
            if sig and sig.get("data_url"):
                _add_sig_image(cell.paragraphs[0], sig["data_url"], width_cm=3.5)
            else:
                p = cell.paragraphs[0]
                run = p.add_run("_________________________")
                run.font.size = Pt(9)

        # Row 2: signed at / name
        for j, role in enumerate(role_keys):
            cell = sig_table.cell(2, j)
            cell.paragraphs[0].clear()
            sig = signatures.get(role)
            letter_sigs = letter.get("signatures", {})
            if role in letter_sigs:
                info = letter_sigs[role]
                ts   = info.get("signed_at", "")
                if ts:
                    try:
                        ts = datetime.fromisoformat(ts).strftime("%d %b %Y %H:%M")
                    except Exception:
                        pass
                txt = f"Signed: {info.get('signed_by', '')}\n{ts}"
            else:
                txt = "Not yet signed"
            run = cell.paragraphs[0].add_run(txt)
            run.font.size = Pt(8)

        # ── Save ─────────────────────────────────────────────────────────
        out_name = f"letter_{letter.get('ref_number', letter['letter_id'])}.docx"
        out_path = _OUTPUT_DIR / out_name
        doc.save(str(out_path))
        logger.info("Generated letter docx: %s", out_path)
        return str(out_path)

    except Exception as e:
        logger.error("generate_letter_docx failed: %s", e, exc_info=True)
        return None
