from io import BytesIO
from pathlib import Path

import pytest
from docx import Document
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def read(file, name):
    from apps.deskpet.backend.notice_documents import read_document

    with read_document(file, name) as (total, pages):
        result = list(pages)
    assert total == len(result)
    return result


def test_word_preserves_paragraphs_and_tables():
    document = Document()
    document.add_heading("交付要求", level=1)
    document.add_paragraph("提交源代码")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "交付物"
    table.cell(0, 1).text = "日期"
    table.cell(1, 0).text = "提交答辩稿"
    table.cell(1, 1).text = "12月3日 18:00"
    document.add_paragraph("提交测试报告")
    file = BytesIO()
    document.save(file)
    file.seek(0)

    pages = read(file, "作业.docx")
    text = "\n".join(page.text for page in pages)
    assert text.index("提交源代码") < text.index("提交答辩稿") < text.index("提交测试报告")
    table_page = next(page for page in pages if "提交答辩稿" in page.text)
    assert "表格" in table_page.location
    assert "12月3日 18:00" in table_page.text


def test_word_recognizes_an_image_inside_a_table():
    document = Document()
    cell = document.add_table(rows=1, cols=1).cell(0, 0)
    cell.text = "提交要求"
    image = BytesIO()
    transparent = Image.new("RGBA", (960, 260))
    ImageDraw.Draw(transparent).text((40, 60), "请提交实验报告", font=windows_font("msyh.ttc"), fill="black")
    transparent.save(image, format="PNG")
    cell.paragraphs[0].add_run().add_picture(image)
    file = BytesIO()
    document.save(file)
    file.seek(0)

    [section] = read(file, "图片表格.docx")
    assert section.text == "提交要求"
    assert "表格" in section.location
    assert "请提交实验报告" in section.ocr_text


@pytest.mark.parametrize("encoding", ["utf-8-sig", "gb18030"])
def test_text_encoding(encoding):
    file = BytesIO("请提交实验报告".encode(encoding))
    assert read(file, "notice.TXT")[0].text == "请提交实验报告"


def add_text_page(writer, text):
    page = writer.add_blank_page(width=500, height=500)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
    })
    content = DecodedStreamObject()
    content.set_data(f"BT /F1 18 Tf 50 455 Td ({text}) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(content)
    return page


def notice_image(text, font):
    image = Image.new("RGB", (960, 260), "white")
    ImageDraw.Draw(image).text((40, 60), text, font=font, fill="black")
    return image


def image_pdf(text, font):
    stream = BytesIO()
    notice_image(text, font).save(stream, format="PDF", resolution=144)
    stream.seek(0)
    return PdfReader(stream).pages[0]


def windows_font(name):
    candidate = Path("C:/Windows/Fonts", name)
    if not candidate.is_file():
        pytest.skip(f"Fixture font missing: {name}")
    return ImageFont.truetype(str(candidate), 60)


def test_pdf_recognizes_mixed_image_on_page_31_and_full_scanned_page():
    font = windows_font("arial.ttf")
    writer = PdfWriter()
    for number in range(1, 31):
        add_text_page(writer, f"Background notes {number}")
    mixed = add_text_page(writer, "This is editable PDF text")
    mixed.merge_page(image_pdf("Submit the ZIP archive", font))
    writer.add_page(image_pdf("Deadline December 12", font))
    file = BytesIO()
    writer.write(file)
    file.seek(0)

    pages = read(file, "长通知.pdf")
    assert pages[30].location == "第 31 页"
    assert "This is editable PDF text" in pages[30].text
    assert "Submit the ZIP archive" in pages[30].ocr_text
    assert "This is editable PDF text" not in pages[30].ocr_text
    assert "Deadline December 12" in pages[31].ocr_text


def test_pdf_recognizes_simplified_chinese_from_a_scanned_page():
    writer = PdfWriter()
    writer.add_page(image_pdf("请提交实验报告", windows_font("msyh.ttc")))
    file = BytesIO()
    writer.write(file)
    file.seek(0)

    assert "请提交实验报告" in read(file, "扫描通知.pdf")[0].ocr_text


def test_pdf_preserves_distinct_deadlines_when_ocr_is_merged():
    writer = PdfWriter()
    page = add_text_page(writer, "Deadline 12/2")
    page.merge_page(image_pdf("Deadline 1/22", windows_font("arial.ttf")))
    file = BytesIO()
    writer.write(file)

    [result] = read(file, "日期.pdf")
    assert "Deadline 12/2" in result.text
    assert "Deadline 1/22" in result.ocr_text


def test_pdf_keeps_native_text_when_local_ocr_is_unavailable(monkeypatch):
    from apps.deskpet.backend import notice_documents

    def missing_engine():
        raise ImportError("OCR backend not installed")

    monkeypatch.setattr(notice_documents, "_local_ocr", missing_engine)
    writer = PdfWriter()
    page = add_text_page(writer, "Visible notice")
    page.merge_page(image_pdf("Submit the ZIP archive", windows_font("arial.ttf")))
    file = BytesIO()
    writer.write(file)

    [result] = read(file, "部分读取.pdf")
    assert result.text == "Visible notice"
    assert not result.ocr_text
    assert any("OCR" in warning for warning in result.warnings)
