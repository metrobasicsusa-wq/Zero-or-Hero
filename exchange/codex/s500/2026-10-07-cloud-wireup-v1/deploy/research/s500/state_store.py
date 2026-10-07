"""Append-only private GitHub state, with durable create-only reservations.

The caller initializes the private state branch. A reservation never expires and
is never overwritten. No market request may precede a confirmed reservation.
Transport errors and server response bodies are deliberately not logged.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
from urllib.parse import quote


MAX_FILE_BYTES = 30 * 1024 * 1024
MAX_PUBLISH_BYTES = 100 * 1024 * 1024
MAX_PUBLISH_ATTEMPTS = 3
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class StateError(RuntimeError):
    """A stable error code without potentially sensitive server text."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class APIError(StateError):
    def __init__(self, status: int | None = None, code: str = "api_error"):
        self.status = status
        super().__init__(f"{code}:http_{status if status is not None else 'unknown'}")


def gh_transport(method: str, endpoint: str, payload: dict | None = None):
    """JSON-only transport using the runner's existing gh authentication."""
    argv = ["gh", "api", "--method", method, endpoint]
    body = None
    if payload is not None:
        argv.extend(["--input", "-"])
        body = json.dumps(payload, allow_nan=False, separators=(",", ":"))
    try:
        process = subprocess.run(argv, input=body, text=True, capture_output=True,
                                 timeout=60, check=False)
    except subprocess.TimeoutExpired:
        raise APIError(None, "timeout") from None
    except OSError:
        raise APIError(None, "transport_unavailable") from None
    if process.returncode:
        statuses = re.findall(r"\(HTTP (\d{3})\)", process.stderr or "")
        raise APIError(int(statuses[-1]) if statuses else None) from None
    try:
        return json.loads(process.stdout)
    except (ValueError, TypeError):
        raise APIError(None, "invalid_api_json") from None


def _path(value: str) -> str:
    if (not isinstance(value, str) or not value or len(value.encode("utf-8")) > 1024
            or value.startswith("/") or "\\" in value
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or any(part in ("", ".", "..") for part in value.split("/"))):
        raise StateError("unsafe_state_path")
    return value


def _sha(value) -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise StateError("invalid_git_object_id")
    return value


def _blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def _json_bytes(payload: dict) -> bytes:
    try:
        return (json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError):
        raise StateError("invalid_reservation_payload") from None


class GitHubState:
    def __init__(self, repo="metrobasicsusa-wq/codex-500-cloud",
                 branch="codex-500-research-state", transport=None):
        if not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise StateError("invalid_repository")
        _path(branch)
        if branch.endswith(".") or ".." in branch or branch.endswith(".lock"):
            raise StateError("invalid_branch")
        self.repo, self.branch = repo, branch
        self.transport = transport or gh_transport
        self.base = f"repos/{repo}"
        self.ref = f"{self.base}/git/ref/heads/{quote(branch, safe='')}"
        self.ref_update = f"{self.base}/git/refs/heads/{quote(branch, safe='')}"
        self._blob_cache: dict[str, bytes] = {}

    def _call(self, method, endpoint, payload=None):
        try:
            return self.transport(method, endpoint, payload)
        except StateError:
            raise
        except Exception:
            # An injected transport must not expose an exception containing a key.
            raise APIError(None, "transport_failure") from None

    def snapshot(self) -> dict:
        """Return one immutable tree snapshot; missing branches are errors."""
        ref = self._call("GET", self.ref)
        try:
            head = _sha(ref["object"]["sha"])
            commit = self._call("GET", f"{self.base}/git/commits/{head}")
            tree_sha = _sha(commit["tree"]["sha"])
            tree = self._call("GET", f"{self.base}/git/trees/{tree_sha}?recursive=1")
            if tree.get("truncated") is not False or not isinstance(tree.get("tree"), list):
                raise StateError("incomplete_state_tree")
            files, seen = {}, set()
            for entry in tree["tree"]:
                path = _path(entry["path"])
                if path in seen:
                    raise StateError("duplicate_state_path")
                seen.add(path)
                if entry["type"] == "tree":
                    continue
                if entry["type"] != "blob" or entry["mode"] not in ("100644", "100755"):
                    raise StateError("unsafe_state_tree_entry")
                size = entry["size"]
                if type(size) is not int or size < 0 or size > MAX_FILE_BYTES:
                    raise StateError("state_blob_size_limit")
                files[path] = {"sha": _sha(entry["sha"]), "size": size, "mode": entry["mode"]}
            return {"head": head, "tree": tree_sha, "files": files}
        except (KeyError, TypeError):
            raise StateError("invalid_state_snapshot") from None

    def _read_entry(self, entry) -> bytes:
        sha = entry["sha"]
        if sha in self._blob_cache:
            content = self._blob_cache[sha]
        else:
            data = self._call("GET", f"{self.base}/git/blobs/{sha}")
            try:
                if data["encoding"] != "base64" or _sha(data["sha"]) != sha:
                    raise StateError("invalid_state_blob")
                encoded = data["content"]
                if not isinstance(encoded, str) or len(encoded) > MAX_FILE_BYTES * 2:
                    raise StateError("invalid_state_blob")
                # GitHub wraps base64 at 60 columns. No other characters allowed.
                content = base64.b64decode(encoded.replace("\n", "").replace("\r", ""), validate=True)
                if data["size"] != len(content):
                    raise StateError("state_blob_size_mismatch")
            except (KeyError, TypeError, ValueError):
                raise StateError("invalid_state_blob") from None
            if _blob_sha(content) != sha:
                raise StateError("state_blob_hash_mismatch")
            self._blob_cache[sha] = content
        if len(content) != entry["size"]:
            raise StateError("state_tree_size_mismatch")
        return content

    def read_many(self, paths) -> dict[str, bytes | None]:
        paths = list(dict.fromkeys(_path(p) for p in paths))
        snap = self.snapshot()
        return {p: self._read_entry(snap["files"][p]) if p in snap["files"] else None for p in paths}

    def read(self, path: str) -> bytes | None:
        return self.read_many([path])[path]

    def list_paths(self, prefix: str = "") -> list[str]:
        if prefix:
            _path(prefix[:-1] if prefix.endswith("/") else prefix)
        return sorted(p for p in self.snapshot()["files"] if p.startswith(prefix))

    def reserve(self, path: str, payload: dict) -> bool:
        """Create a permanent claim once, then confirm its exact owner/body.

        False means a claim already exists or a definitive create conflict lost.
        Unknown writes without exact readback raise; the caller must not fetch.
        Caller-supplied owner must be unique for this worker invocation.
        """
        _path(path)
        if not isinstance(payload, dict) or not isinstance(payload.get("owner"), str) or not payload["owner"]:
            raise StateError("reservation_requires_unique_owner")
        content = _json_bytes(payload)
        if len(content) > MAX_FILE_BYTES:
            raise StateError("state_blob_size_limit")
        if self.read(path) is not None:
            return False
        error = None
        try:
            self._call("PUT", f"{self.base}/contents/{quote(path, safe='/')}", {
                "message": "Reserve one immutable S500 research attempt",
                "branch": self.branch,
                "content": base64.b64encode(content).decode("ascii"),
            })
        except APIError as exc:
            if exc.status not in (None, 409, 422) and not (exc.status >= 500):
                raise
            error = exc
        actual = self.read(path)
        if actual == content:
            return True
        if actual is not None or (error is not None and error.status in (409, 422)):
            return False
        raise StateError("reservation_write_unconfirmed")

    def _publish_readback(self, files, commit_sha):
        snap = self.snapshot()
        for path, expected in files.items():
            entry = snap["files"].get(path)
            if entry is None:
                return None
            if self._read_entry(entry) != expected:
                raise StateError("immutable_path_conflict")
        # The verified current head is authoritative even if a timed-out ref
        # update was superseded by another writer publishing identical bytes.
        return {"commit_sha": snap["head"], "head": snap["head"],
                "paths": sorted(files), "readback": True,
                "byte_count": sum(map(len, files.values()))}

    def publish(self, files: dict[str, bytes], message: str) -> dict:
        """Atomically append files to latest state, with bounded nonforce retries."""
        if not isinstance(files, dict) or not files or not isinstance(message, str) or not message or len(message) > 512:
            raise StateError("invalid_publish_input")
        files = dict(files)
        for path, data in files.items():
            _path(path)
            if not isinstance(data, bytes):
                raise StateError("publish_requires_bytes")
            if len(data) > MAX_FILE_BYTES:
                raise StateError("state_blob_size_limit")
        if sum(map(len, files.values())) > MAX_PUBLISH_BYTES:
            raise StateError("publish_total_size_limit")
        uploaded = {}
        for _attempt in range(MAX_PUBLISH_ATTEMPTS):
            snap = self.snapshot()
            missing = {}
            for path, expected in files.items():
                entry = snap["files"].get(path)
                if entry is None:
                    missing[path] = expected
                elif self._read_entry(entry) != expected:
                    raise StateError("immutable_path_conflict")
            if not missing:
                result = self._publish_readback(files, snap["head"])
                if result is not None:
                    return result
                raise StateError("publish_readback_missing")
            entries = []
            for path, data in sorted(missing.items()):
                expected_sha = _blob_sha(data)
                if expected_sha not in uploaded:
                    blob = self._call("POST", f"{self.base}/git/blobs", {
                        "encoding": "base64", "content": base64.b64encode(data).decode("ascii")})
                    if not isinstance(blob, dict) or blob.get("sha") != expected_sha:
                        raise StateError("created_blob_hash_mismatch")
                    uploaded[expected_sha] = True
                entries.append({"path": path, "mode": "100644", "type": "blob", "sha": expected_sha})
            tree = self._call("POST", f"{self.base}/git/trees", {"base_tree": snap["tree"], "tree": entries})
            if not isinstance(tree, dict):
                raise StateError("invalid_created_tree")
            tree_sha = _sha(tree.get("sha"))
            commit = self._call("POST", f"{self.base}/git/commits", {
                "message": message, "tree": tree_sha, "parents": [snap["head"]]})
            if not isinstance(commit, dict):
                raise StateError("invalid_created_commit")
            commit_sha = _sha(commit.get("sha"))
            uncertain = False
            try:
                self._call("PATCH", self.ref_update, {"sha": commit_sha, "force": False})
            except APIError as exc:
                if exc.status not in (None, 409, 422) and not (exc.status >= 500):
                    raise
                uncertain = True
            result = self._publish_readback(files, commit_sha)
            if result is not None:
                return result
            if not uncertain:
                raise StateError("publish_readback_missing")
        raise StateError("publish_contention_exhausted")
