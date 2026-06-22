from tests.stubs import StubEmbeddingProvider

from paw.ingest.chunking import build_chunks, cosine, split_sections, split_sentences
from paw.providers.config import WikiConfig


def test_split_sections_intro_and_headings():
    md = "intro text\n\n## A\n\nalpha body\n\n## B\n\nbeta body"
    secs = split_sections(md)
    assert secs[0][0] is None and "intro text" in secs[0][1]
    assert secs[1][0] == "A"
    assert secs[2][0] == "B"


def test_split_sentences():
    s = split_sentences("First sentence. Second one! Third?")
    assert len(s) == 3


def test_cosine_identity():
    assert abs(cosine([1.0, 0.0], [1.0, 0.0]) - 1.0) < 1e-9
    assert abs(cosine([1.0, 0.0], [0.0, 1.0])) < 1e-9


async def test_build_chunks_has_summary_first_and_overlap():
    emb = StubEmbeddingProvider(dim=8)
    md = "## A\n\n" + " ".join(f"Sentence number {i}." for i in range(20))
    chunks = await build_chunks(
        summary="the summary",
        markdown=md,
        embedder=emb,
        cfg=WikiConfig(chunk_target_size=120, chunk_overlap_sentences=1),
    )
    assert chunks[0].kind == "summary" and chunks[0].ord == 0
    assert chunks[0].text == "the summary"
    section_chunks = [c for c in chunks if c.kind == "section"]
    assert len(section_chunks) >= 2  # 20 sentences over target_size=120 must split
    # ords are unique and contiguous
    assert [c.ord for c in chunks] == list(range(len(chunks)))
    # overlap: a sentence from the end of chunk k reappears at start of chunk k+1
    first, second = section_chunks[0].text, section_chunks[1].text
    assert first.split(". ")[-1].strip(". ") in second
