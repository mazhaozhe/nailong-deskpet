"""Read notices locally and keep selectable text separate from OCR evidence."""

from __future__ import annotations

import logging
import math
import unicodedata
from contextlib import closing, contextmanager
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from threading import Lock
from typing import BinaryIO, Iterable, Iterator
from zipfile import BadZipFile

import pypdfium2 as pdfium
from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from lxml.etree import XMLSyntaxError
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)
DOCUMENT_SUFFIXES = frozenset({".pdf", ".docx", ".txt"})
_MAX_IMAGE_SIDE = 2000
_MAX_WORD_IMAGE_PIXELS = 25_000_000
_PDFIUM_LOCK = Lock()  # PDFium calls cannot run concurrently, even on separate files.
_OCR_LOCK = Lock()  # RapidOCR keeps mutable parameters on the shared engine.


class DocumentReadError(ValueError):
    """A document could not be opened or decoded."""


@dataclass(frozen=True)
class DocumentPage:
    location: str
    text: str
    ocr_text: str = ""
    warnings: tuple[str, ...] = ()


@lru_cache(maxsize=1)
def _local_ocr():
    import rapidocr

    models = Path(rapidocr.__file__).resolve().parent / "models"
    # Explicit paths keep missing assets from triggering an automatic download.
    return rapidocr.RapidOCR(params={
        "Det.model_path": str(models / "PP-OCRv6_det_small.onnx"),
        "Cls.model_path": str(models / "ch_ppocr_mobile_v2.0_cls_mobile.onnx"),
        "Rec.model_path": str(models / "PP-OCRv6_rec_small.onnx"),
        "EngineConfig.onnxruntime.intra_op_num_threads": 2,
        "EngineConfig.onnxruntime.inter_op_num_threads": 1,
        "Global.log_level": "warning",
    })


class _DocumentOcr:
    def __init__(self):
        self.unavailable = False

    def recognize(self, image: Image.Image) -> tuple[tuple[str, ...], str]:
        unavailable_warning = "本地 OCR 无法启动，图片文字可能缺失。"
        if self.unavailable:
            return (), unavailable_warning
        with _OCR_LOCK:
            try:
                engine = _local_ocr()
            except Exception:
                self.unavailable = True
                logger.exception("Unable to initialize local notice OCR")
                return (), unavailable_warning
            try:
                lines = engine(image).txts or ()
                return tuple(lines), ""
            except Exception:
                logger.exception("Local notice OCR failed on an image")
                return (), "图片识别失败，图片文字可能缺失。"


def _normalized_line(line: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", line).casefold().split())


def _additional_text(text: str, lines: Iterable[str]) -> str:
    seen = {_normalized_line(line) for line in text.splitlines()}
    additional = []
    for line in lines:
        key = _normalized_line(line)
        if key not in seen and any(char.isalnum() for char in key):
            additional.append(line.strip())
            seen.add(key)
    return "\n".join(additional)


def _pdf_page_content(pdf, index: int) -> tuple[str, Image.Image | None, list[str]]:
    warnings = []
    with _PDFIUM_LOCK, closing(pdf[index]) as page:
        try:
            with closing(page.get_textpage()) as text_page:
                text = text_page.get_text_bounded().replace("\r\n", "\n").strip()
        except pdfium.PdfiumError:
            text = ""
            warnings.append("读取 PDF 文本层失败。")

        try:
            objects = [pdfium.raw.FPDF_PAGEOBJ_IMAGE] if text else None
            has_content = next(page.get_objects(filter=objects), None) is not None
            has_annotations = not text and pdfium.raw.FPDFPage_GetAnnotCount(page) > 0
            if not (has_content or has_annotations):
                return text, None, warnings
            width, height = page.get_size()
            longest_side = max(width, height)
            if min(width, height) <= 0 or not math.isfinite(longest_side):
                raise ValueError("Invalid PDF page size")
            scale = min(3, _MAX_IMAGE_SIDE / longest_side)
            with closing(page.render(scale=scale, rev_byteorder=True)) as bitmap:
                return text, bitmap.to_pil().copy(), warnings
        except (pdfium.PdfiumError, ValueError, OSError):
            warnings.append("无法渲染 PDF 页面，图片文字可能缺失。")
            return text, None, warnings


def _pdf_pages(pdf, total: int) -> Iterator[DocumentPage]:
    ocr = _DocumentOcr()
    for index in range(total):
        location = f"第 {index + 1} 页"
        try:
            text, image, warnings = _pdf_page_content(pdf, index)
        except pdfium.PdfiumError:
            yield DocumentPage(location, "", warnings=("无法打开这一页。",))
            continue
        ocr_text = ""
        if image is not None:
            with image:
                lines, warning = ocr.recognize(image)
            if warning:
                warnings.append(warning)
            ocr_text = _additional_text(text, lines)
        yield DocumentPage(location, text, ocr_text, tuple(warnings))


def _table_text(table: Table) -> str:
    rows = []
    seen = set()
    for row in table.rows:
        cells = []
        for cell in row.cells:
            if cell._tc in seen:
                continue
            seen.add(cell._tc)
            content = [block.text if isinstance(block, Paragraph) else _table_text(block)
                       for block in cell.iter_inner_content()]
            cells.append(" / ".join(text.strip() for text in content if text.strip()))
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def _word_image_lines(block: Paragraph | Table, ocr: _DocumentOcr, warnings: list[str]) -> Iterator[str]:
    image_ids = dict.fromkeys(blip.get(qn("r:embed")) for blip in block._element.xpath(".//a:blip"))
    for image_id in image_ids:
        if image_id is None:
            continue
        try:
            part = block.part.related_parts[image_id]
            with Image.open(BytesIO(part.blob)) as source:
                if source.width * source.height > _MAX_WORD_IMAGE_PIXELS:
                    raise ValueError("Word image exceeds OCR pixel limit")
                with ImageOps.exif_transpose(source) as image:
                    image.thumbnail((_MAX_IMAGE_SIDE, _MAX_IMAGE_SIDE))
                    with image.convert("RGBA") as rgba, Image.new("RGB", image.size, "white") as rgb:
                        rgb.paste(rgba, mask=rgba.getchannel("A"))
                        lines, warning = ocr.recognize(rgb)
        except (KeyError, OSError, ValueError, Image.DecompressionBombError):
            warnings.append("无法读取 Word 内嵌图片，图片文字可能缺失。")
            continue
        if warning:
            warnings.append(warning)
        yield from lines


def _word_pages(blocks: list[Paragraph | Table]) -> Iterator[DocumentPage]:
    ocr = _DocumentOcr()
    heading = ""
    para_count = table_count = 0
    for block in blocks:
        if isinstance(block, Table):
            table_count += 1
            location = f"表格 {table_count}"
            text = _table_text(block)
        else:
            para_count += 1
            location = f"第 {para_count} 段"
            text = block.text.strip()
            if text and block.style.style_id.startswith(("Heading", "Title")):
                heading = " ".join(text.split())[:100]
        if heading:
            location = f"{heading} · {location}"
        warnings: list[str] = []
        ocr_text = _additional_text(text, _word_image_lines(block, ocr, warnings))
        yield DocumentPage(location, text, ocr_text, tuple(dict.fromkeys(warnings)))


def _plain_text(file: BinaryIO) -> str:
    data = file.read()
    encodings = ("utf-16",) if data.startswith((b"\xff\xfe", b"\xfe\xff")) else ("utf-8-sig", "gb18030")
    for encoding in encodings:
        try:
            return data.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    raise DocumentReadError("无法读取 TXT 编码，请另存为 UTF-8 后重试。")


@contextmanager
def read_document(file: BinaryIO, filename: str) -> Iterator[tuple[int, Iterator[DocumentPage]]]:
    """Yield the section count and a streaming iterator over the selected document."""
    suffix = Path(filename).suffix.lower()
    file.seek(0)
    if suffix == ".pdf":
        try:
            with _PDFIUM_LOCK:
                pdf = pdfium.PdfDocument(file)
        except pdfium.PdfiumError as exc:
            if exc.err_code == pdfium.raw.FPDF_ERR_PASSWORD:
                raise DocumentReadError("请先解除 PDF 密码保护，再选择文件。") from exc
            raise DocumentReadError("无法打开 PDF，请检查文件是否完整。") from exc
        try:
            with _PDFIUM_LOCK:
                total = len(pdf)
            yield total, _pdf_pages(pdf, total)
        finally:
            with _PDFIUM_LOCK:
                pdf.close()
    elif suffix == ".docx":
        try:
            blocks = list(Document(file).iter_inner_content())
        except (BadZipFile, PackageNotFoundError, XMLSyntaxError, KeyError, ValueError) as exc:
            raise DocumentReadError("无法打开 Word 文档，请检查文件是否完整。") from exc
        yield len(blocks), _word_pages(blocks)
    elif suffix == ".txt":
        yield 1, iter((DocumentPage("全文", _plain_text(file)),))
    else:
        raise DocumentReadError("仅支持 PDF、DOCX 和 TXT 文件。")
