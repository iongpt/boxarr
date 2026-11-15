"""Films whose titles contain studio-like words keep their row (issue #143).

The parser used to drop every row whose title contained "Pictures",
"Studios", "Films", "Entertainment", "Releasing", "Distribution",
"Productions" or "Company" (even inside a word), and every film below it moved
up a rank. Titles only ever come from the chart's Release column, whose links
point to /release/ movie pages, so the filter could only remove real films:
the Oscar-nominated shorts package charts this way every February.
"""

from src.core.boxoffice import BoxOfficeService

# (BOM rank, title, release id, weekend gross, distributor)
_ROWS = [
    (1, "Wicked: For Good", "rl101", "$147,000,000", "Universal Pictures"),
    (2, "The Company You Keep", "rl102", "$15,500,000", "Sony Pictures Classics"),
    (3, "Zootopia 2", "rl103", "$9,250,000", "Walt Disney Studios"),
    (
        4,
        "2025 Oscar Nominated Short Films: Live Action",
        "rl104",
        "$1,200,000",
        "ShortsTV",
    ),
    (5, "Moving Pictures", "rl105", "$900,000", "Neon"),
    (6, "Marvel Studios' Thunderbolts*", "rl106", "$850,000", "Walt Disney Studios"),
    (7, "Accompany Me", "rl107", "$400,000", "A24"),
]


def _chart_html(rows=_ROWS) -> str:
    """A weekend chart in the live 13-column Box Office Mojo layout."""
    body = "".join(f"""
        <tr>
            <td class="mojo-field-type-rank">{rank}</td>
            <td>-</td>
            <td class="mojo-field-type-release">
                <a class="a-link-normal" href="/release/{rid}/?ref_=bo_we_table_{rank}">{title}</a>
            </td>
            <td class="mojo-field-type-money">{gross}</td>
            <td>-</td>
            <td>1,000</td>
            <td>-</td>
            <td>$1,000</td>
            <td>{gross}</td>
            <td>1</td>
            <td class="mojo-field-type-release_studios">
                <a href="https://pro.imdb.com/company/co000{rank}/">{studio}</a>
            </td>
            <td class="hidden">false</td>
            <td class="hidden">false</td>
        </tr>""" for rank, title, rid, gross, studio in rows)
    return f"""
    <html><body>
        <table class="a-bordered">
            <tr>
                <th>Rank</th><th>LW</th><th>Release</th><th>Gross</th>
                <th>%&plusmn; LW</th><th>Theaters</th><th>Change</th>
                <th>Average</th><th>Total Gross</th><th>Weeks</th>
                <th>Distributor</th><th>New This Week</th><th>Estimated</th>
            </tr>
            {body}
        </table>
    </body></html>
    """


def _fallback_html(rows=_ROWS) -> str:
    """A page with no chart table, which the regex fallback parser reads."""
    links = "".join(
        f'<a href="/release/{rid}/?ref_=bo_we_table_{rank}">{title}</a>'
        f'<a href="https://pro.imdb.com/company/co000{rank}/">{studio}</a>'
        for rank, title, rid, _gross, studio in rows
    )
    return f"<html><body><div>{links}</div></body></html>"


def _money(text: str) -> float:
    return float(text.strip("$").replace(",", ""))


class TestTablePath:
    def setup_method(self):
        self.service = BoxOfficeService()

    def test_every_row_is_kept_with_its_chart_rank(self):
        movies = self.service.parse_box_office_html(_chart_html(), limit=30)

        assert [(m.rank, m.title) for m in movies] == [
            (rank, title) for rank, title, *_ in _ROWS
        ]

    def test_values_stay_on_their_own_row(self):
        movies = self.service.parse_box_office_html(_chart_html(), limit=30)

        assert [(m.title, m.weekend_gross, m.release_url) for m in movies] == [
            (title, _money(gross), f"/release/{rid}/?ref_=bo_we_table_{rank}")
            for rank, title, rid, gross, _studio in _ROWS
        ]

    def test_limit_cuts_at_the_chart_rank(self):
        """No film from beyond the limit is pulled in to replace a dropped one."""
        movies = self.service.parse_box_office_html(_chart_html(), limit=4)

        assert [m.title for m in movies] == [
            "Wicked: For Good",
            "The Company You Keep",
            "Zootopia 2",
            "2025 Oscar Nominated Short Films: Live Action",
        ]

    def test_distributor_names_never_become_titles(self):
        movies = self.service.parse_box_office_html(_chart_html(), limit=30)

        studios = {studio for *_, studio in _ROWS}
        assert not studios & {m.title for m in movies}


class TestRegexFallbackPath:
    def setup_method(self):
        self.service = BoxOfficeService()

    def test_every_release_link_is_kept_with_its_rank(self):
        movies = self.service.parse_box_office_html(_fallback_html(), limit=30)

        assert [(m.rank, m.title, m.release_url) for m in movies] == [
            (rank, title, f"/release/{rid}/") for rank, title, rid, *_ in _ROWS
        ]

    def test_limit_cuts_at_the_chart_rank(self):
        movies = self.service.parse_box_office_html(_fallback_html(), limit=2)

        assert [(m.rank, m.title) for m in movies] == [
            (1, "Wicked: For Good"),
            (2, "The Company You Keep"),
        ]
