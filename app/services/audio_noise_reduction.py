import logging
import math
import os
from typing import Any, Dict

import noisereduce as nr
import numpy as np
import scipy.signal
import soundfile as sf

logger = logging.getLogger(__name__)


class AudioProcessingError(Exception):
    """Base exception for audio processing failures."""
    pass


class AudioFormatError(AudioProcessingError):
    """Exception raised when an audio file cannot be decoded or has an invalid format."""
    pass


class AudioNoiseReductionService:
    """Service to load audio, remove background noise, and normalize output to 16 kHz Mono PCM 16-bit WAV."""

    def __init__(self, target_sample_rate: int = 16000, prop_decrease: float = 0.85):
        self.target_sample_rate = target_sample_rate
        self.prop_decrease = prop_decrease

    def clean_audio(
        self,
        input_file_path: str,
        output_file_path: str,
    ) -> Dict[str, Any]:
        """
        Process an input audio file to remove background noise.

        1. Loads audio via soundfile.
        2. Converts multi-channel audio to mono.
        3. Resamples audio to 16 kHz if necessary.
        4. Applies CPU-based spectral gating noise reduction.
        5. Saves cleaned audio as 16 kHz Mono PCM 16-bit WAV.

        Returns a dictionary with processing statistics.
        """
        if not os.path.exists(input_file_path):
            raise AudioFormatError(f"Input file not found: {input_file_path}")

        try:
            audio_data, sample_rate = sf.read(input_file_path, dtype="float32")
        except Exception as exc:
            logger.warning("Failed to decode audio file %s: %s", input_file_path, exc)
            raise AudioFormatError(f"Unable to decode audio file: {exc}") from exc

        if audio_data is None or len(audio_data) == 0:
            raise AudioFormatError("Audio file contains no decodable audio frames")

        original_shape = audio_data.shape
        num_channels = original_shape[1] if audio_data.ndim > 1 else 1

        # 1. Convert to Mono if multi-channel
        if audio_data.ndim > 1:
            logger.debug("Converting %d-channel audio to mono", num_channels)
            audio_data = np.mean(audio_data, axis=-1)

        # 2. Resample to target sample rate (16000 Hz) if needed
        if sample_rate != self.target_sample_rate:
            logger.debug(
                "Resampling audio from %d Hz to %d Hz",
                sample_rate,
                self.target_sample_rate,
            )
            try:
                gcd = math.gcd(sample_rate, self.target_sample_rate)
                up = self.target_sample_rate // gcd
                down = sample_rate // gcd
                audio_data = scipy.signal.resample_poly(audio_data, up, down)
            except Exception as exc:
                logger.exception("Failed to resample audio: %s", exc)
                raise AudioProcessingError(f"Audio resampling failed: {exc}") from exc
            processed_sample_rate = self.target_sample_rate
        else:
            processed_sample_rate = sample_rate

        # 3. Apply background noise reduction using spectral gating
        logger.info(
            "Starting spectral noise reduction (samples=%d, sr=%d, prop_decrease=%.2f)",
            len(audio_data),
            processed_sample_rate,
            self.prop_decrease,
        )
        try:
            cleaned_audio = nr.reduce_noise(
                y=audio_data,
                sr=processed_sample_rate,
                stationary=True,
                prop_decrease=self.prop_decrease,
            )
        except Exception as exc:
            logger.exception("Noise reduction algorithm failed: %s", exc)
            raise AudioProcessingError(f"Noise reduction processing failed: {exc}") from exc

        # 4. Normalize / clip to prevent distortion and clipping when converting to PCM 16-bit
        max_amplitude = np.max(np.abs(cleaned_audio)) if len(cleaned_audio) > 0 else 0.0
        if max_amplitude > 1.0:
            logger.debug("Audio peak exceeded 1.0 (peak=%.3f), normalizing", max_amplitude)
            cleaned_audio = cleaned_audio / max_amplitude
        else:
            cleaned_audio = np.clip(cleaned_audio, -1.0, 1.0)

        # 5. Save output as 16 kHz Mono PCM 16-bit WAV
        try:
            sf.write(
                output_file_path,
                cleaned_audio,
                samplerate=self.target_sample_rate,
                subtype="PCM_16",
                format="WAV",
            )
        except Exception as exc:
            logger.exception("Failed to save cleaned WAV file %s: %s", output_file_path, exc)
            raise AudioProcessingError(f"Failed to write output audio file: {exc}") from exc

        output_size = os.path.getsize(output_file_path) if os.path.exists(output_file_path) else 0
        duration_seconds = len(cleaned_audio) / self.target_sample_rate

        logger.info(
            "Noise reduction completed successfully: duration=%.2fs, output_size=%d bytes",
            duration_seconds,
            output_size,
        )

        return {
            "input_sample_rate": sample_rate,
            "output_sample_rate": self.target_sample_rate,
            "input_channels": num_channels,
            "output_channels": 1,
            "duration_seconds": duration_seconds,
            "output_size_bytes": output_size,
        }


# Singleton service instance
noise_reduction_service = AudioNoiseReductionService()
