"""Automated unit tests for dual-engine TTS (Edge TTS + ElevenLabs eleven_v4)."""

import asyncio
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException, Request
from pydantic import ValidationError
from requests.exceptions import (
    ConnectionError as RequestsConnectionError,
    Timeout as RequestsTimeout,
)

import backend.main as backend_main
from backend.schemas import TTSRequest
from backend.security import MAX_ARTICLE_CHARS
import src.converter as converter


def _make_scope(client_ip: str = "203.0.113.25") -> dict:
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


def _make_request(client_ip: str = "203.0.113.25") -> Request:
    """Create a Starlette/FastAPI Request object for endpoint testing."""
    return Request(_make_scope(client_ip))


def _make_mock_requests_response(
    status_code: int = 200,
    chunks: list[bytes] = None,
    json_payload: dict = None,
) -> MagicMock:
    """Build a mock requests.Response context manager."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.ok = 200 <= status_code < 300
    body_chunks = chunks if chunks is not None else [b"ID3-ELEVENLABS-MP3-DATA" * 20]
    resp.iter_content.return_value = iter(body_chunks)
    if json_payload is not None:
        resp.json.return_value = json_payload
    else:
        resp.json.side_effect = ValueError("No JSON body")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


class TestDualEngineTTS(unittest.TestCase):
    """Test suite for Edge TTS and ElevenLabs (eleven_v4) integration."""

    def setUp(self) -> None:
        backend_main.rate_limiter.reset()
        converter.clear_elevenlabs_voices_cache()
        self.sandbox_root = Path(tempfile.mkdtemp(prefix="tts_v2_test_"))

    def tearDown(self) -> None:
        converter.clear_elevenlabs_voices_cache()
        shutil.rmtree(self.sandbox_root, ignore_errors=True)

    def test_01_edge_tts_and_elevenlabs_independent_operation(self) -> None:
        """1. Edge TTS and ElevenLabs operate independently and generate valid MP3 files."""
        edge_out = self.sandbox_root / "edge_out.mp3"
        eleven_out = self.sandbox_root / "eleven_out.mp3"

        async def fake_edge_synth(text: str, output_file: Path, voice: str) -> None:
            output_file.write_bytes(b"EDGE-MP3-BYTES")

        mock_eleven_resp = _make_mock_requests_response(
            status_code=200,
            chunks=[b"ELEVEN-MP3-BYTES"],
        )

        with patch("src.converter._synthesize_audio", side_effect=fake_edge_synth):
            saved_edge = converter.text_to_speech(
                text="Hola mundo desde Edge TTS.",
                output_path=str(edge_out),
                language="es",
                engine="edge",
                voice="es-ES-AlvaroNeural",
            )
        self.assertEqual(Path(saved_edge).read_bytes(), b"EDGE-MP3-BYTES")

        with patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test_key_placeholder"}, clear=False), \
             patch("src.converter.requests.post", return_value=mock_eleven_resp) as mock_post:
            saved_eleven = converter.text_to_speech(
                text="Hola mundo desde ElevenLabs.",
                output_path=str(eleven_out),
                language="es",
                engine="elevenlabs",
                voice="EXAVITQu4vr4xnSDxMaL",
            )

        self.assertEqual(Path(saved_eleven).read_bytes(), b"ELEVEN-MP3-BYTES")
        mock_post.assert_called_once()

    def test_02_elevenlabs_http_request_parameters_and_model_v4(self) -> None:
        """2. ElevenLabs request uses official endpoint, eleven_v4 model, mp3_44100_128, and language_code."""
        out_path = self.sandbox_root / "params.mp3"
        mock_resp = _make_mock_requests_response(status_code=200, chunks=[b"MP3-V4-AUDIO"])

        with patch.dict(os.environ, {"ELEVENLABS_API_KEY": "xi_test_secret_token"}, clear=False), \
             patch("src.converter.requests.post", return_value=mock_resp) as mock_post:
            converter.text_to_speech(
                text="Bonjour le monde.",
                output_path=str(out_path),
                language="fr",
                engine="elevenlabs",
                voice="FGY2WhTYpPnrIDTdsKH5",
            )

        args, kwargs = mock_post.call_args
        self.assertEqual(
            args[0],
            "https://api.elevenlabs.io/v1/text-to-speech/FGY2WhTYpPnrIDTdsKH5",
        )
        self.assertEqual(kwargs["params"], {"output_format": "mp3_44100_128"})
        self.assertEqual(kwargs["headers"]["xi-api-key"], "xi_test_secret_token")
        self.assertEqual(kwargs["headers"]["Accept"], "audio/mpeg")
        self.assertEqual(
            kwargs["json"],
            {
                "text": "Bonjour le monde.",
                "model_id": "eleven_v4",
                "language_code": "fr",
            },
        )

    def test_03_engine_language_and_voice_validation(self) -> None:
        """3. Engine, language, and voice validation rejects incompatible or invented voice IDs."""
        # Valid combinations
        self.assertEqual(
            converter.validate_engine_voice("edge", "es", None),
            "es-ES-ElviraNeural",
        )
        self.assertEqual(
            converter.validate_engine_voice("edge", "es", "es-ES-AlvaroNeural"),
            "es-ES-AlvaroNeural",
        )
        self.assertEqual(
            converter.validate_engine_voice("elevenlabs", "en", None),
            "Xb7hH8MSUJpSbSDYk0k2",
        )
        self.assertEqual(
            converter.validate_engine_voice("elevenlabs", "fr", "CwhRBWXzGAHq8TQ4Fs17"),
            "CwhRBWXzGAHq8TQ4Fs17",
        )

        # Incompatible combinations must raise ValueError / ValidationError
        with self.assertRaises(ValueError):
            # English Edge voice used with Spanish
            converter.validate_engine_voice("edge", "es", "en-US-AriaNeural")

        with self.assertRaises(ValueError):
            # ElevenLabs voice ID used with Edge TTS
            converter.validate_engine_voice("edge", "es", "EXAVITQu4vr4xnSDxMaL")

        with self.assertRaises(ValueError):
            # Edge TTS voice used with ElevenLabs
            converter.validate_engine_voice("elevenlabs", "es", "es-ES-ElviraNeural")

        with self.assertRaises(ValueError):
            # Invented voice ID
            converter.validate_engine_voice("elevenlabs", "es", "invented_voice_id_123")

        with self.assertRaises(ValidationError):
            TTSRequest(
                url="https://example.com/article",
                engine="elevenlabs",
                language="es",
                voice="es-ES-ElviraNeural",
            )

        with self.assertRaises(ValidationError):
            TTSRequest(
                url="https://example.com/article",
                engine="invalid_engine",
                language="es",
            )

    def test_04_missing_api_key_handling_and_edge_fallback_availability(self) -> None:
        """4. Missing ELEVENLABS_API_KEY raises 503 for ElevenLabs while Edge TTS keeps working."""
        out_path = self.sandbox_root / "missing_key.mp3"

        with patch.dict(os.environ, {"ELEVENLABS_API_KEY": ""}, clear=False):
            with self.assertRaises(converter.ElevenLabsError) as ctx:
                converter.text_to_speech(
                    text="Texto de prueba",
                    output_path=str(out_path),
                    language="es",
                    engine="elevenlabs",
                )
            self.assertEqual(ctx.exception.status_code, 503)
            self.assertEqual(ctx.exception.error_code, "api_key_missing")
            self.assertIn("Edge TTS", ctx.exception.user_message)
            self.assertFalse(out_path.exists())

    def test_05_elevenlabs_http_errors_401_402_429_500_never_leak_key(self) -> None:
        """5. HTTP 401, 402, 429, and 500 errors map to clear messages and never expose the API key."""
        out_path = self.sandbox_root / "err.mp3"
        fake_secret = "SECRET_XI_KEY_DO_NOT_LEAK_999"

        cases = [
            (
                401,
                {"detail": {"status": "invalid_api_key", "message": "Invalid API key"}},
                401,
                "api_key_invalid",
            ),
            (
                402,
                {"detail": {"status": "quota_exceeded", "message": "Quota exceeded"}},
                402,
                "quota_exceeded",
            ),
            (
                429,
                {"detail": {"status": "too_many_requests", "message": "Concurrent limit reached"}},
                429,
                "rate_limit",
            ),
            (
                500,
                {"detail": "Internal server error"},
                502,
                "provider_error",
            ),
        ]

        for http_code, payload, expected_status, expected_code in cases:
            with self.subTest(http_code=http_code):
                mock_resp = _make_mock_requests_response(
                    status_code=http_code,
                    json_payload=payload,
                )
                with patch.dict(os.environ, {"ELEVENLABS_API_KEY": fake_secret}, clear=False), \
                     patch("src.converter.requests.post", return_value=mock_resp):
                    with self.assertRaises(converter.ElevenLabsError) as ctx:
                        converter.text_to_speech(
                            text="Prueba de errores HTTP",
                            output_path=str(out_path),
                            language="es",
                            engine="elevenlabs",
                        )
                    self.assertEqual(ctx.exception.status_code, expected_status)
                    self.assertEqual(ctx.exception.error_code, expected_code)
                    self.assertNotIn(fake_secret, ctx.exception.user_message)
                    self.assertIn("Edge TTS", ctx.exception.user_message)
                    self.assertFalse(out_path.exists())

    def test_06_elevenlabs_timeout_and_connection_errors(self) -> None:
        """6. Network timeouts (504) and connection failures (502) are handled cleanly."""
        out_path = self.sandbox_root / "net_err.mp3"

        with patch.dict(os.environ, {"ELEVENLABS_API_KEY": "valid_key"}, clear=False), \
             patch("src.converter.requests.post", side_effect=RequestsTimeout("Read timed out")):
            with self.assertRaises(converter.ElevenLabsError) as ctx:
                converter.text_to_speech(
                    text="Prueba timeout",
                    output_path=str(out_path),
                    language="es",
                    engine="elevenlabs",
                )
            self.assertEqual(ctx.exception.status_code, 504)
            self.assertEqual(ctx.exception.error_code, "timeout")

        with patch.dict(os.environ, {"ELEVENLABS_API_KEY": "valid_key"}, clear=False), \
             patch("src.converter.requests.post", side_effect=RequestsConnectionError("DNS failure")):
            with self.assertRaises(converter.ElevenLabsError) as ctx:
                converter.text_to_speech(
                    text="Prueba conexión",
                    output_path=str(out_path),
                    language="es",
                    engine="elevenlabs",
                )
            self.assertEqual(ctx.exception.status_code, 502)
            self.assertEqual(ctx.exception.error_code, "connection_error")

    def test_07_character_limit_15000_enforced_and_single_request_for_15000_chars(self) -> None:
        """7. 15,000-character limit is enforced (413 above 15,000; single request at 15,000)."""
        self.assertEqual(MAX_ARTICLE_CHARS, 15000)
        orig_create = backend_main.create_locked_temp_dir

        def sandboxed_create():
            return orig_create(temp_root=self.sandbox_root)

        # 15,001 chars -> HTTP 413 Content Too Large
        with patch("backend.main.create_locked_temp_dir", side_effect=sandboxed_create), \
             patch("backend.main.extract_article", return_value="A" * 15001):
            with self.assertRaises(HTTPException) as ctx:
                backend_main.convert_article_to_mp3(
                    TTSRequest(
                        url="https://example.com/too-long",
                        engine="elevenlabs",
                        language="es",
                    ),
                    _make_request(),
                )
            self.assertEqual(ctx.exception.status_code, 413)

        # Exactly 15,000 chars -> sent in a single request to ElevenLabs eleven_v4
        mock_resp = _make_mock_requests_response(status_code=200, chunks=[b"MP3-15K"])
        with patch.dict(os.environ, {"ELEVENLABS_API_KEY": "valid_key"}, clear=False), \
             patch("backend.main.create_locked_temp_dir", side_effect=sandboxed_create), \
             patch("backend.main.extract_article", return_value="B" * 15000), \
             patch("src.converter.requests.post", return_value=mock_resp) as mock_post:
            response = backend_main.convert_article_to_mp3(
                TTSRequest(
                    url="https://example.com/exact-15k",
                    engine="elevenlabs",
                    language="es",
                    voice="EXAVITQu4vr4xnSDxMaL",
                ),
                _make_request(),
            )

        self.assertEqual(mock_post.call_count, 1)
        self.assertEqual(len(mock_post.call_args.kwargs["json"]["text"]), 15000)

        async def receive() -> dict:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(_message: dict) -> None:
            pass

        asyncio.run(response(_make_scope(), receive, send))
        self.assertEqual(
            list(self.sandbox_root.glob(f"{backend_main.TEMP_DIR_PREFIX}*")),
            [],
        )

    def test_08_temp_dir_cleanup_on_elevenlabs_failure_in_endpoint(self) -> None:
        """8. Temporary directory and lock are cleaned up when ElevenLabs fails in POST /api/tts."""
        created_holders: list[tuple[Path, object]] = []
        orig_create = backend_main.create_locked_temp_dir

        def tracking_create():
            temp_dir, lock_file = orig_create(temp_root=self.sandbox_root)
            created_holders.append((temp_dir, lock_file))
            return temp_dir, lock_file

        mock_resp = _make_mock_requests_response(
            status_code=402,
            json_payload={"detail": {"status": "quota_exceeded", "message": "No credits left"}},
        )

        with patch.dict(os.environ, {"ELEVENLABS_API_KEY": "valid_key"}, clear=False), \
             patch("backend.main.create_locked_temp_dir", side_effect=tracking_create), \
             patch("backend.main.extract_article", return_value="Valid article text"), \
             patch("src.converter.requests.post", return_value=mock_resp):
            with self.assertRaises(HTTPException) as ctx:
                backend_main.convert_article_to_mp3(
                    TTSRequest(
                        url="https://example.com/quota-fail",
                        engine="elevenlabs",
                        language="es",
                    ),
                    _make_request(),
                )
            self.assertEqual(ctx.exception.status_code, 402)
            self.assertIn("Edge TTS", ctx.exception.detail)

        self.assertEqual(len(created_holders), 1)
        temp_dir, lock_file = created_holders[0]
        self.assertFalse(temp_dir.exists())
        self.assertTrue(lock_file.closed)

    def test_09_languages_endpoint_compatibility_and_dynamic_catalog(self) -> None:
        """9. GET /api/languages preserves backwards compatibility and returns dynamic engine catalogs."""
        mock_voices_resp = MagicMock()
        mock_voices_resp.ok = True
        mock_voices_resp.json.return_value = {
            "voices": [
                {"voice_id": "EXAVITQu4vr4xnSDxMaL", "name": "Sarah"},
                {"voice_id": "Xb7hH8MSUJpSbSDYk0k2", "name": "Alice"},
                {"voice_id": "CwhRBWXzGAHq8TQ4Fs17", "name": "Roger"},
            ]
        }

        with patch.dict(os.environ, {"ELEVENLABS_API_KEY": "valid_key"}, clear=False), \
             patch("src.converter.requests.get", return_value=mock_voices_resp) as mock_get:
            res = backend_main.list_languages()

        mock_get.assert_called_once()
        self.assertEqual(res.default, "es")
        self.assertEqual(res.default_engine, "edge")
        self.assertEqual([item.code for item in res.languages], ["es", "en", "fr"])

        engines_by_id = {eng.id: eng for eng in res.engines}
        self.assertIn("edge", engines_by_id)
        self.assertIn("elevenlabs", engines_by_id)

        edge_eng = engines_by_id["edge"]
        self.assertIsNone(edge_eng.quota_notice)
        edge_es = next(lang for lang in edge_eng.languages if lang.code == "es")
        self.assertEqual(edge_es.default_voice, "es-ES-ElviraNeural")
        self.assertTrue(all(v.engine == "edge" for v in edge_es.voices))

        eleven_eng = engines_by_id["elevenlabs"]
        self.assertEqual(eleven_eng.model_id, "eleven_v4")
        self.assertEqual(eleven_eng.quota_notice, converter.ELEVENLABS_QUOTA_NOTICE)
        eleven_es = next(lang for lang in eleven_eng.languages if lang.code == "es")
        self.assertEqual(eleven_es.default_voice, "EXAVITQu4vr4xnSDxMaL")
        self.assertEqual(
            {v.id for v in eleven_es.voices},
            {"EXAVITQu4vr4xnSDxMaL", "Xb7hH8MSUJpSbSDYk0k2", "CwhRBWXzGAHq8TQ4Fs17"},
        )

    def test_10_frontend_components_and_state_preservation_contracts(self) -> None:
        """10. Frontend source files enforce 3 selectors, quota notice, manual Edge switch, and result retention."""
        project_root = Path(__file__).resolve().parent.parent
        app_jsx = (project_root / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
        form_jsx = (
            project_root / "frontend" / "src" / "components" / "ConversionForm.jsx"
        ).read_text(encoding="utf-8")
        banner_jsx = (
            project_root / "frontend" / "src" / "components" / "StatusBanner.jsx"
        ).read_text(encoding="utf-8")

        # Verify 3 selectors in ConversionForm.jsx
        self.assertIn('id="tts-engine"', form_jsx)
        self.assertIn('id="article-language"', form_jsx)
        self.assertIn('id="tts-voice"', form_jsx)

        # Verify exact quota notice text in ConversionForm.jsx
        self.assertIn(converter.ELEVENLABS_QUOTA_NOTICE, form_jsx)
        self.assertIn('engine === "elevenlabs"', form_jsx)

        # Verify manual switch button to Edge TTS in StatusBanner.jsx
        self.assertIn("onSwitchToEdge", banner_jsx)
        self.assertIn("Cambiar a Edge TTS", banner_jsx)

        # Verify App.jsx preserves previous result ({result && <AudioResultCard result={result} />})
        # and sends engine, language, and voice in POST /api/tts without exposing API keys
        self.assertIn("{result && <AudioResultCard result={result} />}", app_jsx)
        self.assertIn("engine: submittedEngine.id", app_jsx)
        self.assertIn("voice: submittedVoice.id", app_jsx)
        self.assertNotIn("ELEVENLABS_API_KEY=", app_jsx)
        self.assertNotIn("xi-api-key", app_jsx)


if __name__ == "__main__":
    unittest.main()
