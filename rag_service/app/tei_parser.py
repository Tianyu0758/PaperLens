# 文件说明：从 Grobid 的 TEI XML 中提取论文元数据、摘要、章节、段落及图表说明等文字。
# 保留章节层级并判断章节类型，输出供切块和定向阅读使用的结构化论文内容。

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lxml import etree


TEI_NS = {"tei": "http://www.tei-c.org/ns/1.0"}


@dataclass(slots=True)
class PaperMetadata:
    title: str | None
    authors: list[str]
    year: int | None
    doi: str | None


@dataclass(slots=True)
class ParsedSection:
    section_path: str
    section_type: str
    paragraphs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ParsedPaper:
    metadata: PaperMetadata
    sections: list[ParsedSection]


SECTION_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("abstract",), "abstract"),
    (("introduction",), "introduction"),
    (("method", "approach", "methodology", "experimental setup", "data collection", "materials"), "methods"),
    (("result", "evaluation", "experiment", "finding"), "results"),
    (("discussion", "limitation"), "discussion"),
    (("conclusion", "future work"), "conclusion"),
    (("reference", "bibliography"), "references"),
)


def _text(element: etree._Element | None) -> str:
    if element is None:
        return ""
    return " ".join(part.strip() for part in element.itertext() if part.strip())


def classify_section(heading: str) -> str:
    normalized = heading.casefold()
    for keywords, section_type in SECTION_RULES:
        if any(keyword in normalized for keyword in keywords):
            return section_type
    return "other"


def _extract_year(root: etree._Element) -> int | None:
    date = root.find(".//tei:teiHeader//tei:publicationStmt//tei:date", TEI_NS)
    candidates = [] if date is None else [date.get("when", ""), _text(date)]
    for candidate in candidates:
        match = re.search(r"\b(19|20)\d{2}\b", candidate)
        if match:
            return int(match.group(0))
    return None


def _extract_authors(root: etree._Element) -> list[str]:
    authors: list[str] = []
    for author in root.xpath(".//tei:teiHeader//tei:sourceDesc//tei:author", namespaces=TEI_NS):
        name = _text(author.find(".//tei:persName", TEI_NS)) or _text(author)
        if name and name not in authors:
            authors.append(name)
    return authors


def _extract_metadata(root: etree._Element) -> PaperMetadata:
    title_element = root.find(".//tei:teiHeader//tei:titleStmt/tei:title", TEI_NS)
    if title_element is None or not _text(title_element):
        title_candidates = root.xpath(
            ".//tei:teiHeader//tei:title[@type='main'] | .//tei:teiHeader//tei:title",
            namespaces=TEI_NS,
        )
        title_element = next((item for item in title_candidates if _text(item)), None)
    title = _text(title_element) or None
    doi = None
    for identifier in root.xpath(".//tei:teiHeader//tei:idno", namespaces=TEI_NS):
        if (identifier.get("type") or "").casefold() == "doi":
            doi = _text(identifier) or None
            break
    return PaperMetadata(
        title=title,
        authors=_extract_authors(root),
        year=_extract_year(root),
        doi=doi,
    )


def _caption_text(element: etree._Element) -> str:
    label = _text(element.find("./tei:head", TEI_NS))
    description = _text(element.find("./tei:figDesc", TEI_NS))
    body = description or _text(element)
    return " ".join(part for part in (label, body) if part).strip()


def _direct_content(div: etree._Element) -> list[str]:
    paragraphs: list[str] = []
    pending: list[str] = []
    for child in div:
        local_name = etree.QName(child).localname
        if local_name == "p":
            text = _text(child)
            if text:
                if pending:
                    text = f"{' '.join(pending)}\n{text}"
                    pending.clear()
                paragraphs.append(text)
        elif local_name in {"figure", "table", "formula"}:
            attachment = _caption_text(child) if local_name != "formula" else _text(child)
            if not attachment:
                continue
            tagged = f"[{local_name.upper()}] {attachment}"
            if paragraphs:
                paragraphs[-1] = f"{paragraphs[-1]}\n{tagged}"
            else:
                pending.append(tagged)
    if pending:
        paragraphs.append("\n".join(pending))
    return paragraphs


def _walk_divs(
    div: etree._Element, parent_headings: list[str], output: list[ParsedSection]
) -> None:
    heading = _text(div.find("./tei:head", TEI_NS))
    headings = parent_headings + ([heading] if heading else [])
    section_path = " > ".join(headings) or "Untitled"
    paragraphs = _direct_content(div)
    if paragraphs:
        output.append(
            ParsedSection(
                section_path=section_path,
                section_type=classify_section(heading or section_path),
                paragraphs=paragraphs,
            )
        )
    for child_div in div.findall("./tei:div", TEI_NS):
        _walk_divs(child_div, headings, output)


def parse_tei(xml: bytes | str) -> ParsedPaper:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=False)
    try:
        root = etree.fromstring(xml.encode() if isinstance(xml, str) else xml, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise ValueError(f"无效的 TEI XML: {exc}") from exc

    sections: list[ParsedSection] = []
    abstract = root.find(".//tei:teiHeader//tei:profileDesc//tei:abstract", TEI_NS)
    abstract_text = _text(abstract)
    if abstract_text:
        sections.append(ParsedSection("Abstract", "abstract", [abstract_text]))
    for div in root.xpath(".//tei:text/tei:body/tei:div", namespaces=TEI_NS):
        if div.getparent() is not None and etree.QName(div.getparent()).localname == "body":
            _walk_divs(div, [], sections)
    return ParsedPaper(metadata=_extract_metadata(root), sections=sections)
