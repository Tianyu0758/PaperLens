# 文件说明：验证短章节保留、长章节切分、重叠长度与稳定文本块标识。
# 使用可控的字符计数器代替实际 tokenizer，使切块边界容易核对。

from app.chunking import build_chunks
from app.tei_parser import ParsedSection


class CharacterCodec:
    def encode(self, text: str) -> list[int]:
        return [ord(character) for character in text]

    def decode(self, token_ids: list[int]) -> str:
        return "".join(chr(token_id) for token_id in token_ids)


def test_short_section_is_preserved_and_enriched() -> None:
    sections = [ParsedSection("Methods > Data", "methods", ["Small paragraph."])]
    chunks = build_chunks("paper-1", sections, CharacterCodec(), target_tokens=80, overlap_tokens=20)

    assert len(chunks) == 1
    assert chunks[0].text == "【Methods > Data】\nSmall paragraph."
    assert chunks[0].section_type == "methods"


def test_long_section_uses_overlap_without_exceeding_limit() -> None:
    text = "\n\n".join(f"Paragraph {index} has useful content." for index in range(12))
    sections = [ParsedSection("Results", "results", [text])]
    codec = CharacterCodec()

    chunks = build_chunks(
        "paper-1", sections, codec, target_tokens=100, overlap_tokens=20
    )

    assert len(chunks) > 1
    assert all(len(codec.encode(chunk.text)) <= 100 for chunk in chunks)
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert len({chunk.chunk_id for chunk in chunks}) == len(chunks)
    first_body = chunks[0].text.split("\n", 1)[1]
    second_body = chunks[1].text.split("\n", 1)[1]
    assert first_body[-20:].strip() in second_body


def test_chunk_ids_are_deterministic() -> None:
    sections = [ParsedSection("Introduction", "introduction", ["Stable content."])]
    first = build_chunks("paper-1", sections, CharacterCodec())
    second = build_chunks("paper-1", sections, CharacterCodec())
    assert first[0].chunk_id == second[0].chunk_id
