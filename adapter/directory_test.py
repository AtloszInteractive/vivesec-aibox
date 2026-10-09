"""Directory lookups (E02): token claims and Microsoft Entra ID through a mocked
Microsoft Graph, including caching and the fail-closed error path."""
import io
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse

import directory
import identity

GRAPH = directory.DEFAULT_GRAPH_URL
LOGIN = directory.DEFAULT_AUTHORITY
SECRET = "client-secret-value"


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FakeGraph:
    """Answers Graph requests from a URL-prefix table and records the calls."""

    def __init__(self):
        self.calls = []
        self.routes = {}
        self.fail = None

    def __call__(self, request, timeout):
        url = request.full_url
        self.calls.append((request.get_method(), url, request.data, dict(request.header_items())))
        if self.fail is not None:
            raise self.fail
        for prefix, body in self.routes.items():
            if url.startswith(prefix):
                if isinstance(body, Exception):
                    raise body
                return FakeResponse(json.dumps(body).encode("utf-8"))
        raise urllib.error.HTTPError(url, 404, "not found", {}, None)

    def count(self, fragment):
        return sum(1 for call in self.calls if fragment in call[1])


def token_identity(**extra):
    return identity.Identity("lars", identity.SOURCE_TOKEN, upn="lars@example.com",
                             groups=["g1"], roles=["Reader"], **extra)


class SourceTest(unittest.TestCase):
    def test_parse_source(self):
        warnings = []
        self.assertEqual(directory.parse_source(""), directory.SOURCE_OFF)
        self.assertEqual(directory.parse_source("Entra"), directory.SOURCE_ENTRA)
        self.assertEqual(directory.parse_source("ldap", warnings.append), directory.SOURCE_OFF)
        self.assertTrue(warnings)

    def test_from_env_selects_the_backend(self):
        quiet = lambda _m: None  # noqa: E731
        self.assertIsInstance(directory.from_env({}, quiet), directory.NullDirectory)
        self.assertIsInstance(directory.from_env({"ADAPTER_DIRECTORY": "token"}, quiet),
                              directory.TokenDirectory)
        warnings = []
        entra = directory.from_env({"ADAPTER_DIRECTORY": "entra"}, warnings.append)
        self.assertIsInstance(entra, directory.EntraDirectory)
        self.assertTrue(warnings, "an incomplete Entra configuration must be reported")

    def test_off_returns_nothing(self):
        info = directory.NullDirectory().lookup(token_identity())
        self.assertEqual((info.groups, info.roles), ((), ()))

    def test_token_source_trusts_only_verified_tokens(self):
        source = directory.TokenDirectory()
        info = source.lookup(token_identity())
        self.assertEqual((info.groups, info.roles, info.error), (("g1",), ("Reader",), None))
        header_only = identity.Identity("lars", identity.SOURCE_HEADER)
        info = source.lookup(header_only)
        self.assertEqual(info.groups, ())
        self.assertIsNotNone(info.error)


class EntraTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.secret_file = os.path.join(self.temp.name, "entra.secret")
        with open(self.secret_file, "w", encoding="utf-8") as handle:
            handle.write(SECRET + "\n")
        self.now = [1000.0]
        self.graph = FakeGraph()
        self.graph.routes = {
            LOGIN + "/tenant-1/oauth2/v2.0/token": {"access_token": "AT", "expires_in": 3600},
            GRAPH + "/v1.0/users/lars@example.com/transitiveMemberOf": {
                "value": [{"id": "g1", "displayName": "Finance"}],
                "@odata.nextLink": GRAPH + "/v1.0/next-groups"},
            GRAPH + "/v1.0/next-groups": {"value": [{"id": "g2", "displayName": "Board"},
                                                     {"id": "g1", "displayName": "Finance"}]},
            GRAPH + "/v1.0/servicePrincipals/sp-1": {"appRoles": [
                {"id": "r1", "value": "AIBox.Admin"}, {"id": "r2", "value": "AIBox.User"}]},
            GRAPH + "/v1.0/users/lars@example.com/appRoleAssignments": {"value": [
                {"appRoleId": "r2", "resourceId": "sp-1"},
                {"appRoleId": "r1", "resourceId": "another-app"}]},
        }

    def entra(self, **overrides):
        options = dict(tenant_id="tenant-1", client_id="client-1",
                       secret_file=self.secret_file, resource_id="sp-1", ttl=900,
                       opener=self.graph, clock=lambda: self.now[0])
        options.update(overrides)
        return directory.EntraDirectory(**options)

    def test_groups_and_roles_are_resolved(self):
        info = self.entra().lookup(token_identity())
        self.assertIsNone(info.error)
        self.assertEqual(info.groups, ("g1", "g2"))
        self.assertEqual(info.group_names, {"g1": "Finance", "g2": "Board"})
        self.assertEqual(info.roles, ("AIBox.User",))

    def test_client_credentials_request(self):
        self.entra().lookup(token_identity())
        method, url, data, _headers = self.graph.calls[0]
        self.assertEqual(method, "POST")
        form = urllib.parse.parse_qs(data.decode("ascii"))
        self.assertEqual(form["grant_type"], ["client_credentials"])
        self.assertEqual(form["client_secret"], [SECRET])
        self.assertEqual(form["scope"], [GRAPH + "/.default"])
        graph_headers = self.graph.calls[1][3]
        self.assertEqual(graph_headers.get("Authorization"), "Bearer AT")

    def test_results_are_cached_until_the_ttl(self):
        entra = self.entra()
        entra.lookup(token_identity())
        calls = len(self.graph.calls)
        self.now[0] += 899
        entra.lookup(token_identity())
        self.assertEqual(len(self.graph.calls), calls)
        self.now[0] += 2
        entra.lookup(token_identity())
        self.assertGreater(self.graph.count("transitiveMemberOf"), 1)
        # The app token outlives both lookups and is not fetched again.
        self.assertEqual(self.graph.count("oauth2"), 1)

    def test_forget_drops_the_cached_entry(self):
        entra = self.entra()
        entra.lookup(token_identity())
        entra.forget("LARS@example.com")
        entra.lookup(token_identity())
        self.assertEqual(self.graph.count("transitiveMemberOf"), 2)

    def test_outage_yields_no_groups_and_is_retried_later(self):
        entra = self.entra()
        entra.lookup(token_identity())
        self.now[0] += 1000
        self.graph.fail = urllib.error.URLError("network down")
        info = entra.lookup(token_identity())
        self.assertEqual((info.groups, info.roles), ((), ()))
        self.assertIn("unreachable", info.error)
        calls = len(self.graph.calls)
        self.now[0] += 10
        entra.lookup(token_identity())
        self.assertEqual(len(self.graph.calls), calls, "failures are briefly cached")
        self.graph.fail = None
        self.now[0] += directory.NEGATIVE_TTL_SECONDS
        self.assertEqual(entra.lookup(token_identity()).groups, ("g1", "g2"))

    def test_http_errors_do_not_leak_the_secret(self):
        self.graph.routes[LOGIN + "/tenant-1/oauth2/v2.0/token"] = urllib.error.HTTPError(
            "x", 401, "unauthorized", {}, None)
        info = self.entra().lookup(token_identity())
        self.assertEqual(info.groups, ())
        self.assertNotIn(SECRET, info.error)
        self.assertIn("401", info.error)

    def test_paging_may_not_leave_graph(self):
        self.graph.routes[GRAPH + "/v1.0/users/lars@example.com/transitiveMemberOf"] = {
            "value": [], "@odata.nextLink": "https://evil.example/steal"}
        info = self.entra().lookup(token_identity())
        self.assertEqual(info.groups, ())
        self.assertIn("left the Graph endpoint", info.error)
        self.assertEqual(self.graph.count("evil.example"), 0)

    def test_without_resource_id_no_roles_are_reported(self):
        info = self.entra(resource_id=None).lookup(token_identity())
        self.assertEqual(info.roles, ())
        self.assertEqual(self.graph.count("appRoleAssignments"), 0)

    def test_directory_key(self):
        entra = self.entra()
        info = entra.lookup(identity.Identity("opaque-id", identity.SOURCE_HEADER))
        self.assertIn("no directory id", info.error)
        self.assertEqual(self.graph.calls, [])
        email_user = identity.Identity("lars@example.com", identity.SOURCE_HEADER)
        self.assertEqual(entra.lookup(email_user).groups, ("g1", "g2"))

    def test_user_id_is_url_encoded(self):
        self.entra().lookup(identity.Identity("x", identity.SOURCE_TOKEN,
                                              upn="a/b?c@example.com"))
        urls = [call[1] for call in self.graph.calls if "/users/" in call[1]]
        self.assertTrue(urls)
        self.assertTrue(all("/users/a%2Fb%3Fc@example.com/" in url for url in urls))

    def test_misconfiguration_is_reported_without_network(self):
        for overrides in ({"tenant_id": ""}, {"tenant_id": "../evil"},
                          {"client_id": ""}, {"secret_file": None}):
            info = self.entra(**overrides).lookup(token_identity())
            self.assertEqual(info.groups, ())
            self.assertIsNotNone(info.error)
        self.assertEqual(self.graph.calls, [])

    def test_missing_secret_file(self):
        info = self.entra(secret_file=os.path.join(self.temp.name, "nope")).lookup(
            token_identity())
        self.assertIn("secret", info.error)

    def test_a_slow_lookup_does_not_block_cached_users(self):
        entra = self.entra()
        entra.lookup(token_identity())
        entered, release = threading.Event(), threading.Event()
        fast_opener = self.graph

        def slow_opener(request, timeout):
            if "/users/slow@example.com/" in request.full_url:
                entered.set()
                release.wait(5)
                raise urllib.error.URLError("timed out")
            return fast_opener(request, timeout)

        entra._open = slow_opener
        slow = identity.Identity("slow", identity.SOURCE_TOKEN, upn="slow@example.com")
        worker = threading.Thread(target=entra.lookup, args=(slow,))
        worker.start()
        self.addCleanup(worker.join)
        self.addCleanup(release.set)
        self.assertTrue(entered.wait(5))
        started = time.monotonic()
        self.assertEqual(entra.lookup(token_identity()).groups, ("g1", "g2"))
        self.assertLess(time.monotonic() - started, 1.0)
        release.set()
        worker.join(5)
        self.assertIn("unreachable", entra.lookup(slow).error)


if __name__ == "__main__":
    unittest.main()
