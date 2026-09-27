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
