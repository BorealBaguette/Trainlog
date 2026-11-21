"""The November 2026 security fixes, each with the legitimate use it must keep."""

import unittest

from tests.harness import (
    HOST,
    OWNER,
    app,
    are_friends,
    befriend,
    login,
    make_trip,
    make_user,
    uid,
)
from src.users import User

SAME_ORIGIN = f"https://{HOST}"


class RouterStatus(unittest.TestCase):
    def test_arbitrary_url_not_fetched(self):
        response = app.test_client().get(
            "/router_status/single", query_string={"url": "http://127.0.0.1:1/x?"}
        )
        self.assertEqual(response.json["status"], "ERROR")

    def test_known_router_still_checked(self):
        response = app.test_client().get(
            "/router_status/single",
            query_string={"url": "https://routing.openstreetmap.de/routed-car"},
        )
        # Offline in tests, so down, but it was tried
        self.assertEqual(response.json["status"], "DOWN")


class FlagSvg(unittest.TestCase):
    def test_size_applied(self):
        body = app.test_client().get("/flags/fr.svg?w=36&h=24").get_data(as_text=True)
        self.assertIn('width="36" height="24"', body)

    def test_markup_not_reflected(self):
        response = app.test_client().get(
            "/flags/fr.svg", query_string={"w": '"><script>alert(1)</script>', "h": "24"}
        )
        self.assertNotIn("<script", response.get_data(as_text=True))


class ResizeImage(unittest.TestCase):
    def test_used_size(self):
        response = app.test_client().get(
            "/resize_image/240/40?image_path=images/logo_square.png"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "image/png")

    def test_other_size_refused(self):
        response = app.test_client().get(
            "/resize_image/3000/3000?image_path=images/logo_square.png"
        )
        self.assertEqual(response.status_code, 404)


class TripVideoJson(unittest.TestCase):
    def setUp(self):
        self.viewer = make_user()
        self.sharer = make_user(share_level=1)
        self.hidden = make_user(share_level=0)
        self.own = make_trip(self.viewer)

    def video(self, ids):
        return login(self.viewer).post(
            "/public/trips/3d.json", json={"ids": ",".join(map(str, ids))}
        ).status_code

    def test_own_trip(self):
        self.assertEqual(self.video([self.own]), 200)

    def test_with_shared_trip(self):
        self.assertEqual(self.video([self.own, make_trip(self.sharer)]), 200)

    def test_with_trip_of_account_not_sharing(self):
        self.assertEqual(self.video([self.own, make_trip(self.hidden)]), 401)

    def test_with_friends_trip_of_a_stranger(self):
        trip = make_trip(self.sharer, visibility="friends")
        self.assertEqual(self.video([self.own, trip]), 401)
        befriend(self.viewer, self.sharer)
        self.assertEqual(self.video([self.own, trip]), 200)


class RoutingTripData(unittest.TestCase):
    def setUp(self):
        self.user = make_user()
        self.client = login(self.user)

    def test_trip_data_is_data(self):
        response = self.client.post(
            f"/u/{self.user}/routing", data={"trip_data": '{"name": "</script><b>x"}'}
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("</script><b>", response.get_data(as_text=True))

    def test_code_refused(self):
        for path in [f"/u/{self.user}/routing", f"/u/{self.user}/air_routing/air"]:
            with self.subTest(path=path):
                response = self.client.post(path, data={"trip_data": "1;alert(1)//"})
                self.assertEqual(response.status_code, 400)

    def test_get_without_data(self):
        self.assertEqual(self.client.get(f"/u/{self.user}/routing").status_code, 200)


class SessionCookie(unittest.TestCase):
    def test_flags(self):
        name = make_user()
        response = app.test_client().post(
            "/login/?raw=1", data={"username": name, "password": "test-password"}
        )
        cookie = next(h for h in response.headers.getlist("Set-Cookie") if h.startswith("session="))
        self.assertIn("SameSite=Lax", cookie)
        self.assertIn("Secure", cookie)


class CrossSiteWrites(unittest.TestCase):
    def setUp(self):
        self.user = make_user()
        self.client = login(self.user)
        self.trip = make_trip(self.user)

    def delete(self, **headers):
        return self.client.post(
            f"/u/{self.user}/deleteTrip", data={"tripId": str(self.trip)}, headers=headers
        ).status_code

    def exists(self):
        return self.client.get(f"/gpx/{self.trip}").status_code == 200

    def test_other_site_refused(self):
        self.assertEqual(self.delete(Origin="https://evil.example"), 403)
        self.assertTrue(self.exists())

    def test_subdomain_refused(self):
        self.assertEqual(self.delete(Origin="https://dev.trainlog.me"), 403)
        self.assertTrue(self.exists())

    def test_same_origin(self):
        self.assertEqual(self.delete(Origin=SAME_ORIGIN), 200)
        self.assertFalse(self.exists())

    def test_app_without_origin(self):
        self.assertEqual(self.delete(), 200)
        self.assertFalse(self.exists())

    def test_app_webview_null_origin(self):
        self.assertEqual(self.delete(Origin="null"), 200)

    def test_no_session_not_checked(self):
        # Token endpoints and webhooks: no cookie, so the token decides
        response = app.test_client().post(
            "/api/gps/upload", headers={"Origin": "https://evil.example"}
        )
        self.assertEqual(response.status_code, 401)


class LoginRedirect(unittest.TestCase):
    def test_next_to_other_site_ignored(self):
        name = make_user()
        for target in ["//evil.example/x", "/\\evil.example/x", "https://evil.example/x"]:
            with self.subTest(next=target):
                response = app.test_client().post(
                    "/login/", data={"username": name, "password": "test-password", "next": target}
                )
                self.assertEqual(response.status_code, 302)
                self.assertNotIn("evil.example", response.location)


class NoStateChangeOnGet(unittest.TestCase):
    def test_get_refused(self):
        user = make_user()
        other = uid(make_user())
        cases = [
            (OWNER, f"/toggle_role/{other}/alpha/make"),
            (OWNER, "/admin/add_dummy_path/1"),
            (user, f"/u/{user}/requestFriend/{other}"),
            (user, f"/u/{user}/acceptFriendship/{other}"),
            (user, f"/u/{user}/cancelFriendship/{other}"),
            (user, f"/u/{user}/delete_ticket/1"),
            (user, f"/u/{user}/toggle_ticket_active/1"),
            (user, f"/u/{user}/logout"),
            (user, f"/u/{user}/freehandify/1"),
            (user, f"/u/{user}/freehandify/revert/1"),
            (OWNER, "/admin/freehandify/1"),
            (OWNER, "/admin/freehandify/revert/1"),
        ]
        for who, path in cases:
            with self.subTest(path=path):
                # 404: before_request answers a method mismatch that way
                self.assertIn(login(who).get(path).status_code, {404, 405})
        with app.app_context():
            self.assertFalse(User.query.get(other).alpha)


class PostActionsStillWork(unittest.TestCase):
    def test_friendship_flow(self):
        alice, bob = make_user(), make_user()
        a, b = login(alice), login(bob)
        self.assertEqual(a.post(f"/u/{alice}/requestFriend/{uid(bob)}").status_code, 302)
        self.assertEqual(b.post(f"/u/{bob}/acceptFriendship/{uid(alice)}").status_code, 302)
        self.assertTrue(are_friends(alice, bob))
        self.assertEqual(a.post(f"/u/{alice}/cancelFriendship/{uid(bob)}").status_code, 302)
        self.assertFalse(are_friends(alice, bob))

    def test_toggle_role(self):
        target = uid(make_user())
        self.assertEqual(login(make_user()).post(f"/toggle_role/{target}/alpha/make").status_code, 401)
        response = login(OWNER).post(f"/toggle_role/{target}/alpha/make")
        self.assertEqual(response.json, {"success": True})

    def test_ticket_toggle(self):
        user = make_user()
        self.assertEqual(login(user).post(f"/u/{user}/toggle_ticket_active/1").status_code, 200)

    def test_logout(self):
        user = make_user()
        client = login(user)
        self.assertEqual(client.post(f"/u/{user}/logout").status_code, 302)
        response = client.get(f"/u/{user}")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.location)


if __name__ == "__main__":
    unittest.main()
