"""Status code of every GET route that needs no argument but a username, as an
anonymous visitor, as the user, and as the site owner looking at the user.

Writes them to $TRAINLOG_SNAPSHOT (JSON) when set, for scripts/compare_main.sh to
diff two versions; the test itself only fails on a 500.
"""

import json
import os
import unittest

from tests.harness import OWNER, app, login, make_trip, make_user

# Logging out mid-run would change every answer after it; the flag sprite would be
# rebuilt into the real static folder (a fresh checkout's SVGs look newer than it)
SKIP = {"static", "user_shortcut", "logout", "serve_flag_sprite"}

# Already failing on main, not what this suite guards: 500 when called without
# their query parameters, nothing returned on GET, or needing the network or a
# config section the test config leaves out (discord).
KNOWN_5XX = {
    "/getCountry", "/placeAutocomplete", "/stationAutocomplete",
    "/stations/explorer/search", "/u/<username>/scottySaveTrip",
    "/u/<username>/stations/tidy/data", "/admin/refreshCurrency", "/discord/connect",
}


def snapshot_rules():
    for rule in app.url_map.iter_rules():
        if rule.endpoint in SKIP or "GET" not in rule.methods:
            continue
        if rule.arguments <= {"username"}:
            yield rule


def take():
    user = make_user()
    make_trip(user)
    clients = {"anon": app.test_client(), "user": login(user), "owner": login(OWNER)}
    statuses = {}
    for rule in sorted(snapshot_rules(), key=lambda r: r.rule):
        path = rule.rule.replace("<username>", user)
        for role, client in clients.items():
            response = client.get(path)
            statuses[f"{role} {rule.rule}"] = response.status_code
            response.close()
    return statuses


class RouteSnapshot(unittest.TestCase):
    def test_no_server_errors(self):
        statuses = take()
        out = os.environ.get("TRAINLOG_SNAPSHOT")
        if out:
            with open(out, "w") as f:
                json.dump(statuses, f, indent=1, sort_keys=True)
        errors = sorted(
            k for k, v in statuses.items() if v >= 500 and k.split(" ", 1)[1] not in KNOWN_5XX
        )
        self.assertEqual(errors, [], "routes answering 5xx")


if __name__ == "__main__":
    unittest.main()
