"""Automated tests for temporary MP3 lifecycle, ASGI client disconnect, URL ports, and dependencies."""

import asyncio
import fcntl
import importlib.metadata as importlib_metadata
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fastapi import HTTPException, Request
from pydantic import ValidationError

import backend.main as backend_main
from backend.schemas import TTSRequest
from src.extractor import validate_url


def _make_scope(client_ip: str = "203.0.113.10") -> dict:
    """Create a standard ASGI HTTP scope for POST /api/tts."""
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/tts",
        "raw_path": b"/api/tts",
        "query_string": b"",
        "headers": [],
        "client": (client_ip, 54321),
    }


def _make_request(client_ip: str = "203.0.113.10") -> Request:
    """Create a Starlette/FastAPI Request object for endpoint testing."""
    return Request(_make_scope(client_ip))


class TestTempFileLifecycle(unittest.TestCase):
    """Test suite verifying temporary directory lifecycle, ASGI disconnect cleanup, ports, and dependencies."""

    def setUp(self) -> None:
        backend_main.rate_limiter.reset()
        self.sandbox_root = Path(tempfile.mkdtemp(prefix="tts_test_root_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.sandbox_root, ignore_errors=True)

    def _active_temp_dirs(self) -> list[Path]:
        return list(self.sandbox_root.glob(f"{backend_main.TEMP_DIR_PREFIX}*"))

    def test_01_successful_mp3_generation_and_real_asgi_streaming(self) -> None:
        """1. Successful MP3 generation streams full body via ASGI and cleans up once."""
        orig_create = backend_main.create_locked_temp_dir

        def sandboxed_create():
            return orig_create(temp_root=self.sandbox_root)

        def fake_tts(text: str, output_path: str, language: str = "es") -> str:
            Path(output_path).write_bytes(b"ID3-FAKE-MP3-DATA" * 100)
            return output_path

        with patch("backend.main.create_locked_temp_dir", side_effect=sandboxed_create), \
             patch("backend.main.extract_article", return_value="Sample article text"), \
             patch("backend.main.text_to_speech", side_effect=fake_tts):
            response = backend_main.convert_article_to_mp3(
                TTSRequest(url="https://example.com/article", language="es"),
                _make_request(),
            )

        mp3_path = Path(response.path)
        self.assertTrue(mp3_path.is_file())
        sent_messages: list[dict] = []

        async def receive() -> dict:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict) -> None:
            sent_messages.append(message)

        asyncio.run(response(_make_scope(), receive, send))

        body_bytes = b"".join(
            m.get("body", b"") for m in sent_messages if m["type"] == "http.response.body"
        )
        self.assertEqual(body_bytes, b"ID3-FAKE-MP3-DATA" * 100)
        self.assertEqual(self._active_temp_dirs(), [])

    def test_02_cleanup_after_extraction_failure(self) -> None:
        """2. Extraction failure leaves zero temporary directories behind."""
        orig_create = backend_main.create_locked_temp_dir

        def sandboxed_create():
            return orig_create(temp_root=self.sandbox_root)

        with patch("backend.main.create_locked_temp_dir", side_effect=sandboxed_create), \
             patch("backend.main.extract_article", side_effect=ConnectionError("Upstream 503")):
            with self.assertRaises(HTTPException) as ctx:
                backend_main.convert_article_to_mp3(
                    TTSRequest(url="https://example.com/fail", language="es"),
                    _make_request(),
                )
            self.assertEqual(ctx.exception.status_code, 502)
            self.assertEqual(self._active_temp_dirs(), [])

    def test_03_cleanup_after_synthesis_failure(self) -> None:
        """3. Synthesis failure cleans up the created temporary directory and lock."""
        created_holders: list[tuple[Path, object]] = []
        orig_create = backend_main.create_locked_temp_dir

        def tracking_create():
            temp_dir, lock_file = orig_create(temp_root=self.sandbox_root)
            created_holders.append((temp_dir, lock_file))
            return temp_dir, lock_file

        with patch("backend.main.create_locked_temp_dir", side_effect=tracking_create), \
             patch("backend.main.extract_article", return_value="Article text"), \
             patch("backend.main.text_to_speech", side_effect=ConnectionError("Edge TTS failed")):
            with self.assertRaises(HTTPException) as ctx:
                backend_main.convert_article_to_mp3(
                    TTSRequest(url="https://example.com/tts-fail", language="es"),
                    _make_request(),
                )
            self.assertEqual(ctx.exception.status_code, 502)

        self.assertEqual(len(created_holders), 1)
        temp_dir, lock_file = created_holders[0]
        self.assertFalse(temp_dir.exists())
        self.assertTrue(lock_file.closed)

    def test_04_cleanup_after_unexpected_exception(self) -> None:
        """4. Unexpected exception after temp dir creation cleans up once and returns safe 500."""
        created_holders: list[tuple[Path, object]] = []
        orig_create = backend_main.create_locked_temp_dir

        def tracking_create():
            temp_dir, lock_file = orig_create(temp_root=self.sandbox_root)
            created_holders.append((temp_dir, lock_file))
            return temp_dir, lock_file

        with patch("backend.main.create_locked_temp_dir", side_effect=tracking_create), \
             patch("backend.main.extract_article", return_value="Article text"), \
             patch("backend.main.text_to_speech", side_effect=KeyError("sensitive_internal_key_name")):
            with self.assertRaises(HTTPException) as ctx:
                backend_main.convert_article_to_mp3(
                    TTSRequest(url="https://example.com/unexpected", language="es"),
                    _make_request(),
                )
            self.assertEqual(ctx.exception.status_code, 500)
            self.assertNotIn("sensitive_internal_key_name", ctx.exception.detail)
            self.assertEqual(
                ctx.exception.detail,
                "An internal server error occurred while generating the audio file.",
            )

        self.assertEqual(len(created_holders), 1)
        temp_dir, lock_file = created_holders[0]
        self.assertFalse(temp_dir.exists())
        self.assertTrue(lock_file.closed)

    def test_05_temp_dir_retention_while_response_is_streaming(self) -> None:
        """5. Temporary directory and lock are retained mid-stream even if stale cleanup runs."""
        orig_create = backend_main.create_locked_temp_dir

        def sandboxed_create():
            return orig_create(temp_root=self.sandbox_root)

        def fake_tts(text: str, output_path: str, language: str = "es") -> str:
            # 200 KB file so FileResponse sends multiple 64 KB chunks
            Path(output_path).write_bytes(b"M" * (200 * 1024))
            return output_path

        with patch("backend.main.create_locked_temp_dir", side_effect=sandboxed_create), \
             patch("backend.main.extract_article", return_value="Valid text"), \
             patch("backend.main.text_to_speech", side_effect=fake_tts):
            response = backend_main.convert_article_to_mp3(
                TTSRequest(url="https://example.com/stream", language="es"),
                _make_request(),
            )

        mp3_path = Path(response.path)
        temp_dir = mp3_path.parent
        mid_stream_checks: list[bool] = []

        async def receive() -> dict:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict) -> None:
            if message["type"] == "http.response.body" and message.get("more_body", False):
                # Run stale cleanup right in the middle of streaming chunks!
                removed = backend_main.cleanup_stale_temp_dirs(temp_root=self.sandbox_root)
                mid_stream_checks.append(removed == 0 and mp3_path.is_file())

        asyncio.run(response(_make_scope(), receive, send))
        self.assertTrue(len(mid_stream_checks) >= 1 and all(mid_stream_checks))
        self.assertFalse(temp_dir.exists())

    def test_06_cleanup_after_response_streaming_completes(self) -> None:
        """6. Cleanup deletes temp directory and closes lock after ASGI streaming completes."""
        created_holders: list[tuple[Path, object]] = []
        orig_create = backend_main.create_locked_temp_dir

        def tracking_create():
            temp_dir, lock_file = orig_create(temp_root=self.sandbox_root)
            created_holders.append((temp_dir, lock_file))
            return temp_dir, lock_file

        def fake_tts(text: str, output_path: str, language: str = "es") -> str:
            Path(output_path).write_bytes(b"MP3-CONTENT")
            return output_path

        with patch("backend.main.create_locked_temp_dir", side_effect=tracking_create), \
             patch("backend.main.extract_article", return_value="Valid text"), \
             patch("backend.main.text_to_speech", side_effect=fake_tts):
            response = backend_main.convert_article_to_mp3(
                TTSRequest(url="https://example.com/complete", language="es"),
                _make_request(),
            )

        temp_dir, lock_file = created_holders[0]
        self.assertTrue(temp_dir.exists())
        self.assertFalse(lock_file.closed)

        async def receive() -> dict:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(_message: dict) -> None:
            pass

        asyncio.run(response(_make_scope(), receive, send))
        self.assertFalse(temp_dir.exists())
        self.assertTrue(lock_file.closed)

    def test_07_lock_release_and_idempotent_descriptor_closure(self) -> None:
        """7. cleanup_temp_dir and make_once_cleanup release lock and never double-close."""
        temp_dir, lock_file = backend_main.create_locked_temp_dir(temp_root=self.sandbox_root)
        cleanup_calls = 0
        orig_cleanup = backend_main.cleanup_temp_dir

        def counting_cleanup(td, lf):
            nonlocal cleanup_calls
            cleanup_calls += 1
            return orig_cleanup(td, lf)

        with patch("backend.main.cleanup_temp_dir", side_effect=counting_cleanup):
            cleanup_once = backend_main.make_once_cleanup(temp_dir, lock_file)
            cleanup_once()
            cleanup_once()
            cleanup_once()

        self.assertEqual(cleanup_calls, 1)
        self.assertTrue(lock_file.closed)
        self.assertFalse(temp_dir.exists())

    def test_08_stale_cleanup_preserves_directories_held_by_active_locks(self) -> None:
        """8. Stale cleanup preserves active directories (>10 min old) in-process and cross-process."""
        active_dir, active_lock = backend_main.create_locked_temp_dir(temp_root=self.sandbox_root)
        old_time = time.time() - 1800
        os.utime(active_dir, (old_time, old_time))
        os.utime(active_dir / backend_main.LOCK_FILENAME, (old_time, old_time))

        child_script = (
            "from pathlib import Path; import os, time; "
            "from backend.main import create_locked_temp_dir, LOCK_FILENAME; "
            f"d, lf = create_locked_temp_dir(temp_root=Path({str(self.sandbox_root)!r})); "
            "old = time.time() - 1800; os.utime(d, (old, old)); os.utime(d / LOCK_FILENAME, (old, old)); "
            "print(str(d), flush=True); time.sleep(30)"
        )
        child = subprocess.Popen(
            [sys.executable, "-c", child_script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            child_dir = Path(child.stdout.readline().strip())
            self.assertTrue(child_dir.exists())

            removed = backend_main.cleanup_stale_temp_dirs(temp_root=self.sandbox_root)
            self.assertEqual(removed, 0)
            self.assertTrue(active_dir.exists())
            self.assertTrue(child_dir.exists())
        finally:
            if child.stdout:
                child.stdout.close()
            if child.stderr:
                child.stderr.close()
            child.kill()
            child.wait(timeout=5)
            backend_main.cleanup_temp_dir(active_dir, active_lock)
            backend_main.cleanup_stale_temp_dirs(temp_root=self.sandbox_root)

    def test_09_recovery_of_abandoned_directories_after_process_crash(self) -> None:
        """9. Stale cleanup recovers directories abandoned by a SIGKILL process crash."""
        child_script = (
            "from pathlib import Path; import time; "
            "from backend.main import create_locked_temp_dir; "
            f"d, lf = create_locked_temp_dir(temp_root=Path({str(self.sandbox_root)!r})); "
            "(d / 'article.mp3').write_bytes(b'PARTIAL'); "
            "print(str(d), flush=True); time.sleep(30)"
        )
        child = subprocess.Popen(
            [sys.executable, "-c", child_script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            crashed_dir = Path(child.stdout.readline().strip())
            self.assertTrue(crashed_dir.exists())

            uninitialized_dir = Path(
                tempfile.mkdtemp(prefix=backend_main.TEMP_DIR_PREFIX, dir=str(self.sandbox_root))
            )
        finally:
            if child.stdout:
                child.stdout.close()
            if child.stderr:
                child.stderr.close()
            child.kill()
            child.wait(timeout=5)

        removed = backend_main.cleanup_stale_temp_dirs(temp_root=self.sandbox_root)
        self.assertEqual(removed, 2)
        self.assertFalse(crashed_dir.exists())
        self.assertFalse(uninitialized_dir.exists())

    def test_10_concurrent_creation_and_stale_cleanup_race_free(self) -> None:
        """10. Concurrent directory creation and stale cleanup never delete newly created active dirs."""
        active_holders: list[tuple[Path, object]] = []
        holders_lock = threading.Lock()
        errors: list[Exception] = []
        stop_cleanup = threading.Event()

        def creator_worker() -> None:
            try:
                for _ in range(10):
                    d, lf = backend_main.create_locked_temp_dir(temp_root=self.sandbox_root)
                    with holders_lock:
                        active_holders.append((d, lf))
                    time.sleep(0.005)
            except Exception as exc:
                errors.append(exc)

        def cleaner_worker() -> None:
            try:
                while not stop_cleanup.is_set():
                    backend_main.cleanup_stale_temp_dirs(temp_root=self.sandbox_root)
                    time.sleep(0.002)
            except Exception as exc:
                errors.append(exc)

        cleaner = threading.Thread(target=cleaner_worker)
        creators = [threading.Thread(target=creator_worker) for _ in range(4)]

        cleaner.start()
        for t in creators:
            t.start()
        for t in creators:
            t.join()
        stop_cleanup.set()
        cleaner.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(active_holders), 40)

        for d, lf in active_holders:
            self.assertTrue(d.exists())
            self.assertTrue((d / backend_main.LOCK_FILENAME).exists())
            backend_main.cleanup_temp_dir(d, lf)

    def test_11_client_disconnect_mid_stream_oserror_cleans_up_and_unlocks(self) -> None:
        """11. Client disconnect (OSError) during ASGI FileResponse streaming deletes temp dir and closes lock."""
        created_holders: list[tuple[Path, object]] = []
        orig_create = backend_main.create_locked_temp_dir

        def tracking_create():
            temp_dir, lock_file = orig_create(temp_root=self.sandbox_root)
            created_holders.append((temp_dir, lock_file))
            return temp_dir, lock_file

        def fake_tts(text: str, output_path: str, language: str = "es") -> str:
            # 256 KB so Starlette sends multiple 64 KB chunks
            Path(output_path).write_bytes(b"A" * (256 * 1024))
            return output_path

        with patch("backend.main.create_locked_temp_dir", side_effect=tracking_create), \
             patch("backend.main.extract_article", return_value="Article text"), \
             patch("backend.main.text_to_speech", side_effect=fake_tts):
            response = backend_main.convert_article_to_mp3(
                TTSRequest(url="https://example.com/disconnect-oserror", language="es"),
                _make_request(),
            )

        temp_dir, lock_file = created_holders[0]
        mp3_path = temp_dir / "article.mp3"
        body_chunks_seen = 0

        async def receive() -> dict:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def disconnecting_send(message: dict) -> None:
            nonlocal body_chunks_seen
            if message["type"] == "http.response.body":
                body_chunks_seen += 1
                if body_chunks_seen == 1:
                    # During chunk 1, file and OS lock must still be active!
                    self.assertTrue(mp3_path.is_file())
                    self.assertFalse(lock_file.closed)
                    with open(temp_dir / backend_main.LOCK_FILENAME, "a+b") as probe:
                        with self.assertRaises(BlockingIOError):
                            fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                elif body_chunks_seen == 2:
                    raise OSError("Client closed connection mid-stream")

        with self.assertRaises(OSError):
            asyncio.run(response(_make_scope(), receive, disconnecting_send))

        self.assertEqual(body_chunks_seen, 2)
        self.assertFalse(temp_dir.exists())
        self.assertTrue(lock_file.closed)

    def test_12_client_disconnect_mid_stream_cancelled_error_cleans_up(self) -> None:
        """12. Client disconnect (asyncio.CancelledError) during ASGI streaming deletes temp dir and closes lock."""
        created_holders: list[tuple[Path, object]] = []
        orig_create = backend_main.create_locked_temp_dir

        def tracking_create():
            temp_dir, lock_file = orig_create(temp_root=self.sandbox_root)
            created_holders.append((temp_dir, lock_file))
            return temp_dir, lock_file

        def fake_tts(text: str, output_path: str, language: str = "es") -> str:
            Path(output_path).write_bytes(b"B" * (256 * 1024))
            return output_path

        with patch("backend.main.create_locked_temp_dir", side_effect=tracking_create), \
             patch("backend.main.extract_article", return_value="Article text"), \
             patch("backend.main.text_to_speech", side_effect=fake_tts):
            response = backend_main.convert_article_to_mp3(
                TTSRequest(url="https://example.com/disconnect-cancel", language="es"),
                _make_request(),
            )

        temp_dir, lock_file = created_holders[0]
        chunks_sent = 0

        async def receive() -> dict:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def cancelling_send(message: dict) -> None:
            nonlocal chunks_sent
            if message["type"] == "http.response.body":
                chunks_sent += 1
                if chunks_sent == 2:
                    raise asyncio.CancelledError("ASGI task cancelled on client disconnect")

        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(response(_make_scope(), receive, cancelling_send))

        self.assertEqual(chunks_sent, 2)
        self.assertFalse(temp_dir.exists())
        self.assertTrue(lock_file.closed)

    def test_13_protocol_and_port_correspondence_validation(self) -> None:
        """13. validate_url and TTSRequest enforce strict correspondence between scheme and port."""
        allowed_urls = [
            "http://example.com/article",
            "http://example.com:80/article",
            "https://example.com/article",
            "https://example.com:443/article",
        ]
        for url in allowed_urls:
            self.assertEqual(validate_url(url), url)
            req = TTSRequest(url=url, language="es")
            self.assertEqual(req.url, url)

        rejected_urls = [
            "http://example.com:443/article",
            "https://example.com:80/article",
            "http://example.com:8080/article",
            "https://example.com:8443/article",
            "http://example.com:22/article",
            "https://example.com:8000/article",
        ]
        for bad_url in rejected_urls:
            with self.subTest(bad_url=bad_url):
                with self.assertRaises(ValueError):
                    validate_url(bad_url)
                with self.assertRaises(ValidationError):
                    TTSRequest(url=bad_url, language="es")

    def test_14_lxml_and_requirements_compatibility(self) -> None:
        """14. requirements.txt pins lxml<5.2.0 and newspaper3k imports lxml.html.clean cleanly."""
        req_path = Path(__file__).resolve().parent.parent / "requirements.txt"
        req_lines = [
            line.strip()
            for line in req_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        self.assertIn("lxml<5.2.0", req_lines)

        lxml_version = importlib_metadata.version("lxml")
        major, minor = (int(part) for part in lxml_version.split(".")[:2])
        self.assertTrue((major, minor) < (5, 2), f"Expected lxml < 5.2.0, got {lxml_version}")

        import lxml.html.clean  # noqa: F401
        import newspaper  # noqa: F401


if __name__ == "__main__":
    unittest.main()
