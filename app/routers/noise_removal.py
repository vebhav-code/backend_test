import logging
import os
import tempfile
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from app.services.audio_noise_reduction import (
    AudioFormatError,
    AudioProcessingError,
    noise_reduction_service,
)

router = APIRouter(tags=["Noise Removal"])
logger = logging.getLogger(__name__)

SUPPORTED_AUDIO_EXTENSIONS = {".wav"}
SUPPORTED_MIME_TYPES = {
    "audio/wav",
    "audio/x-wav",
    "audio/wave",
    "application/octet-stream",  # Often sent by mobile HTTP clients for binary files
}


def _cleanup_file(file_path: Optional[str]) -> None:
    """Safely delete a file if it exists without raising exceptions."""
    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
            logger.debug("Removed temporary file: %s", file_path)
        except OSError as exc:
            logger.warning("Failed to remove temporary file %s: %s", file_path, exc)


@router.post(
    "/remove-noise",
    response_class=FileResponse,
    responses={
        200: {
            "content": {"audio/wav": {}},
            "description": "Cleaned WAV audio file (16 kHz, Mono, PCM 16-bit)",
        },
        400: {"description": "Invalid audio file or unsupported format"},
        500: {"description": "Noise reduction processing failure"},
    },
)
async def remove_noise(
    file: Optional[UploadFile] = File(None),
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    """
    Accepts an uploaded WAV audio file, removes background noise, and
    returns a cleaned 16 kHz Mono PCM 16-bit WAV file.
    """
    if file is None or not file.filename:
        logger.warning("Noise removal request rejected: missing file payload")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An audio file must be provided in the 'file' field",
        )

    filename = file.filename
    _, ext = os.path.splitext(filename.lower())

    if ext not in SUPPORTED_AUDIO_EXTENSIONS:
        logger.warning(
            "Noise removal request rejected: unsupported extension %r for file %r",
            ext,
            filename,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file format '{ext}'. Only WAV audio files (.wav) are supported",
        )

    # Read uploaded file content
    try:
        audio_bytes = await file.read()
    except Exception as exc:
        logger.exception("Failed to read uploaded file %r: %s", filename, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to read uploaded file data",
        ) from exc
    finally:
        await file.close()

    original_size = len(audio_bytes)
    if original_size == 0:
        logger.warning("Noise removal request rejected: file %r is empty", filename)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded audio file is empty",
        )

    logger.info(
        "Audio received for noise removal: filename=%r, content_type=%r, size=%d bytes",
        filename,
        file.content_type,
        original_size,
    )

    input_temp_path = None
    output_temp_path = None

    try:
        # Save uploaded bytes to a temporary WAV file
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as in_f:
            in_f.write(audio_bytes)
            input_temp_path = in_f.name

        # Create path for output WAV file
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as out_f:
            output_temp_path = out_f.name

        logger.info(
            "Noise removal started for file=%r (input_temp=%s, output_temp=%s)",
            filename,
            input_temp_path,
            output_temp_path,
        )

        # Execute noise reduction
        stats = noise_reduction_service.clean_audio(
            input_file_path=input_temp_path,
            output_file_path=output_temp_path,
        )

        logger.info(
            "Noise removal completed for file=%r: output_size=%d bytes, duration=%.2fs",
            filename,
            stats["output_size_bytes"],
            stats["duration_seconds"],
        )

        # Clean up input temporary file immediately
        _cleanup_file(input_temp_path)
        input_temp_path = None

        # Schedule cleanup of output temporary file after response is streamed
        background_tasks.add_task(_cleanup_file, output_temp_path)

        return FileResponse(
            path=output_temp_path,
            media_type="audio/wav",
            filename="cleaned_audio.wav",
            background=background_tasks,
        )

    except AudioFormatError as exc:
        logger.warning("Invalid audio format for file %r: %s", filename, exc)
        _cleanup_file(input_temp_path)
        _cleanup_file(output_temp_path)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid audio file: {exc}",
        ) from exc

    except AudioProcessingError as exc:
        logger.exception("Noise reduction error for file %r: %s", filename, exc)
        _cleanup_file(input_temp_path)
        _cleanup_file(output_temp_path)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Noise reduction processing failed",
        ) from exc

    except Exception as exc:
        logger.exception("Unexpected error during noise removal for file %r: %s", filename, exc)
        _cleanup_file(input_temp_path)
        _cleanup_file(output_temp_path)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while processing audio",
        ) from exc
