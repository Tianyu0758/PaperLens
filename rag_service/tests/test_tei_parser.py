# 文件说明：验证 TEI 中的元数据、章节、正文和附属说明能否正确提取。
# 覆盖章节类型判断及标题回退等边界，使用本地 XML 内容完成测试。

from app.tei_parser import classify_section, parse_tei


TEI = b"""<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc>
      <titleStmt><title>Reliable RAG Systems</title></titleStmt>
      <publicationStmt><date when="2026-03-04"/></publicationStmt>
      <sourceDesc>
        <biblStruct>
          <analytic><author><persName><forename>Ada</forename><surname>Lovelace</surname></persName></author></analytic>
          <idno type="DOI">10.1000/rag</idno>
        </biblStruct>
      </sourceDesc>
    </fileDesc>
    <profileDesc><abstract><p>An abstract.</p></abstract></profileDesc>
  </teiHeader>
  <text><body>
    <div><head>1 Introduction</head><p>First paragraph.</p>
      <figure><head>Figure 1</head><figDesc>System overview.</figDesc></figure>
      <div><head>1.1 Motivation</head><p>Nested paragraph.</p></div>
    </div>
    <div><head>Results and Discussion</head><p>Strong results.</p><formula>x = y</formula></div>
  </body></text>
</TEI>
"""


def test_parse_tei_metadata_sections_and_attachments() -> None:
    parsed = parse_tei(TEI)

    assert parsed.metadata.title == "Reliable RAG Systems"
    assert parsed.metadata.authors == ["Ada Lovelace"]
    assert parsed.metadata.year == 2026
    assert parsed.metadata.doi == "10.1000/rag"
    assert [section.section_path for section in parsed.sections] == [
        "Abstract",
        "1 Introduction",
        "1 Introduction > 1.1 Motivation",
        "Results and Discussion",
    ]
    assert "[FIGURE] Figure 1 System overview." in parsed.sections[1].paragraphs[0]
    assert "[FORMULA] x = y" in parsed.sections[3].paragraphs[0]


def test_section_priority_prefers_results_over_discussion() -> None:
    assert classify_section("Results and Discussion") == "results"


def test_title_falls_back_to_source_description() -> None:
    xml = b"""<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader><fileDesc>
    <titleStmt/><publicationStmt/><sourceDesc><biblStruct><analytic>
    <title type="main">Fallback title</title></analytic></biblStruct></sourceDesc>
    </fileDesc></teiHeader><text><body/></text></TEI>"""
    assert parse_tei(xml).metadata.title == "Fallback title"
