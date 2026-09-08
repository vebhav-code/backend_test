import io
import os
import tempfile
import unittest
from unittest.mock import patch
import wave

from fastapi.testclient import TestClient
import numpy as np
import soundfile as sf

from app.main import app
from app.services.audio_noise_reduction import (
    AudioFormatError,
    AudioNoiseReductionService,
    AudioProcessingError,
    noise_reduction_service,
)

client = TestClient(app)


def _generate_wav_bytes(
    duration_seconds: float = 0.5,
    sample_rate: int = 16000,
    num_channels: int = 1,
    frequency: float = 440.0,
    noise_level: float = 0.05,
) -> bytes:
    """Generate in-memory WAV bytes with a sine tone plus random noise."""
    total_samples = int(duration_seconds * sample_rate)
    time_points = np.linspace(0, duration_seconds, total_samples, endpoint=False)
    tone = 0.5 * np.sin(2 * np.pi * frequency * time_points)
    noise = noise_level * np.random.normal(size=total_samples)
    signal = tone + noise

    if num_channels > 1:
        signal = np.repeat(signal[:, np.newaxis], num_channels, axis=1)

    buffer = io.BytesIO()
    sf.write(buffer, signal, sample_rate, subtype="PCM_16", format="WAV")
    return buffer.getvalue()


class TestNoiseRemovalService(unittest.TestCase):
    """Unit tests for AudioNoiseReductionService."""

    def setUp(self):
        self.service = AudioNoiseReductionService()

    def test_clean_audio_success(self):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as in_f, \
             tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as out_f:
            in_path = in_f.name
            out_path = out_f.name
            in_f.write(_generate_wav_bytes(sample_rate=16000, num_channels=1))

        try:
            stats = self.service.clean_audio(in_path, out_path)
            self.assertEqual(stats["output_sample_rate"], 16000)
            self.assertEqual(stats["output_channels"], 1)
            self.assertGreater(stats["output_size_bytes"], 0)

            with wave.open(out_path, "rb") as wf:
                self.assertEqual(wf.getnchannels(), 1)
                self.assertEqual(wf.getsampwidth(), 2)  # 16-bit PCM
                self.assertEqual(wf.getframerate(), 16000)
        finally:
            if os.path.exists(in_path):
                os.remove(in_path)
            if os.path.exists(out_path):
                os.remove(out_path)

    def test_clean_audio_converts_stereo_and_resamples(self):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as in_f, \
             tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as out_f:
            in_path = in_f.name
            out_path = out_f.name
            # Generate 44.1 kHz stereo audio
            in_f.write(_generate_wav_bytes(sample_rate=44100, num_channels=2))

        try:
            stats = self.service.clean_audio(in_path, out_path)
            self.assertEqual(stats["input_sample_rate"], 44100)
            self.assertEqual(stats["input_channels"], 2)
            self.assertEqual(stats["output_sample_rate"], 16000)
            self.assertEqual(stats["output_channels"], 1)

            with wave.open(out_path, "rb") as wf:
                self.assertEqual(wf.getnchannels(), 1)
                self.assertEqual(wf.getsampwidth(), 2)
                self.assertEqual(wf.getframerate(), 16000)
        finally:
            if os.path.exists(in_path):
                os.remove(in_path)
            if os.path.exists(out_path):
                os.remove(out_path)

    def test_clean_audio_invalid_file_raises_format_error(self):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as in_f, \
             tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as out_f:
            in_path = in_f.name
            out_path = out_f.name
            in_f.write(b"NOT_A_VALID_WAV_FILE")

        try:
            with self.assertRaises(AudioFormatError):
                self.service.clean_audio(in_path, out_path)
        finally:
            if os.path.exists(in_path):
                os.remove(in_path)
            if os.path.exists(out_path):
                os.remove(out_path)


class TestNoiseRemovalEndpoint(unittest.TestCase):
    """Integration tests for POST /remove-noise."""

    def test_remove_noise_success(self):
        wav_data = _generate_wav_bytes(sample_rate=16000, num_channels=1)
        response = client.post(
            "/remove-noise",
            files={"file": ("flutter_audio.wav", wav_data, "audio/wav")},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("audio/wav", response.headers.get("content-type", ""))

        response_bytes = response.content
        self.assertGreater(len(response_bytes), 44)  # Valid WAV has at least 44-byte header

        # Verify output WAV properties
        with wave.open(io.BytesIO(response_bytes), "rb") as wf:
            self.assertEqual(wf.getnchannels(), 1)      # Mono
            self.assertEqual(wf.getsampwidth(), 2)      # 16-bit PCM
            self.assertEqual(wf.getframerate(), 16000)  # 16 kHz

    def test_remove_noise_stereo_converted_to_mono(self):
        stereo_wav_data = _generate_wav_bytes(sample_rate=16000, num_channels=2)
        response = client.post(
            "/remove-noise",
            files={"file": ("stereo_input.wav", stereo_wav_data, "audio/wav")},
        )

        self.assertEqual(response.status_code, 200)
        with wave.open(io.BytesIO(response.content), "rb") as wf:
            self.assertEqual(wf.getnchannels(), 1)
            self.assertEqual(wf.getsampwidth(), 2)
            self.assertEqual(wf.getframerate(), 16000)

    def test_remove_noise_resamples_to_16k(self):
        wav_44k = _generate_wav_bytes(sample_rate=44100, num_channels=1)
        response = client.post(
            "/remove-noise",
            files={"file": ("sample_44k.wav", wav_44k, "audio/wav")},
        )

        self.assertEqual(response.status_code, 200)
        with wave.open(io.BytesIO(response.content), "rb") as wf:
            self.assertEqual(wf.getframerate(), 16000)

    def test_remove_noise_missing_file(self):
        response = client.post("/remove-noise")
        self.assertEqual(response.status_code, 400)
        self.assertIn("detail", response.json())

    def test_remove_noise_empty_file(self):
        response = client.post(
            "/remove-noise",
            files={"file": ("empty.wav", b"", "audio/wav")},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Uploaded audio file is empty")

    def test_remove_noise_unsupported_extension(self):
        response = client.post(
            "/remove-noise",
            files={"file": ("sample.txt", b"some text", "text/plain")},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Unsupported file format", response.json()["detail"])

    def test_remove_noise_corrupted_wav(self):
        response = client.post(
            "/remove-noise",
            files={"file": ("corrupted.wav", b"RIFFcorruptedpayload", "audio/wav")},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid audio file", response.json()["detail"])

    def test_remove_noise_processing_failure_handled_gracefully(self):
        wav_data = _generate_wav_bytes(sample_rate=16000, num_channels=1)
        with patch("app.routers.noise_removal.noise_reduction_service.clean_audio") as mock_clean:
            mock_clean.side_effect = AudioProcessingError("Simulated algorithm failure")
            response = client.post(
                "/remove-noise",
                files={"file": ("sample.wav", wav_data, "audio/wav")},
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"], "Noise reduction processing failed")

    def test_remove_noise_cleans_up_temp_files(self):
        captured_paths = []
        original_clean = noise_reduction_service.clean_audio

        def tracking_clean(input_file_path, output_file_path):
            captured_paths.append(input_file_path)
            captured_paths.append(output_file_path)
            return original_clean(input_file_path, output_file_path)

        wav_data = _generate_wav_bytes(sample_rate=16000, num_channels=1)
        with patch.object(noise_reduction_service, "clean_audio", side_effect=tracking_clean):
            response = client.post(
                "/remove-noise",
                files={"file": ("cleanup_test.wav", wav_data, "audio/wav")},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(captured_paths), 2)
        # Verify both input and output temp files were removed
        for path in captured_paths:
            self.assertFalse(os.path.exists(path), f"Temp file was not cleaned up: {path}")


if __name__ == "__main__":
    unittest.main()
