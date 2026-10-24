import pytest

from deckgen.schemas.draft import DraftConversionError, ItemDraft, SlideDraft, draft_to_slide
from deckgen.schemas.deck import SlideSpec


def base(**kw):
    return SlideDraft(id="s1", section="A", layout="TITLE_ONLY", title="A message title", **kw)


def test_cards_roundtrip():
    items = [ItemDraft(icon="FaCarSide", heading=f"H{i}", body="b") for i in range(3)]
    s = draft_to_slide(base(comp_kind="cards", items=items))
    assert isinstance(s, SlideSpec) and s.composition.kind == "cards" and len(s.composition.items) == 3


def test_stats_detail_empty_becomes_none():
    items = [ItemDraft(value="200", label="Cars"), ItemDraft(value="20", label="Evo", detail="x")]
    s = draft_to_slide(base(comp_kind="stats", items=items))
    assert s.composition.items[0].detail is None and s.composition.items[1].detail == "x"


def test_timeline_comparison_icon_rows_and_chart_table():
    ev = [ItemDraft(year=str(1980 + i), heading="h", body="b") for i in range(3)]
    assert draft_to_slide(base(comp_kind="timeline", items=ev)).composition.kind == "timeline"
    cols = [ItemDraft(heading="A", bullets=["x"]), ItemDraft(heading="B", bullets=["y"])]
    assert draft_to_slide(base(comp_kind="comparison", items=cols)).composition.kind == "comparison"
    rows = [ItemDraft(icon="FaHeart", heading="h", body="b") for _ in range(3)]
    assert draft_to_slide(base(comp_kind="icon_rows", items=rows)).composition.kind == "icon_rows"
    d = SlideDraft(id="c", section="A", layout="CHART_COMMENTARY", title="Chart title here", bullets=["a", "b"],
                   chart_type="bar", chart_title="T", categories=["x", "y"], series=[{"name": "s", "values": [1, 2]}])
    s = draft_to_slide(d)
    assert s.chart.type == "bar" and s.placeholders["body"] == ["a", "b"]
    t = SlideDraft(id="t", section="A", layout="TABLE", title="Table title here", table_header=["a", "b"],
                   table_rows=[{"cells": ["1", "2"]}])
    assert draft_to_slide(t).table.rows == [["1", "2"]]


def test_title_slide_subtitle_placeholder():
    s = draft_to_slide(SlideDraft(id="t", section="A", layout="TITLE", title="Deck", subtitle="Sub"))
    assert s.placeholders == {"subtitle": "Sub"} and s.composition is None


def test_invalid_conversion_reports_which_slide():
    with pytest.raises(DraftConversionError, match="s1"):
        draft_to_slide(base(comp_kind="cards", items=[ItemDraft(icon="FaCarSide", heading="h", body="b")]))  # needs >= 3
    with pytest.raises(DraftConversionError, match="s1"):
        draft_to_slide(base(comp_kind="cards", items=[ItemDraft(icon="bad icon", heading="h", body="b")] * 3))
