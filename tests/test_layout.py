import re

from django.urls import reverse

from accounts.models import Factory
from tests.test_complaints import as_user, lodge  # noqa: F401  (fixtures and helpers)


def kpis(html):
    return [int(n) for n in re.findall(r'<div class="kpi[^"]*"><b>(\d+)</b>', html)]


def listed(html):
    return re.findall(r'<td><a href="[^"]*">([A-Z]{2}/\d{4}/\d{4})</a>', html)


def test_stat_tiles_count_what_the_user_can_see(as_user):
    sales = as_user("sales")
    lodge(sales, submit="1")  # submitted
    lodge(sales, save="1")  # a draft
    lodge(sales, submit="1", factory=Factory.objects.get(code="FB").pk)

    # seen, drafts, waiting for my action. A lodger has nothing to do once the complaint is submitted.
    assert kpis(sales.get(reverse("home")).content.decode()) == [3, 1, 0]
    # The Coordinator sees the two submitted complaints and not the draft, and both wait for them.
    assert kpis(as_user("mr").get(reverse("home")).content.decode()) == [2, 0, 2]
    assert kpis(as_user("fb.pic").get(reverse("home")).content.decode()) == [1, 0, 0]


def test_list_filters(as_user):
    sales = as_user("sales")
    lodge(sales, submit="1", buyer="Alpha Foods")
    lodge(sales, submit="1", buyer="Beta Trading", factory=Factory.objects.get(code="FB").pk)
    lodge(sales, save="1", buyer="Gamma Draft")
    fa, fb = Factory.objects.get(code="FA").pk, Factory.objects.get(code="FB").pk

    def refs(**params):
        return listed(sales.get(reverse("home"), params).content.decode())

    assert len(refs()) == 3
    assert [r[:2] for r in refs(factory=fb)] == ["FB"]
    assert [r[:2] for r in refs(factory=fa)] == ["FA", "FA"]
    assert len(refs(status="draft")) == 1
    assert len(refs(status="submitted")) == 2
    assert len(refs(q="beta")) == 1
    assert len(refs(q="SC-000123")) == 3  # contract number
    assert refs(q="no such text") == []
    assert refs(date_from="2999-01-01") == []
    assert len(refs(date_to="2999-01-01")) == 3
    assert len(refs(date_from="2000-01-01", status="submitted", factory=fa)) == 1


def test_an_empty_filtered_list_says_so(as_user):
    page = as_user("sales").get(reverse("home"), {"q": "nothing"}).content.decode()
    assert "No complaints match the current filters." in page
    assert "No complaints yet" not in page


def test_filters_survive_paging(as_user, monkeypatch):
    monkeypatch.setattr("complaints.views.PAGE_SIZE", 2)
    sales = as_user("sales")
    for _ in range(3):
        lodge(sales, submit="1")
    html = sales.get(reverse("home"), {"status": "submitted"}).content.decode()
    assert "status=submitted&amp;page=2" in html


def test_sidebar_marks_the_current_page(as_user):
    client = as_user("sales")
    cases = {"home": "My complaints", "complaint_new": "New complaint", "help": "Help"}
    for url_name, label in cases.items():
        html = client.get(reverse(url_name)).content.decode()
        active = re.findall(r'<a href="[^"]*" class="active" aria-current="page">([^<]+)</a>', html)
        assert active == [label], url_name
    assert "Signed in as" in html and "Role: Lodger" in html


def test_only_the_coordinator_gets_the_settings_link(as_user):
    assert "Settings</a>" in as_user("mr").get(reverse("home")).content.decode()
    assert "Settings</a>" not in as_user("fa.pic").get(reverse("home")).content.decode()


def test_the_sign_in_page_has_no_sidebar(client, seeded):
    html = client.get(reverse("login")).content.decode()
    assert "Sign in" in html and 'class="sidebar"' not in html


def test_help_page_highlights_the_users_role_guide(as_user):
    expected = {"sales": "Lodgers", "mr": "Coordinator (MR)", "fa.pic": "PICs and laboratory",
                "fa.lab": "PICs and laboratory", "fa.hod": "HOD and Factory Manager",
                "fa.fm": "HOD and Factory Manager", "management": "Top management"}  # fmt: skip
    for username, guide in expected.items():
        html = as_user(username).get(reverse("help")).content.decode()
        mine = re.findall(r'<div class="guide mine">\s*<h3>([^<]+?)\s*<span', html)
        assert mine == [guide], username
        assert html.count("<details>") >= 5


def test_help_requires_sign_in(client, seeded):
    assert client.get(reverse("help")).status_code == 302
