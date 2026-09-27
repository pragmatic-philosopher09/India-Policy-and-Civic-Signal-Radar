from datetime import date

from radar.parse import Item, parse_month
from radar.score import classify_action, tag_topics

SAMPLE = """
<html><body><div class="view-content">
<p><strong><span style="color:#3366ff">Highlights of this Issue</span></strong></p>
<p><strong><span style="color:#3366ff">Recap item that must be skipped</span></strong></p>
<div style="border-bottom:solid #3366ff 1.0pt"><p><strong>Finance</strong></p></div>
<p><strong><span style="color:#3366ff">Parliament passes the Income Tax Bill, 2026</span></strong></p>
<p><em>Someone (someone@prsindia.org)</em></p>
<p>The Income Tax Bill, 2026 was passed.<a href="#_edn1">[1]</a> It replaces the 1961 Act.
<a href="/billtrack/income-tax-bill-2026">here</a></p>
<ul><li><p>Slabs unchanged.</p></li></ul>
<div style="border-bottom:solid #3366ff 1.0pt"><p><strong>Education</strong></p></div>
<p><strong><span style="color:#3366ff">Draft rules on public examinations released for comments</span></strong></p>
<p>The Ministry released draft rules to curb paper leaks in NEET and other exams.</p>
<p><a name="_edn1"></a>[1] Endnote</p>
</div></body></html>
"""


def test_parse_skips_highlights_and_splits_sectors():
    items = parse_month(SAMPLE, date(2026, 8, 1), "u")
    assert [i.title for i in items] == [
        "Parliament passes the Income Tax Bill, 2026",
        "Draft rules on public examinations released for comments",
    ]
    assert items[0].sector == "Finance" and items[1].sector == "Education"
    assert "[1]" not in items[0].body
    assert "Slabs unchanged." in items[0].body
    assert items[0].links == ["https://prsindia.org/billtrack/income-tax-bill-2026"]
    assert items[0].month == "2026-08"


def test_classify_action_priority():
    assert classify_action("Parliament passes the Income Tax Bill") == "enacted"
    assert classify_action("Draft rules on public examinations released for comments") == "consultation"
    assert classify_action("Standing Committee submits report on gig workers") == "committee"
    assert classify_action("Cabinet approves new scheme") == "cabinet"
    assert classify_action("GDP grows 7.8%") == "other"


def test_tag_topics():
    it = Item("2026-08", "Education", "Draft rules on public examinations released",
              "Rules to curb paper leaks in NEET.")
    tags = tag_topics(it)
    assert "education-and-exams" in tags
    assert "gig-work-and-labour-codes" not in tags


def test_extractive_summary_respects_abbreviations():
    from radar.summarize import extractive
    body = ("The Committee (Chair: Dr. A. Sharma) presented its report on Cyber Crimes. "
            "It recommended Rs. 500 crore. Third sentence.")
    out = extractive("t", body)
    assert out.startswith("The Committee (Chair: Dr. A. Sharma) presented its report on Cyber Crimes.")
    assert not out.endswith("Dr.")


def test_parse_deadline_variants():
    from datetime import date
    from radar.build_site import parse_deadline, _deadline
    assert parse_deadline("September 4, 2026") == date(2026, 9, 4)
    assert parse_deadline("4 September 2026") == date(2026, 9, 4)
    assert parse_deadline("4th September, 2026") == date(2026, 9, 4)
    assert parse_deadline(None) is None
    assert _deadline("Comments are invited till August 7, 2026.") == "August 7, 2026"
    assert _deadline("The draft was released.") is None


def test_crosscheck_query_and_relevance():
    from radar.crosscheck import build_query, classify, _relevant
    q, tok = build_query("Parliament passed the Public Examinations (Prevention of Unfair Means) Amendment Bill, 2026")
    assert q.startswith('"') and "Unfair Means" in q
    assert {"public", "examinations", "unfair", "means"} <= tok
    assert _relevant(tok, "Lok Sabha passes anti-paper-leak Public Examinations Bill")
    assert not _relevant(tok, "Formula 2 rules and regulations updated for 2026")
    assert classify("https://pib.gov.in/PressReleasePage.aspx?PRID=1")[0] == "government"
    assert classify("https://www.thehindu.com/news/x.ece") == ("news", "The Hindu")
    assert classify("https://prsindia.org/billtrack/x")[0] == "exclude"
    assert classify("https://someblog.example.com/post")[0] == "other"


def test_digest_composes_without_network():
    from datetime import date
    from radar.config import TOPICS
    from radar.notify import compose_digest, compose_pings
    from radar.score import TopicScore, Evidence
    ev = Evidence("u1", "2026-08", "Finance", "RBI maintains repo rate", "other", 1.0, None, [], "https://prsindia.org/x",
                  hook="Repo on hold — your EMI isn't moving.")
    ev.corroborations = [{"kind": "government", "outlet": "PIB", "url": "https://pib.gov.in", "title": "", "published": None}]
    ts = TopicScore(TOPICS[0], ["2026-07", "2026-08"], [0, 1.0], [0, 1], 0.5, 0.2, 150, 1, 50, "heating", [ev],
                    confidence="low", why=[ev])
    cons = {"open": [dict(uid="c1", title="Draft X", hook="Should X change?", deadline=date(2026, 10, 9), days_left=5,
                          links=["https://example.gov.in/draft.pdf"], source_url="https://prsindia.org/y",
                          route=dict(body="RBI", url="", how="rbi"))], "unknown": [], "closed": []}
    text = compose_digest([ts], cons, date(2026, 9, 28))
    assert "Policy Pulse" in text and "Should X change?" in text and "Repo on hold" in text and "✓" in text
    pings = compose_pings(cons)
    assert len(pings) == 1 and pings[0][0] == "ping:c1:7d" and "5 days left" in pings[0][1]


def test_announcements_parse():
    from datetime import date
    from radar.announcements import parse
    html = """<div class="view-content"><table><thead><tr><th>Comments invited on</th><th>Deadline for submission</th>
    <th>Press Release</th><th>PRS Analysis</th></tr></thead><tbody>
    <tr><td><a href="/files/x.pdf">The Indian Statistical Institute Bill, 2026</a></td><td>Sep 28,2026</td>
    <td><a href="https://sansad.in/pr">Press Release</a></td><td></td></tr>
    <tr><td>Draft SHANTI Rules</td><td>Sep 04,2026</td><td><a href="https://dae.gov.in/x">Press Release</a></td><td><a href="">   </a></td></tr>
    </tbody></table></div>"""
    rows = parse(html)
    assert rows[0]["title"] == "The Indian Statistical Institute Bill, 2026"
    assert rows[0]["deadline"] == date(2026, 9, 28)
    assert rows[0]["draft_url"] == "https://prsindia.org/files/x.pdf" and rows[0]["press_url"] == "https://sansad.in/pr"
    assert rows[1]["analysis_url"] is None


def test_notice_headline_deadline_and_likely_closed():
    from datetime import date
    from radar.notice import deadline_from_headlines, apply
    hits = [{"kind": "news", "outlet": "SCC Online", "title": "BCI Releases Draft Advocates (Amendment) Bill, 2026; Invites Suggestions Till 31 July"}]
    d, src = deadline_from_headlines(hits, "2026-07")
    assert d == date(2026, 7, 31) and src == "SCC Online"
    e = dict(uid="x", month="2026-07", deadline=None, corroborations=[], route=dict(body="", url="", how="generic"))
    apply(e, date(2026, 9, 27))
    assert e["likely_closed"] is True and e["deadline"] is None


def test_state_bills_parse():
    from radar.states import parse_list
    html = """<div class="view-content">
    <div class="views-row"><div class="views-field views-field-title-field"><span><h3 class="file">
    <a href="/files/bills_acts/bills_states/karnataka/2025/Bill1of2025KA.pdf">The Karnataka Platform Based Gig Workers (Social Security and Welfare) Bill, 2025</a>
    </h3></span></div><div class="views-field views-field-field-bill-status"><span class="status-pending">Karnataka</span></div></div>
    </div>"""
    rows = parse_list(html)
    assert rows[0]["state"] == "Karnataka" and rows[0]["year"] == 2025
    assert rows[0]["url"].startswith("https://prsindia.org/files/") and rows[0]["title"].startswith("The Karnataka Platform")
