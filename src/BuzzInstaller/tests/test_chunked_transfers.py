"""Tests for resumable, chunk-based bundle transfers.

Everything here is local: the BuzzHeavier API is replaced by an in-memory
fake, so no network access is required.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

_PKG = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

from core import bundle, chunking
from core.archive import archive_name, create_zip64_store
from core.buzzheavier_api import DownloadError, UploadError
from core.bundle_service import (
    BundleDownloadService,
    BundleError,
    BundleManifestError,
    BundleUploadService,
    TransferPaused,
)
from core.download_service import DownloadResult, DownloadService
from core.transfer_db import (
    CHUNK_COMPLETED,
    CHUNK_DOWNLOADING,
    CHUNK_FAILED,
    CHUNK_UPLOADING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_INTERRUPTED,
    STATUS_PAUSED,
    ChunkVerificationError,
    TransferStore,
)
from core.transfer_lock import TransferGuard, TransferInProgressError
from utils.file_utils import extract_zip

MIB = 1024 * 1024


# ---------------------------------------------------------------------------
# Fake API
# ---------------------------------------------------------------------------
class FakeAPI:
    """In-memory stand-in for BuzzHeavierAPI."""

    def __init__(self) -> None:
        self.remote: dict[str, bytes] = {}
        self.files: dict[str, str] = {}
        self.upload_calls: dict[str, int] = {}
        self.download_calls: dict[str, int] = {}
        self.fail_upload_when = None
        self.fail_download_codes: dict[str, int] = {}
        self.corrupt_download_codes: dict[str, bytes] = {}
        self.closed = False
        self._next = 0

    def _new_code(self) -> str:
        self._next += 1
        return f"code{self._next:08d}"

    def upload_stream(self, fileobj, filename, size, progress_cb=None,
                      cancelled=None, parent_id=None):
        filename = Path(filename).name
        if self.fail_upload_when is not None and self.fail_upload_when(filename):
            raise UploadError(f"simulated upload failure: {filename}")
        data = fileobj.read(int(size))
        if len(data) != int(size):
            raise UploadError("short read")
        if cancelled is not None and cancelled():
            raise UploadError("cancelled")
        code = self._new_code()
        self.remote[code] = data
        self.files[filename] = code
        self.upload_calls[filename] = self.upload_calls.get(filename, 0) + 1
        if progress_cb is not None:
            progress_cb(int(size), int(size))
        return {
            "code": code,
            "url": f"https://buzzheavier.com/{code}",
            "name": filename,
            "size": int(size),
            "local_sha256": hashlib.sha256(data).hexdigest(),
        }

    def upload_file(self, file_path, progress_cb=None, cancelled=None,
                    parent_id=None):
        path = Path(file_path)
        data = path.read_bytes()
        if self.fail_upload_when is not None and self.fail_upload_when(path.name):
            raise UploadError(f"simulated upload failure: {path.name}")
        if cancelled is not None and cancelled():
            raise UploadError("cancelled")
        code = self._new_code()
        self.remote[code] = data
        self.files[path.name] = code
        self.upload_calls[path.name] = self.upload_calls.get(path.name, 0) + 1
        if progress_cb is not None:
            progress_cb(len(data), len(data))
        return {
            "code": code,
            "url": f"https://buzzheavier.com/{code}",
            "name": path.name,
            "size": len(data),
            "local_sha256": hashlib.sha256(data).hexdigest(),
        }

    def peek_download(self, code, limit=16384):
        return self.remote.get(code, b"")[: int(limit)]

    def download_file(self, code, dest_path=None, progress_cb=None,
                      cancelled=None):
        self.download_calls[code] = self.download_calls.get(code, 0) + 1
        remaining = self.fail_download_codes.get(code, 0)
        if remaining > 0:
            self.fail_download_codes[code] = remaining - 1
            raise DownloadError(f"simulated download failure: {code}")
        if code not in self.remote:
            raise DownloadError(f"missing remote file: {code}")
        data = self.corrupt_download_codes.get(code, self.remote[code])
        target = Path(dest_path) if dest_path is not None else Path(f"{code}.bin")
        if target.is_dir():
            target = target / f"{code}.bin"
        target.parent.mkdir(parents=True, exist_ok=True)
        if cancelled is not None and cancelled():
            raise DownloadError("cancelled")
        target.write_bytes(data)
        if progress_cb is not None:
            progress_cb(len(data), len(data))
        return target

    def close(self):
        self.closed = True


class FakeLegacy:
    def __init__(self) -> None:
        self.called = []
        self.config = None

    def download(self, code, progress_cb=None, cancelled=None, config=None):
        self.called.append(code)
        self.config = config
        return DownloadResult(
            code=code,
            file_path=Path(code),
            file_name=code,
            size=0,
        )

    def apply_post_download(self, result, config, force_extract=False):
        return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _config(tmp: Path, chunk_size: int = MIB, retry: int = 1) -> dict:
    return {
        "chunked_transfers_enabled": True,
        "chunk_size_bytes": chunk_size,
        "chunk_upload_concurrency": 1,
        "chunk_download_concurrency": 1,
        "chunk_retry_limit": retry,
        "chunk_retry_backoff": 0.0,
        "download_folder": str(tmp / "downloads"),
        "auto_extract_zip": False,
        "delete_zip_after_extract": False,
        "launch_folder_after_download": False,
        "keep_bundle_archives": False,
        "buzzheavier_account_id": "",
        "buzzheavier_bundle_parent_id": "",
    }


def _make_file(path: Path, size: int, seed: int = 0) -> bytes:
    data = bytes((i * 31 + seed) % 256 for i in range(size))
    path.write_bytes(data)
    return data


def _upload_service(tmp: Path, api: FakeAPI, store: TransferStore,
                    cfg: dict) -> BundleUploadService:
    return BundleUploadService(
        api_factory=lambda: api,
        store=store,
        config=cfg,
        staging_dir=tmp / "staging-up",
        lock_dir=tmp / "locks",
    )


def _download_service(tmp: Path, api: FakeAPI, store: TransferStore,
                      cfg: dict, legacy=None) -> BundleDownloadService:
    return BundleDownloadService(
        api_factory=lambda: api,
        store=store,
        config=cfg,
        staging_dir=tmp / "staging-down",
        lock_dir=tmp / "locks",
        legacy_service=legacy or DownloadService(api=api),
    )


# ---------------------------------------------------------------------------
# Chunk planning
# ---------------------------------------------------------------------------
def test_chunk_planning_small_exact_and_partial():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        small = tmp / "small.bin"
        _make_file(small, MIB // 2, 1)
        specs = chunking.plan_chunks(small, MIB, "uuid-small")
        assert len(specs) == 1
        assert specs[0].offset == 0
        assert specs[0].size == MIB // 2

        exact = tmp / "exact.bin"
        _make_file(exact, 2 * MIB, 2)
        specs = chunking.plan_chunks(exact, MIB, "uuid-exact")
        assert [s.size for s in specs] == [MIB, MIB]
        assert [s.offset for s in specs] == [0, MIB]

        partial = tmp / "partial.bin"
        _make_file(partial, 2 * MIB + 12345, 3)
        specs = chunking.plan_chunks(partial, MIB, "uuid-partial")
        assert [s.size for s in specs] == [MIB, MIB, 12345]
        assert [s.offset for s in specs] == [0, MIB, 2 * MIB]
        assert len({s.name for s in specs}) == 3
        assert specs[-1].end == partial.stat().st_size


def test_chunk_count_and_bundle_candidate():
    assert chunking.chunk_count(0, 10 * 1024**3) == 0
    assert chunking.chunk_count(10 * 1024**3, 10 * 1024**3) == 1
    assert chunking.chunk_count(10 * 1024**3 + 1, 10 * 1024**3) == 2

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        folder = tmp / "folder"
        folder.mkdir()
        assert chunking.is_bundle_candidate(folder, MIB)
        small = tmp / "small.bin"
        _make_file(small, 100, 0)
        assert not chunking.is_bundle_candidate(small, MIB)
        large = tmp / "large.bin"
        _make_file(large, MIB + 1, 0)
        assert chunking.is_bundle_candidate(large, MIB)


def test_chunk_hashes_match_streamed_ranges():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        path = tmp / "data.bin"
        data = _make_file(path, 2 * MIB + 77, 7)
        specs = chunking.plan_chunks(path, MIB, "uuid-hash")
        for spec in specs:
            expected = hashlib.sha256(data[spec.offset:spec.end]).hexdigest()
            assert chunking.hash_range(path, spec.offset, spec.size) == expected
        assert chunking.sha256_file(path) == hashlib.sha256(data).hexdigest()
        assert chunking.source_identity(path) == (
            len(data), hashlib.sha256(data).hexdigest()
        )


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------
def test_transfer_store_persists_and_verifies_chunks():
    with tempfile.TemporaryDirectory() as raw:
        store = TransferStore(Path(raw) / "transfers.db")
        try:
            specs = [
                chunking.ChunkSpec(0, 0, 10, "a", ""),
                chunking.ChunkSpec(1, 10, 5, "b", ""),
            ]
            transfer = store.create_transfer(
                uuid="u1", direction="upload", name="file.bin",
                source_path="source.bin", size=15, sha256="a" * 64,
                chunk_size=10, chunks=specs,
            )
            assert transfer["total_chunks"] == 2
            assert transfer["completed_chunks"] == 0

            store.mark_chunk_started("u1", 0)
            chunk = store.get_chunk("u1", 0)
            assert chunk["status"] == CHUNK_UPLOADING
            assert chunk["attempts"] == 1

            digest = hashlib.sha256(b"0123456789").hexdigest()
            assert store.mark_chunk_completed(
                "u1", 0, sha256=digest, size=10, remote_code="abc123",
            )
            assert not store.mark_chunk_completed(
                "u1", 0, sha256=digest, size=10, remote_code="abc123",
            )
            transfer = store.get_transfer("u1")
            assert transfer["completed_chunks"] == 1

            try:
                store.mark_chunk_completed("u1", 1, sha256="b" * 64, size=4)
            except ChunkVerificationError:
                pass
            else:
                raise AssertionError("size mismatch should be rejected")

            try:
                store.mark_chunk_completed("u1", 1, sha256="", size=5)
            except ChunkVerificationError:
                pass
            else:
                raise AssertionError("missing checksum should be rejected")

            digest2 = hashlib.sha256(b"abcde").hexdigest()
            assert store.mark_chunk_completed(
                "u1", 1, sha256=digest2, size=5,
            )
            assert store.get_transfer("u1")["status"] == "finalizing"
            assert store.completed_chunk_indexes("u1") == {0, 1}

            store.set_status("u1", STATUS_COMPLETED)
            assert store.find_resumable(
                "upload", "source.bin", 15, "a" * 64,
            ) is None
            store.set_status("u1", STATUS_INTERRUPTED)
            assert store.find_resumable(
                "upload", "source.bin", 15, "a" * 64,
            )["uuid"] == "u1"
            assert [t["uuid"] for t in store.interrupted("upload")] == ["u1"]

            # Resetting a completed chunk must also repair the count.
            store.reset_chunk("u1", 1, include_completed=True)
            assert store.get_chunk("u1", 1)["status"] == "pending"
            assert store.get_transfer("u1")["completed_chunks"] == 1
        finally:
            store.close()


def test_transfer_store_direction_aware_chunk_status():
    with tempfile.TemporaryDirectory() as raw:
        store = TransferStore(Path(raw) / "transfers.db")
        try:
            specs = [chunking.ChunkSpec(0, 0, 4, "a", "")]
            store.create_transfer(
                uuid="d1", direction="download", name="file.bin",
                source_path="code", size=4, sha256="c" * 64,
                chunk_size=4, chunks=specs,
            )
            store.mark_chunk_started("d1", 0)
            assert store.get_chunk("d1", 0)["status"] == CHUNK_DOWNLOADING
        finally:
            store.close()


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------
def _manifest_dict(name="file.bin", size=10, chunks=None):
    chunks = chunks or [{
        "index": 0, "offset": 0, "size": 10,
        "sha256": hashlib.sha256(b"0123456789").hexdigest(),
        "name": "part0", "remote_code": "abcd1234",
    }]
    return {
        "format": bundle.FORMAT_NAME,
        "format_id": bundle.FORMAT_ID,
        "version": bundle.FORMAT_VERSION,
        "transfer_uuid": "0123456789abcdef",
        "name": name,
        "kind": "file",
        "size": size,
        "sha256": hashlib.sha256(b"0123456789").hexdigest(),
        "chunk_size": 10,
        "total_chunks": len(chunks),
        "created_at": 0,
        "chunks": chunks,
    }


def test_manifest_roundtrip_and_prefix_detection():
    manifest = bundle.build_manifest(
        transfer_uuid="0123456789abcdef", name="file.bin", size=10,
        sha256=hashlib.sha256(b"0123456789").hexdigest(),
        chunk_size=10,
        chunks=[{
            "index": 0, "offset": 0, "size": 10,
            "sha256": hashlib.sha256(b"0123456789").hexdigest(),
            "name": "part0", "remote_code": "abcd1234",
        }],
    )
    raw = bundle.serialize_manifest(manifest)
    parsed = bundle.parse_manifest(raw)
    assert parsed["transfer_uuid"] == manifest["transfer_uuid"]
    assert parsed["chunks"][0]["remote_code"] == "abcd1234"
    assert bundle.is_manifest_prefix(raw)
    assert not bundle.is_manifest_prefix(b"hello world")
    assert not bundle.is_manifest_prefix(b'{"hello": 1}')


def test_manifest_rejects_invalid_input():
    cases = []

    bad = _manifest_dict()
    bad["version"] = 99
    cases.append(bad)

    bad = _manifest_dict()
    bad["format_id"] = "OTHER"
    cases.append(bad)

    bad = _manifest_dict()
    bad["total_chunks"] = 2
    cases.append(bad)

    bad = _manifest_dict()
    bad["chunks"][0]["index"] = 1
    cases.append(bad)

    bad = _manifest_dict()
    bad["chunks"][0]["offset"] = 1
    cases.append(bad)

    bad = _manifest_dict()
    bad["chunks"][0]["sha256"] = "not-a-hash"
    cases.append(bad)

    bad = _manifest_dict()
    bad["chunks"][0]["remote_code"] = "../bad"
    cases.append(bad)

    bad = _manifest_dict()
    bad["chunks"][0]["size"] = 9
    cases.append(bad)

    for case in cases:
        try:
            bundle.parse_manifest(case)
        except bundle.ManifestError:
            continue
        raise AssertionError(f"manifest should have been rejected: {case}")


def test_manifest_sanitizes_untrusted_names():
    manifest = _manifest_dict(name="..\\..\\evil.txt")
    parsed = bundle.parse_manifest(manifest)
    assert parsed["name"] == "evil.txt"
    assert "/" not in parsed["name"] and "\\" not in parsed["name"]

    manifest = _manifest_dict()
    manifest["chunks"][0]["name"] = "../../escape.bin"
    parsed = bundle.parse_manifest(manifest)
    assert parsed["chunks"][0]["name"] == "escape.bin"


# ---------------------------------------------------------------------------
# Archive / extraction safety
# ---------------------------------------------------------------------------
def test_zip64_store_roundtrip_and_safe_extraction():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        source = tmp / "Folder"
        (source / "nested").mkdir(parents=True)
        (source / "hello.txt").write_text("hello", encoding="utf-8")
        (source / "nested" / "data.bin").write_bytes(b"\x00\x01\x02")
        (source / "empty.txt").write_bytes(b"")

        archive = tmp / archive_name("Folder")
        create_zip64_store(source, archive)
        assert archive.is_file()

        dest = tmp / "extracted"
        extract_zip(archive, dest)
        assert (dest / "hello.txt").read_text(encoding="utf-8") == "hello"
        assert (dest / "nested" / "data.bin").read_bytes() == b"\x00\x01\x02"
        assert (dest / "empty.txt").read_bytes() == b""


def test_extract_zip_blocks_zip_slip_and_never_overwrites():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        archive = tmp / "evil.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("../escape.txt", b"bad")
            zf.writestr("C:/absolute.txt", b"bad")
            zf.writestr("/rooted.txt", b"bad")
            zf.writestr("good.txt", b"good")
            zf.writestr("nested/good.txt", b"nested")

        dest = tmp / "dest"
        dest.mkdir()
        (dest / "good.txt").write_bytes(b"keep me")
        extract_zip(archive, dest)

        assert not (tmp / "escape.txt").exists()
        assert not (dest / "absolute.txt").exists()
        assert not (dest / "rooted.txt").exists()
        assert (dest / "good.txt").read_bytes() == b"keep me"
        assert (dest / "good (1).txt").read_bytes() == b"good"
        assert (dest / "nested" / "good.txt").read_bytes() == b"nested"


# ---------------------------------------------------------------------------
# Bundle upload
# ---------------------------------------------------------------------------
def test_bundle_upload_success_and_manifest():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        source = tmp / "payload.bin"
        data = _make_file(source, 2 * MIB + 333, 11)
        cfg = _config(tmp)
        api = FakeAPI()
        store = TransferStore(tmp / "transfers.db")
        try:
            service = _upload_service(tmp, api, store, cfg)
            result = service.upload(source)
            assert result.code and result.bz_url == f"BZ://{result.code}"
            assert result.total_chunks == 3
            assert result.sha256 == hashlib.sha256(data).hexdigest()
            assert source.read_bytes() == data

            manifest = bundle.parse_manifest(api.remote[result.code])
            assert manifest["kind"] == "file"
            assert manifest["total_chunks"] == 3
            assert manifest["sha256"] == hashlib.sha256(data).hexdigest()
            rebuilt = b"".join(
                api.remote[chunk["remote_code"]] for chunk in manifest["chunks"]
            )
            assert rebuilt == data
            assert all(
                chunk["sha256"] == hashlib.sha256(
                    api.remote[chunk["remote_code"]]
                ).hexdigest()
                for chunk in manifest["chunks"]
            )

            transfer = store.get_transfer(result.transfer_uuid)
            assert transfer["status"] == STATUS_COMPLETED
            assert transfer["manifest_code"] == result.code
            assert all(
                c["status"] == CHUNK_COMPLETED
                for c in store.get_chunks(result.transfer_uuid)
            )
            assert not (tmp / "staging-up" / result.transfer_uuid).exists()
        finally:
            store.close()


def test_bundle_upload_interrupted_then_resumed():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        source = tmp / "payload.bin"
        data = _make_file(source, 3 * MIB + 5, 12)
        cfg = _config(tmp, retry=1)
        api = FakeAPI()
        api.fail_upload_when = lambda name: ".part00001-" in name
        store = TransferStore(tmp / "transfers.db")
        try:
            service = _upload_service(tmp, api, store, cfg)
            try:
                service.upload(source)
            except BundleError:
                pass
            else:
                raise AssertionError("expected the first upload to fail")

            failed = store.list_transfers("upload")[0]
            assert failed["status"] == STATUS_FAILED
            assert store.get_chunk(failed["uuid"], 1)["status"] == CHUNK_FAILED
            assert store.get_chunk(failed["uuid"], 0)["status"] == CHUNK_COMPLETED

            api.fail_upload_when = None
            service2 = _upload_service(tmp, api, store, cfg)
            result = service2.upload("", resume_uuid=failed["uuid"])
            assert result.total_chunks == 4
            assert result.sha256 == hashlib.sha256(data).hexdigest()

            manifest = bundle.parse_manifest(api.remote[result.code])
            rebuilt = b"".join(
                api.remote[c["remote_code"]] for c in manifest["chunks"]
            )
            assert rebuilt == data
            # No remote chunk name was uploaded twice.
            assert all(count == 1 for count in api.upload_calls.values())
            assert store.get_transfer(failed["uuid"])["status"] == STATUS_COMPLETED
        finally:
            store.close()


def test_bundle_upload_pause_and_resume():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        source = tmp / "payload.bin"
        data = _make_file(source, 3 * MIB + 5, 21)
        cfg = _config(tmp, retry=1)
        api = FakeAPI()
        store = TransferStore(tmp / "transfers.db")
        try:
            service = _upload_service(tmp, api, store, cfg)
            state = {"paused": False}

            def chunk_cb(index, chunk_state, detail):
                if chunk_state == "completed" and not state["paused"]:
                    state["paused"] = True
                    service.pause()

            try:
                service.upload(source, chunk_cb=chunk_cb)
            except TransferPaused:
                pass
            else:
                raise AssertionError("expected the upload to pause")

            transfer = store.list_transfers("upload")[0]
            assert transfer["status"] == STATUS_PAUSED
            assert store.completed_chunk_indexes(transfer["uuid"]) == {0}
            first = store.get_chunk(transfer["uuid"], 0)
            assert api.upload_calls[first["name"]] == 1

            service2 = _upload_service(tmp, api, store, cfg)
            result = service2.upload("", resume_uuid=transfer["uuid"])
            assert result.total_chunks == 4
            assert store.get_transfer(transfer["uuid"])["status"] == STATUS_COMPLETED
            # The already verified chunk was not uploaded a second time.
            assert api.upload_calls[first["name"]] == 1

            manifest = bundle.parse_manifest(api.remote[result.code])
            rebuilt = b"".join(
                api.remote[c["remote_code"]] for c in manifest["chunks"]
            )
            assert rebuilt == data
        finally:
            store.close()


def test_bundle_upload_resume_detects_changed_source():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        source = tmp / "payload.bin"
        _make_file(source, 2 * MIB + 1, 13)
        cfg = _config(tmp, retry=1)
        api = FakeAPI()
        api.fail_upload_when = lambda name: ".part00001-" in name
        store = TransferStore(tmp / "transfers.db")
        try:
            service = _upload_service(tmp, api, store, cfg)
            try:
                service.upload(source)
            except BundleError:
                pass
            failed = store.list_transfers("upload")[0]
            source.write_bytes(b"changed")
            api.fail_upload_when = None
            service2 = _upload_service(tmp, api, store, cfg)
            try:
                service2.upload("", resume_uuid=failed["uuid"])
            except chunking.SourceChangedError:
                pass
            else:
                raise AssertionError("changed source should not resume")
            assert store.get_transfer(failed["uuid"])["status"] == STATUS_FAILED
        finally:
            store.close()


# ---------------------------------------------------------------------------
# Bundle download
# ---------------------------------------------------------------------------
def _upload_for_download(tmp, data, cfg, api, store):
    source = tmp / "payload.bin"
    source.write_bytes(data)
    result = _upload_service(tmp, api, store, cfg).upload(source)
    return result, bundle.parse_manifest(api.remote[result.code])


def test_bundle_download_success_and_legacy_fallback():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        data = b"abcdefghij" * 1000
        cfg = _config(tmp, chunk_size=4096)
        api = FakeAPI()
        store = TransferStore(tmp / "transfers.db")
        try:
            result, manifest = _upload_for_download(tmp, data, cfg, api, store)
            service = _download_service(tmp, api, store, cfg)
            downloaded = service.download(result.code)
            assert downloaded.file_path.read_bytes() == data
            assert downloaded.size == len(data)
            transfer = store.get_transfer(manifest["transfer_uuid"])
            assert transfer["status"] == STATUS_COMPLETED

            # A non-manifest link still uses the unchanged legacy path.
            api.remote["legacycode"] = b"not a bundle"
            legacy = FakeLegacy()
            fallback = _download_service(tmp, api, store, cfg, legacy=legacy)
            fallback.download("legacycode")
            assert legacy.called == ["legacycode"]
        finally:
            store.close()


def test_bundle_download_interrupted_then_resumed():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        data = os.urandom(3 * MIB + 9)
        cfg = _config(tmp, chunk_size=MIB, retry=1)
        api = FakeAPI()
        store = TransferStore(tmp / "transfers.db")
        try:
            result, manifest = _upload_for_download(tmp, data, cfg, api, store)
            bad_code = manifest["chunks"][1]["remote_code"]
            api.fail_download_codes[bad_code] = 1
            service = _download_service(tmp, api, store, cfg)
            try:
                service.download(result.code)
            except BundleError:
                pass
            else:
                raise AssertionError("expected the first download to fail")

            transfer = store.find_by_manifest_code(result.code)
            local_uuid = transfer["uuid"]
            assert transfer["status"] == STATUS_FAILED
            assert store.get_chunk(local_uuid, 1)["status"] == CHUNK_FAILED
            assert store.get_chunk(local_uuid, 0)["status"] == CHUNK_COMPLETED

            api.fail_download_codes.clear()
            service2 = _download_service(tmp, api, store, cfg)
            resumed = service2.download("", resume_uuid=local_uuid)
            assert resumed.file_path.read_bytes() == data
            assert store.get_transfer(local_uuid)["status"] == STATUS_COMPLETED
        finally:
            store.close()


def test_bundle_download_pause_and_resume():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        data = os.urandom(3 * MIB + 9)
        cfg = _config(tmp, chunk_size=MIB, retry=1)
        api = FakeAPI()
        store = TransferStore(tmp / "transfers.db")
        try:
            result, manifest = _upload_for_download(tmp, data, cfg, api, store)
            service = _download_service(tmp, api, store, cfg)
            state = {"paused": False}

            def chunk_cb(index, chunk_state, detail):
                if chunk_state == "completed" and not state["paused"]:
                    state["paused"] = True
                    service.pause()

            try:
                service.download(result.code, chunk_cb=chunk_cb)
            except TransferPaused:
                pass
            else:
                raise AssertionError("expected the download to pause")

            transfer = store.find_by_manifest_code(result.code)
            local_uuid = transfer["uuid"]
            assert transfer["status"] == STATUS_PAUSED
            assert store.completed_chunk_indexes(local_uuid) == {0}
            first_code = manifest["chunks"][0]["remote_code"]
            assert api.download_calls[first_code] == 1

            service2 = _download_service(tmp, api, store, cfg)
            resumed = service2.download("", resume_uuid=local_uuid)
            assert resumed.file_path.read_bytes() == data
            assert store.get_transfer(local_uuid)["status"] == STATUS_COMPLETED
            assert api.download_calls[first_code] == 1
        finally:
            store.close()


def test_bundle_download_corrupt_chunk_and_missing_remote():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        data = os.urandom(2 * MIB + 17)
        cfg = _config(tmp, chunk_size=MIB, retry=1)
        api = FakeAPI()
        store = TransferStore(tmp / "transfers.db")
        try:
            result, manifest = _upload_for_download(tmp, data, cfg, api, store)
            bad_code = manifest["chunks"][1]["remote_code"]
            api.corrupt_download_codes[bad_code] = b"x" * manifest["chunks"][1]["size"]
            service = _download_service(tmp, api, store, cfg)
            try:
                service.download(result.code)
            except BundleError:
                pass
            else:
                raise AssertionError("corrupt chunk should fail verification")
            download_transfer = store.find_by_manifest_code(result.code)
            local_uuid = download_transfer["uuid"]
            assert store.get_chunk(local_uuid, 1)["status"] == CHUNK_FAILED
            assert not list((tmp / "downloads").rglob("payload.bin"))

            api.corrupt_download_codes.clear()
            service2 = _download_service(tmp, api, store, cfg)
            resumed = service2.download("", resume_uuid=local_uuid)
            assert resumed.file_path.read_bytes() == data
        finally:
            store.close()

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        data = os.urandom(2 * MIB)
        cfg = _config(tmp, chunk_size=MIB, retry=1)
        api = FakeAPI()
        store = TransferStore(tmp / "transfers.db")
        try:
            result, manifest = _upload_for_download(tmp, data, cfg, api, store)
            missing_code = manifest["chunks"][0]["remote_code"]
            saved = api.remote.pop(missing_code)
            service = _download_service(tmp, api, store, cfg)
            try:
                service.download(result.code)
            except BundleError:
                pass
            else:
                raise AssertionError("missing remote chunk should fail")
            download_transfer = store.find_by_manifest_code(result.code)
            local_uuid = download_transfer["uuid"]
            assert store.get_chunk(local_uuid, 0)["status"] == CHUNK_FAILED
            api.remote[missing_code] = saved
            service2 = _download_service(tmp, api, store, cfg)
            assert service2.download(
                "", resume_uuid=local_uuid
            ).file_path.read_bytes() == data
        finally:
            store.close()


def test_bundle_download_invalid_manifest_is_rejected():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        cfg = _config(tmp)
        api = FakeAPI()
        store = TransferStore(tmp / "transfers.db")
        try:
            manifest = _manifest_dict()
            manifest["version"] = 999
            import json
            api.remote["badmanifest"] = json.dumps(manifest).encode("utf-8")
            service = _download_service(tmp, api, store, cfg)
            try:
                service.download("badmanifest")
            except BundleManifestError:
                pass
            else:
                raise AssertionError("invalid manifest should be rejected")
            assert store.list_transfers("download") == []
        finally:
            store.close()


# ---------------------------------------------------------------------------
# Folder transfers
# ---------------------------------------------------------------------------
def test_folder_upload_and_download_roundtrip():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        source = tmp / "MyFolder"
        (source / "nested").mkdir(parents=True)
        (source / "a.txt").write_text("alpha", encoding="utf-8")
        (source / "nested" / "b.bin").write_bytes(b"\x01\x02\x03")
        (source / "empty.txt").write_bytes(b"")
        cfg = _config(tmp, chunk_size=4096)
        api = FakeAPI()
        store = TransferStore(tmp / "transfers.db")
        try:
            upload = _upload_service(tmp, api, store, cfg)
            result = upload.upload(source)
            manifest = bundle.parse_manifest(api.remote[result.code])
            assert manifest["kind"] == "folder"
            assert manifest["name"] == "MyFolder"
            assert manifest["archive_format"] == "zip64-store"
            assert source.is_dir()

            download = _download_service(tmp, api, store, cfg)
            downloaded = download.download(result.code)
            assert downloaded.was_extracted
            assert downloaded.extracted_to is not None
            assert (downloaded.extracted_to / "a.txt").read_text(
                encoding="utf-8"
            ) == "alpha"
            assert (downloaded.extracted_to / "nested" / "b.bin").read_bytes() == (
                b"\x01\x02\x03"
            )
            assert (downloaded.extracted_to / "empty.txt").read_bytes() == b""

            # Re-opening the same link returns the completed transfer and
            # must not overwrite or re-extract anything.
            download2 = _download_service(tmp, api, store, cfg)
            downloaded2 = download2.download(result.code)
            assert downloaded2.was_extracted
            assert downloaded2.extracted_to == downloaded.extracted_to
            assert (downloaded.extracted_to / "a.txt").read_text(
                encoding="utf-8"
            ) == "alpha"
        finally:
            store.close()


# ---------------------------------------------------------------------------
# Duplicate prevention / locking
# ---------------------------------------------------------------------------
def test_transfer_guard_prevents_duplicate_workers():
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        first = TransferGuard("same-transfer", tmp / "locks")
        second = TransferGuard("same-transfer", tmp / "locks")
        assert first.acquire()
        try:
            assert not second.acquire()
        finally:
            first.release()
        assert second.acquire()
        second.release()


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
TESTS = [
    test_chunk_planning_small_exact_and_partial,
    test_chunk_count_and_bundle_candidate,
    test_chunk_hashes_match_streamed_ranges,
    test_transfer_store_persists_and_verifies_chunks,
    test_transfer_store_direction_aware_chunk_status,
    test_manifest_roundtrip_and_prefix_detection,
    test_manifest_rejects_invalid_input,
    test_manifest_sanitizes_untrusted_names,
    test_zip64_store_roundtrip_and_safe_extraction,
    test_extract_zip_blocks_zip_slip_and_never_overwrites,
    test_bundle_upload_success_and_manifest,
    test_bundle_upload_interrupted_then_resumed,
    test_bundle_upload_pause_and_resume,
    test_bundle_upload_resume_detects_changed_source,
    test_bundle_download_success_and_legacy_fallback,
    test_bundle_download_interrupted_then_resumed,
    test_bundle_download_pause_and_resume,
    test_bundle_download_corrupt_chunk_and_missing_remote,
    test_bundle_download_invalid_manifest_is_rejected,
    test_folder_upload_and_download_roundtrip,
    test_transfer_guard_prevents_duplicate_workers,
]


def main() -> int:
    failed = 0
    for test in TESTS:
        try:
            test()
            print(f"PASS: {test.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL: {test.__name__}: {exc}")
        except Exception as exc:
            failed += 1
            import traceback
            print(f"ERROR: {test.__name__}: {exc}")
            traceback.print_exc()
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
