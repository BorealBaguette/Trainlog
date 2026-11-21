"""Who can reach what: logins, user pages, trips across accounts."""

import unittest

from tests.harness import OWNER, PASSWORD, app, befriend, login, make_trip, make_user


class Pages(unittest.TestCase):
    def test_public_pages_render(self):
        client = app.test_client()
        for path in ["/", "/login/", "/signup/", "/about", "/privacy", "/robots.txt",
                     "/password_reset_request", "/leaderboard", "/news", "/feature_requests"]:
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 200)

    def test_unknown_host_refused(self):
        response = app.test_client().get("/about", base_url="https://evil.example")
        self.assertEqual(response.status_code, 406)


class Login(unittest.TestCase):
    def test_wrong_password(self):
        name = make_user()
        response = app.test_client().post(
            "/login/?raw=1", data={"username": name, "password": "nope"}
        )
        self.assertEqual(response.status_code, 401)

    def test_login_by_email(self):
        name = make_user()
        response = app.test_client().post(
            "/login/?raw=1", data={"username": f"{name}@example.com", "password": PASSWORD}
        )
        self.assertEqual(response.status_code, 200)

    def test_next_stays_on_site(self):
        name = make_user()
        response = app.test_client().post(
            "/login/", data={"username": name, "password": PASSWORD, "next": f"/u/{name}/trips"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith(f"/u/{name}/trips"))


class UserPages(unittest.TestCase):
    def setUp(self):
        self.alice = make_user()
        self.bob = make_user()

    def test_anonymous_sent_to_login(self):
        response = app.test_client().get(f"/u/{self.alice}")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.location)

    def test_own_map(self):
        self.assertEqual(login(self.alice).get(f"/u/{self.alice}").status_code, 200)

    def test_someone_elses_map(self):
        self.assertEqual(login(self.bob).get(f"/u/{self.alice}").status_code, 401)

    def test_site_owner_sees_any_map(self):
        self.assertEqual(login(OWNER).get(f"/u/{self.alice}").status_code, 200)

    def test_unknown_user_404(self):
        self.assertEqual(login(self.alice).get("/u/nobody-at-all").status_code, 404)

    def test_shortcut_redirects_to_own_page(self):
        response = login(self.alice).get("/trips")
        self.assertEqual(response.status_code, 307)
        self.assertTrue(response.location.endswith(f"/u/{self.alice}/trips"))

    def test_owner_only_pages(self):
        for path in ["/admin/health/", "/admin/denied_logins", "/admin/suspicious"]:
            with self.subTest(path=path):
                self.assertEqual(login(self.alice).get(path).status_code, 401)

    def test_edit_someone_elses_trip(self):
        trip = make_trip(self.alice)
        response = login(self.bob).get(f"/u/{self.bob}/edit/{trip}")
        self.assertEqual(response.status_code, 401)


class TripSharing(unittest.TestCase):
    """The per-trip rules: the account shares its trips, the trip is not private,
    friends-only trips need friendship. Checked on GPX downloads and getPublicTrips."""

    def setUp(self):
        self.sharer = make_user(share_level=1)
        self.hidden = make_user(share_level=0)
        self.friend = make_user()
        self.stranger = make_user()
        befriend(self.sharer, self.friend)

    def gpx(self, client, trip):
        return client.get(f"/gpx/{trip}").status_code

    def public_trips(self, client, trip):
        return client.post("/getPublicTrips", json={"tripIds": str(trip)}).status_code

    def test_public_trip_of_sharing_account(self):
        trip = make_trip(self.sharer)
        self.assertEqual(self.gpx(app.test_client(), trip), 200)
        self.assertEqual(self.public_trips(app.test_client(), trip), 200)

    def test_trip_of_account_not_sharing(self):
        trip = make_trip(self.hidden)
        self.assertEqual(self.gpx(login(self.stranger), trip), 401)
        self.assertEqual(self.public_trips(login(self.stranger), trip), 401)
        self.assertEqual(self.gpx(login(self.hidden), trip), 200)
        self.assertEqual(self.gpx(login(OWNER), trip), 200)

    def test_private_trip(self):
        trip = make_trip(self.sharer, visibility="private")
        self.assertEqual(self.gpx(login(self.friend), trip), 401)
        self.assertEqual(self.public_trips(login(self.friend), trip), 401)
        self.assertEqual(self.gpx(login(self.sharer), trip), 200)

    def test_friends_trip(self):
        trip = make_trip(self.sharer, visibility="friends")
        self.assertEqual(self.gpx(login(self.friend), trip), 200)
        self.assertEqual(self.gpx(login(self.stranger), trip), 401)
        self.assertEqual(self.public_trips(login(self.stranger), trip), 401)


if __name__ == "__main__":
    unittest.main()
