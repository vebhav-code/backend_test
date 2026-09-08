import math
import json
import logging
import os
import re
import tempfile
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from gradio_client import Client, handle_file
from sqlalchemy.orm import Session

from app.database import get_db
from app.connection_manager import manager
from app.models import FlaggedNumber
from app.schemas import FlaggedNumberResponse

router = APIRouter(tags=["Voice Detection"])
logger = logging.getLogger(__name__)


def _failure_response() -> Dict[str, Any]:
    return {"success": False, "message": "Voice detection failed"}


def _unexpected_response(result: Any) -> Dict[str, Any]:
    return {
        "success": False,
        "message": "Unexpected Gradio response",
        "result_type": type(result).__name__,
        "result": repr(result),
    }


def _validate_result(result: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(result, dict) or result.get("success") is not True:
        return None

    verdict = result.get("verdict")
    if not isinstance(verdict, str) or verdict not in {"REAL", "FAKE"}:
        return None

    scores = (result.get("fake_probability"), result.get("bonafide_score"))
    if any(
        isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not math.isfinite(score)
        for score in scores
    ):
        return None

    return {
        "success": True,
        "fake_probability": result["fake_probability"],
        "bonafide_score": result["bonafide_score"],
        "verdict": result["verdict"],
    }


def _parse_result(value: Any, depth: int = 0) -> Optional[Dict[str, Any]]:
    if depth > 4:
        return None

    validated = _validate_result(value)
    if validated is not None:
        return validated

    if isinstance(value, str):
        try:
            return _parse_result(json.loads(value), depth + 1)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    if isinstance(value, (list, tuple)):
        for item in value:
            parsed = _parse_result(item, depth + 1)
            if parsed is not None:
                return parsed
        return None

    if isinstance(value, dict):
        for item in value.values():
            parsed = _parse_result(item, depth + 1)
            if parsed is not None:
                return parsed
    return None


@router.post("/voice-detection")
async def detect_voice(
    file: Optional[UploadFile] = File(None),
    phone_number: Optional[str] = Form(None),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    if file is None:
        return _failure_response()

    uploaded_filename = file.filename
    temporary_path = None
    result = None
    try:
        audio_bytes = file.file.read()
        logger.info(
            "Voice detection upload: filename=%r content_type=%r size=%d",
            uploaded_filename,
            file.content_type,
            len(audio_bytes),
        )
        if (
            not uploaded_filename
            or not uploaded_filename.lower().endswith(".mp3")
            or not audio_bytes
        ):
            return _failure_response()

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temporary_file:
            temporary_file.write(audio_bytes)
            temporary_path = temporary_file.name
        temporary_size = os.path.getsize(temporary_path)
        logger.info(
            "Voice detection temporary file: path=%r exists=%s size=%d",
            temporary_path,
            os.path.exists(temporary_path),
            temporary_size,
        )
        if temporary_size == 0:
            return _failure_response()

        try:
            logger.info("Initializing Gradio Client for Juek/AI_Voice_Detection")
            client = Client("Juek/AI_Voice_Detection")
            if hasattr(client, "view_api"):
                try:
                    api_info = client.view_api(return_format="dict")
                    logger.info("Gradio API information: %r", api_info)
                except Exception:
                    logger.exception("Unable to retrieve Gradio API information")

            result = client.predict(
                audio_path=handle_file(temporary_path),
                api_name="/detect_voice",
            )
        except Exception as exc:
            logger.exception(
                "Gradio prediction failed: filename=%r uploaded_size=%d "
                "temporary_path=%r temporary_size=%s result_type=%s result=%r "
                "exception_type=%s exception_message=%s",
                uploaded_filename,
                len(audio_bytes),
                temporary_path,
                temporary_size,
                type(result).__name__,
                result,
                type(exc).__name__,
                str(exc),
            )
            return {
                "success": False,
                "message": "Gradio prediction failed",
                "error": str(exc),
            }

        print("GRADIO RESULT TYPE:", type(result))
        print("GRADIO RESULT:", repr(result))
        logger.info(
            "Voice detection Gradio result: type=%s repr=%r",
            type(result).__name__,
            result,
        )
        parsed_result = _parse_result(result)
        if parsed_result is not None and parsed_result["verdict"] == "FAKE" and phone_number:
            clean_phone_number = phone_number.strip()
            if re.fullmatch(r"\+?[0-9]{7,15}", clean_phone_number):
                try:
                    flagged_number = FlaggedNumber(
                        phone_number=clean_phone_number,
                        verdict=parsed_result["verdict"],
                        fake_probability=parsed_result["fake_probability"],
                        bonafide_score=parsed_result["bonafide_score"],
                        source="voice_detection",
                    )
                    db.add(flagged_number)
                    db.commit()
                    logger.info(
                        "Persisted flagged number: phone_number=%r verdict=%s",
                        clean_phone_number,
                        parsed_result["verdict"],
                    )
                    try:
                        await manager.broadcast(
                            {
                                "type": "scam_number_flagged",
                                "phone_number": clean_phone_number,
                                "verdict": parsed_result["verdict"],
                                "fake_probability": parsed_result["fake_probability"],
                                "bonafide_score": parsed_result["bonafide_score"],
                                "flagged_at": flagged_number.flagged_at.isoformat(),
                            }
                        )
                    except Exception:
                        logger.exception(
                            "Failed to broadcast flagged number: phone_number=%r",
                            clean_phone_number,
                        )
                except Exception:
                    db.rollback()
                    logger.exception(
                        "Failed to persist flagged number: phone_number=%r",
                        clean_phone_number,
                    )
            else:
                logger.warning("Ignoring invalid flagged phone number: %r", phone_number)
        return parsed_result or _unexpected_response(result)
    except Exception:
        logger.exception("Voice detection failed while calling Gradio")
        return _failure_response()
    finally:
        file.file.close()
        if temporary_path is not None:
            try:
                os.remove(temporary_path)
            except OSError:
                pass


@router.get("/scam-numbers", response_model=list[FlaggedNumberResponse])
def get_scam_numbers(
    limit: int = Query(50, ge=1, le=200),
    phone_number: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    query = db.query(FlaggedNumber)
    if phone_number:
        query = query.filter(FlaggedNumber.phone_number == phone_number.strip())
    return query.order_by(FlaggedNumber.flagged_at.desc()).limit(limit).all()