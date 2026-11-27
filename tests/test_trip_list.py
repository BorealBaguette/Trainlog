"""The trips table's data endpoint."""

import unittest
from datetime import UTC, datetime, timedelta

from tests.harness import login, make_trip, make_user


def make_trips(username, upcoming, past):
    now = datetime.now(UTC).replace(tzinfo=None, minute=0, second=0, microsecond=0)
    for i in range(upcoming):
        make_trip(username, start=now + timedelta(days=i + 1))
    for i in range(past):
        make_trip(username, start=now - timedelta(days=i + 1))


def fetch(username, start=0, length=6, anchor=True):
    form = {
        "draw": 1,
        "start": start,
        "length": length,
        "includeFuture": 1,
        "sort_field": "temporal",
        "sort_dir": "desc",
    }
    if anchor:
        form["anchorNow"] = 1
    response = login(username).post(
        f"/u/{username}/get_trips_api?projects=False", data=form
    )
    assert response.status_code == 200, response.data
    return response.get_json()


class AnchorNow(unittest.TestCase):
    def test_lands_on_page_grid(self):
        # 7 upcoming, 6 per page: the first past trip is row 7, on the second page
        name = make_user()
        make_trips(name, upcoming=7, past=5)
        anchored = fetch(name)
        self.assertEqual(anchored["start"], 6)
        # Paging away and back asks for that page by its offset
        self.assertEqual(anchored["data"], fetch(name, start=6, anchor=False)["data"])

    def test_all_upcoming_lands_on_last_page(self):
        name = make_user()
        make_trips(name, upcoming=6, past=0)
        anchored = fetch(name)
        self.assertEqual(anchored["start"], 0)
        self.assertEqual(len(anchored["data"]), 6)


if __name__ == "__main__":
    unittest.main()
