"""Photos, offline: licence filter, candidate search with a stubbed Commons, download + crop, PHOTO_TEXT rules."""
import io

import pytest
from PIL import Image

from deckgen.research import photos
from deckgen.schemas import SlideSpec
from deckgen.schemas.research import Brief, Outline, OutlineSlide


def page(title, lic, width=2000, mime="image/jpeg", restrictions=""):
    md = {"LicenseShortName": {"value": lic}, "Artist": {"value": '<a href="x">Jane Doe</a>'}, "ImageDescription": {"value": "<b>A car</b> on a road"}}
    if restrictions:
        md["Restrictions"] = {"value": restrictions}
    return {"title": title, "index": 1, "imageinfo": [{"mime": mime, "width": width, "thumburl": "http://t/x.jpg", "url": "http://t/full.jpg",
                                                       "descriptionurl": "http://c/page", "extmetadata": md}]}


def test_only_openly_licensed_large_photos_are_candidates():
    ok = photos.to_candidate(page("File:Audi_Quattro.jpg", "CC BY-SA 2.0"), "p01", 3)
    assert ok and ok.title == "Audi Quattro" and ok.author == "Jane Doe" and ok.desc == "A car on a road"
    assert photos.credit(ok) == "Photo: Jane Doe, CC BY-SA 2.0, Wikimedia Commons"
    for bad in (page("File:a.jpg", "CC BY-NC 2.0"), page("File:a.jpg", "CC BY-NC-SA 4.0"), page("File:a.jpg", "Fair use"),
                page("File:a.jpg", "CC BY 2.0", width=300), page("File:a.svg", "CC BY 2.0", mime="image/svg+xml"),
                page("File:a.jpg", "CC BY 2.0", restrictions="personality")):
        assert photos.to_candidate(bad, "p02", 3) is None
    assert photos.to_candidate(page("File:b.jpg", "Public domain"), "p03", 3)


def test_fetch_crops_to_the_frame_aspect_and_catalog_uses_each_photo_once(tmp_path):
    buf = io.BytesIO()
    Image.new("RGB", (2000, 2000), "red").save(buf, "JPEG")

    class R:
        content = buf.getvalue()

        def raise_for_status(self):
            pass

    c = photos.to_candidate(page("File:Car.jpg", "CC0"), "p01", 2)
    cat = photos.Catalog({2: [c]}, tmp_path / "photos")
    got = cat.take("p01", 2, get=lambda *a, **k: R())
    w, h = Image.open(got[0].path).size
    assert got and w / h == pytest.approx(7.6 / 4.85, rel=0.01)
    assert cat.take("p01", 2) is None and cat.offered(2) == []
    assert photos.Catalog.from_json(cat.to_json(), tmp_path).cands[2][0].id == "p01"


def test_find_for_outline_dedupes_and_numbers_candidates(monkeypatch):
    class FakeLLM:
        def generate(self, **kw):
            return photos._Queries(slides=[photos._Q(n=2, queries=["audi quattro"]), photos._Q(n=3, queries=["audi quattro", "lancia 037"])])

    monkeypatch.setattr(photos, "search_commons", lambda q, **k: [page(f"File:{q}.jpg", "CC BY 2.0"), page("File:same.jpg", "CC BY 2.0")])
    brief = Brief.model_construct(topic="t")
    o = Outline.model_construct(slides=[OutlineSlide.model_construct(n=2, layout_hint="TITLE_ONLY", key_message="a", section="s"),
                                        OutlineSlide.model_construct(n=3, layout_hint="TITLE_ONLY", key_message="b", section="s")])
    got = photos.find_for_outline(FakeLLM(), brief, o)
    ids = [c.id for cs in got.values() for c in cs]
    assert ids == sorted(set(ids)) and len(got[2]) == 2 and {c.title for c in got[3]} == {"lancia 037"}


def test_photo_text_requires_a_photo():
    from deckgen.spec.rules import check_slide_v

    s = SlideSpec(id="s02", section="x", layout="PHOTO_TEXT", title="Car", placeholders={"body": ["one"]}, speaker_notes="n")
    assert any(v.check == "layout_needs_content" for v in check_slide_v(s, {}, []))
