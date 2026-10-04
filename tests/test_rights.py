from gnn.fetch import FixtureFetcher
from gnn.pipeline import corroboration, verify_claims

HTML = "<html><body><article><p>Emissions fell 12% in 2025, the agency said.</p></article></body></html>"


def test_tdm_reservation_header_marks_page_reserved():
    f = FixtureFetcher(pages={"https://news.example.com/a": HTML},
                       headers={"https://news.example.com/a": {"content-type": "text/html", "tdm-reservation": "1"}})
    page = f.get("https://news.example.com/a")
    assert page.tdm_reserved and "tdm-reservation header" in page.note


def test_noai_meta_marks_page_reserved():
    html = '<html><head><meta name="robots" content="index, noai"></head><body><p>Text here for testing.</p></body></html>'
    page = FixtureFetcher(pages={"https://x.example.com/": html}).get("https://x.example.com/")
    assert page.tdm_reserved


def test_extractor_drops_navigation_and_keeps_links():
    html = '<nav><p>Menu item</p></nav><article><p>Real text with a <a href="/doc">study</a>.</p></article>'
    page = FixtureFetcher(pages={"https://y.example.com/n": html}).get("https://y.example.com/n")
    assert "Menu item" not in page.text and "Real text" in page.text
    assert ("https://y.example.com/doc", "study") in page.links


def _docs():
    return [
        {"n": 1, "rights": "FACTS_ONLY", "tier": 2, "owner_org": "Other Outlet", "publisher": "Other Outlet",
         "text": "Emissions fell 12% in 2025. The minister was delighted."},
        {"n": 2, "rights": "PRIMARY_PUBLIC", "tier": 1, "owner_org": "Agency", "publisher": "Agency",
         "text": "Emissions fell 12% in 2025, according to provisional figures."},
    ]


def test_claims_only_in_another_outlets_report_are_dropped():
    claims = [
        {"id": "c1", "doc": 1, "span": "Emissions fell 12% in 2025."},       # confirmed by doc 2 wording? no: not verbatim
        {"id": "c2", "doc": 2, "span": "Emissions fell 12% in 2025"},         # primary: kept
        {"id": "c3", "doc": 1, "span": "The minister was delighted."},        # lead only: dropped
        {"id": "c4", "doc": 2, "span": "Emissions rose 12%"},                 # not verbatim: dropped
    ]
    kept, dropped = verify_claims(claims, _docs())
    assert [c["id"] for c in kept] == ["c2"]
    whys = {c["id"]: c["why"] for c in dropped}
    assert "primary confirmation" in whys["c3"]
    assert "verbatim" in whys["c4"]


def test_corroboration_prefers_primary():
    kept = [{"doc": 2}]
    assert corroboration(kept, _docs()) == "primary_confirmed"
    docs = [{"n": 1, "rights": "FACTS_ONLY", "tier": 2, "owner_org": "A", "publisher": "A"},
            {"n": 2, "rights": "LICENSED", "tier": 3, "owner_org": "B", "publisher": "B"}]
    assert corroboration([{"doc": 1}, {"doc": 2}], docs) == "two_independent"
    assert corroboration([{"doc": 2}], docs) == "single_source"
