import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from state_store import APIError, GitHubState, StateError, _blob_sha, gh_transport


class FakeGitHub:
    """In-memory Git objects with nonforce compare-and-swap ref behavior."""

    def __init__(self):
        self.calls, self.blobs, self.trees, self.commits = [], {}, {}, {}
        self.sequence = 0
        self.head = self.commit(self.tree({}), None)
        self.put_behavior = None
        self.patch_behavior = None
        self.get_error = None
        self.truncated = False

    def ident(self, value):
        return hashlib.sha1(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def tree(self, mapping):
        sha = self.ident(mapping)
        self.trees[sha] = dict(mapping)
        return sha

    def commit(self, tree, parent):
        self.sequence += 1
        sha = self.ident([tree, parent, self.sequence])
        self.commits[sha] = {"tree": tree, "parent": parent}
        return sha

    def put_file(self, path, data):
        sha = _blob_sha(data)
        self.blobs[sha] = data
        files = dict(self.trees[self.commits[self.head]["tree"]])
        files[path] = sha
        self.head = self.commit(self.tree(files), self.head)

    def __call__(self, method, endpoint, payload=None):
        self.calls.append((method, endpoint, payload))
        route = endpoint.split("/codex-500-cloud/", 1)[1]
        if method == "GET" and self.get_error is not None:
            raise self.get_error
        if method == "GET" and route.startswith("git/ref/"):
            return {"object": {"sha": self.head}}
        if method == "GET" and route.startswith("git/commits/"):
            commit = self.commits[route.split("/")[-1]]
            return {"tree": {"sha": commit["tree"]}}
        if method == "GET" and route.startswith("git/trees/"):
            tree = self.trees[route.split("/")[-1].split("?")[0]]
            return {"truncated": self.truncated, "tree": [
                {"path": p, "mode": "100644", "type": "blob", "sha": sha,
                 "size": len(self.blobs[sha])} for p, sha in sorted(tree.items())]}
        if method == "GET" and route.startswith("git/blobs/"):
            sha = route.split("/")[-1]
            if sha not in self.blobs:
                raise APIError(404)
            data = self.blobs[sha]
            return {"sha": sha, "size": len(data), "encoding": "base64",
                    "content": base64.b64encode(data).decode()}
        if method == "PUT" and route.startswith("contents/"):
            path = route[len("contents/"):]
            if self.put_behavior == "permission":
                raise APIError(403)
            if self.put_behavior == "timeout_before":
                raise APIError(None, "timeout")
            if self.put_behavior == "race":
                self.put_file(path, b'{"owner":"other"}\n')
                raise APIError(422)
            if path in self.trees[self.commits[self.head]["tree"]]:
                raise APIError(422)
            assert "sha" not in payload
            self.put_file(path, base64.b64decode(payload["content"]))
            if self.put_behavior == "timeout_after":
                raise APIError(None, "timeout")
            return {"commit": {"sha": self.head}}
        if method == "POST" and route == "git/blobs":
            data = base64.b64decode(payload["content"])
            sha = _blob_sha(data)
            self.blobs[sha] = data
            return {"sha": sha}
        if method == "POST" and route == "git/trees":
            tree = dict(self.trees[payload["base_tree"]])
            for entry in payload["tree"]:
                tree[entry["path"]] = entry["sha"]
            return {"sha": self.tree(tree)}
        if method == "POST" and route == "git/commits":
            return {"sha": self.commit(payload["tree"], payload["parents"][0])}
        if method == "PATCH" and route.startswith("git/refs/"):
            assert payload["force"] is False
            if self.patch_behavior == "permission":
                raise APIError(403)
            if self.patch_behavior in ("contend_once", "contend_always"):
                self.put_file(f"other/{self.sequence}.json", b"other")
                if self.patch_behavior == "contend_once":
                    self.patch_behavior = None
                raise APIError(422)
            if self.patch_behavior == "timeout_before_once":
                self.patch_behavior = None
                raise APIError(None, "timeout")
            proposed = self.commits[payload["sha"]]
            if proposed["parent"] != self.head:
                raise APIError(422)
            self.head = payload["sha"]
            if self.patch_behavior == "timeout_after":
                raise APIError(None, "timeout")
            return {"object": {"sha": self.head}}
        raise AssertionError(f"unexpected mock route {method} {route}")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeGitHub()
        self.state = GitHubState(transport=self.api)

    def test_absent_path_is_none_but_absent_branch_is_error(self):
        self.assertIsNone(self.state.read("days/a.json"))
        self.api.get_error = APIError(404)
        with self.assertRaises(APIError):
            self.state.read("days/a.json")

    def test_permission_and_timeout_never_become_missing(self):
        for status in (403, None, 500):
            self.api.get_error = APIError(status)
            with self.assertRaises(APIError):
                self.state.read("missing.json")

    def test_read_many_has_one_head_and_blob_cache(self):
        self.api.put_file("a.json", b"same")
        self.api.put_file("b.json", b"same")
        self.api.calls.clear()
        self.assertEqual(self.state.read_many(["a.json", "b.json", "absent.json"]),
                         {"a.json": b"same", "b.json": b"same", "absent.json": None})
        self.assertEqual(sum("/git/ref/" in c[1] for c in self.api.calls), 1)
        self.assertEqual(sum("/git/blobs/" in c[1] for c in self.api.calls), 1)
        self.api.put_file("absent.json", b"now present")
        self.assertEqual(self.state.read("absent.json"), b"now present")

    def test_snapshot_and_listing(self):
        self.api.put_file("days/a/result.json", b"{}")
        self.api.put_file("rehearsals/a.json", b"{}")
        self.assertEqual(self.state.list_paths("days/"), ["days/a/result.json"])
        self.assertEqual(self.state.snapshot()["head"], self.api.head)

    def test_truncated_tree_fails_closed(self):
        self.api.truncated = True
        with self.assertRaisesRegex(StateError, "incomplete_state_tree"):
            self.state.list_paths()

    def test_reservation_create_then_never_reacquire(self):
        payload = {"owner": "run:1:nonce", "phase": "preopen"}
        self.assertTrue(self.state.reserve("days/a/claim.json", payload))
        self.assertFalse(self.state.reserve("days/a/claim.json", payload))
        self.assertFalse(self.state.reserve("days/a/claim.json", {"owner": "run:2:nonce"}))
        self.assertEqual(sum(c[0] == "PUT" for c in self.api.calls), 1)

    def test_reservation_requires_unique_owner(self):
        for payload in ({}, {"owner": ""}, {"owner": 1}):
            with self.assertRaisesRegex(StateError, "unique_owner"):
                self.state.reserve("claim.json", payload)
        self.assertEqual(self.api.calls, [])

    def test_reservation_race_loser_does_not_overwrite(self):
        self.api.put_behavior = "race"
        self.assertFalse(self.state.reserve("claim.json", {"owner": "mine"}))
        self.assertEqual(self.state.read("claim.json"), b'{"owner":"other"}\n')

    def test_reservation_unknown_write_confirms_exact_owner(self):
        self.api.put_behavior = "timeout_after"
        self.assertTrue(self.state.reserve("claim.json", {"owner": "mine"}))
        self.assertEqual(sum(c[0] == "PUT" for c in self.api.calls), 1)

    def test_reservation_unknown_missing_never_authorizes_fetch(self):
        self.api.put_behavior = "timeout_before"
        with self.assertRaisesRegex(StateError, "reservation_write_unconfirmed"):
            self.state.reserve("claim.json", {"owner": "mine"})
        self.assertEqual(sum(c[0] == "PUT" for c in self.api.calls), 1)

    def test_reservation_permission_failure(self):
        self.api.put_behavior = "permission"
        with self.assertRaises(APIError) as caught:
            self.state.reserve("claim.json", {"owner": "mine"})
        self.assertEqual(caught.exception.status, 403)

    def test_binary_large_blob_roundtrip_without_contents_reads(self):
        data = bytes(range(256)) * 5000
        result = self.state.publish({"days/a/sources.tar.gz": data, "days/a/result.json": b"{}\n"}, "Research result")
        self.assertTrue(result["readback"])
        self.assertEqual(result["commit_sha"], self.api.head)
        fresh = GitHubState(transport=self.api)
        self.assertEqual(fresh.read("days/a/sources.tar.gz"), data)
        self.assertFalse(any(c[0] == "GET" and "/contents/" in c[1] for c in self.api.calls))

    def test_identical_publish_is_idempotent_and_conflict_is_rejected(self):
        self.state.publish({"result.json": b"a"}, "First")
        writes = sum(c[0] != "GET" for c in self.api.calls)
        self.state.publish({"result.json": b"a"}, "Identical")
        self.assertEqual(sum(c[0] != "GET" for c in self.api.calls), writes)
        with self.assertRaisesRegex(StateError, "immutable_path_conflict"):
            self.state.publish({"result.json": b"changed", "new.json": b"new"}, "Conflict")
        self.assertEqual(sum(c[0] != "GET" for c in self.api.calls), writes)
        self.assertIsNone(self.state.read("new.json"))

    def test_publish_keeps_existing_matching_and_unrelated_files(self):
        self.api.put_file("claim.json", b"claim")
        self.api.put_file("existing.json", b"existing")
        self.state.publish({"claim.json": b"claim", "result.json": b"result"}, "Complete")
        self.assertEqual(self.state.list_paths(), ["claim.json", "existing.json", "result.json"])

    def test_publish_contention_rebuilds_on_latest_parent(self):
        self.api.patch_behavior = "contend_once"
        result = self.state.publish({"result.json": b"result"}, "Result")
        self.assertTrue(result["readback"])
        self.assertEqual(sum(c[0] == "PATCH" for c in self.api.calls), 2)
        self.assertEqual(len(self.state.list_paths("other/")), 1)
        patches = [c[2] for c in self.api.calls if c[0] == "PATCH"]
        self.assertTrue(all(p["force"] is False for p in patches))

    def test_publish_unknown_applied_ref_uses_readback(self):
        self.api.patch_behavior = "timeout_after"
        self.assertTrue(self.state.publish({"result.json": b"result"}, "Result")["readback"])
        self.assertEqual(sum(c[0] == "PATCH" for c in self.api.calls), 1)

    def test_publish_unknown_unapplied_ref_retries_idempotently(self):
        self.api.patch_behavior = "timeout_before_once"
        self.assertTrue(self.state.publish({"result.json": b"result"}, "Result")["readback"])
        self.assertEqual(sum(c[0] == "PATCH" for c in self.api.calls), 2)
        self.assertEqual(sum(c[0] == "POST" and c[1].endswith("/git/blobs") for c in self.api.calls), 1)

    def test_publish_contention_is_bounded(self):
        self.api.patch_behavior = "contend_always"
        with self.assertRaisesRegex(StateError, "publish_contention_exhausted"):
            self.state.publish({"result.json": b"result"}, "Result")
        self.assertEqual(sum(c[0] == "PATCH" for c in self.api.calls), 3)
        self.assertIsNone(self.state.read("result.json"))

    def test_publish_permission_denied_not_retried(self):
        self.api.patch_behavior = "permission"
        with self.assertRaises(APIError):
            self.state.publish({"result.json": b"result"}, "Result")
        self.assertEqual(sum(c[0] == "PATCH" for c in self.api.calls), 1)

    def test_paths_and_limits_rejected_before_network(self):
        for path in ("../a", "/a", "a//b", "a/./b", "a\\b", "a\n"):
            with self.assertRaises(StateError):
                self.state.publish({path: b"x"}, "Bad")
        with patch("state_store.MAX_FILE_BYTES", 2):
            with self.assertRaises(StateError):
                self.state.publish({"a": b"123"}, "Oversize")
        with patch("state_store.MAX_PUBLISH_BYTES", 3):
            with self.assertRaises(StateError):
                self.state.publish({"a": b"12", "b": b"34"}, "Oversize")
        self.assertEqual(self.api.calls, [])

    def test_blob_tamper_and_missing_listed_blob_are_errors(self):
        self.api.put_file("a", b"good")
        original = self.api

        def tampered(method, endpoint, payload=None):
            result = original(method, endpoint, payload)
            if method == "GET" and "/git/blobs/" in endpoint:
                result["content"] = base64.b64encode(b"evil").decode()
            return result

        with self.assertRaisesRegex(StateError, "hash_mismatch"):
            GitHubState(transport=tampered).read("a")

        def absent(method, endpoint, payload=None):
            if method == "GET" and "/git/blobs/" in endpoint:
                raise APIError(404)
            return original(method, endpoint, payload)

        with self.assertRaises(APIError):
            GitHubState(transport=absent).read("a")

    def test_transport_exceptions_are_sanitized(self):
        def bad(*args):
            raise RuntimeError("private-credential-value")
        with self.assertRaises(StateError) as caught:
            GitHubState(transport=bad).read("a")
        self.assertNotIn("private-credential", str(caught.exception))


class TransportTests(unittest.TestCase):
    @patch("state_store.subprocess.run")
    def test_structured_stdin_without_shell(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, '{"sha":"ok"}', "")
        gh_transport("POST", "repos/example/repo/git/commits", {"message": "literal $(anything)\nsecond"})
        args, kwargs = run.call_args
        self.assertIsInstance(args[0], list)
        self.assertNotIn("shell", kwargs)
        self.assertEqual(json.loads(kwargs["input"])["message"], "literal $(anything)\nsecond")
        self.assertEqual(kwargs["timeout"], 60)

    @patch("state_store.subprocess.run")
    def test_server_body_and_stderr_not_exposed(self, run):
        run.return_value = subprocess.CompletedProcess([], 1, "secret body", "gh: secret value (HTTP 403)")
        with self.assertRaises(APIError) as caught:
            gh_transport("GET", "repos/example/repo")
        self.assertEqual(caught.exception.status, 403)
        self.assertNotIn("secret", str(caught.exception))

    @patch("state_store.subprocess.run")
    def test_timeout_is_unknown(self, run):
        run.side_effect = subprocess.TimeoutExpired(["gh"], 60, output="private-output")
        with self.assertRaises(APIError) as caught:
            gh_transport("GET", "repos/example/repo")
        self.assertIsNone(caught.exception.status)
        self.assertNotIn("private", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
