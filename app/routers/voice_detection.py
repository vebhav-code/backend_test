import math
import json
import logging
import os
import re
import tempfile
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from gradio_client import Client, handle_file
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.connection_manager import manager
from app.models import FlaggedNumber, get_utc_now
from app.schemas import FlaggedNumberResponse
from app.utils import normalize_phone_number

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


def record_or_update_scam_number(
    db: Session,
    phone_number: str,
    verdict: str,
    fake_probability: float,
    bonafide_score: float,
) -> Optional[FlaggedNumber]:
    """
    Finds or creates a unique scam record for the normalized phone number.
    - If new: creates a record with fake_detection_count=1.
    - If existing: increments fake_detection_count by 1, updates scores and last_flagged_at.
    - Concurrency-safe: handles IntegrityError by rolling back and retrying as update.
    - Fault-tolerant: rolls back on error and returns None without crashing the caller.
    """
    normalized_phone = normalize_phone_number(phone_number)
    if not normalized_phone:
        logger.warning("Ignoring invalid scam phone number: %r", phone_number)
        return None

    now = get_utc_now()
    try:
        candidates = [normalized_phone]
        if normalized_phone.startswith("+"):
            candidates.append(normalized_phone[1:])
        else:
            candidates.append(f"+{normalized_phone}")

        existing = (
            db.query(FlaggedNumber)
            .filter(FlaggedNumber.phone_number.in_(candidates))
            .first()
        )
        if existing:
            existing.fake_detection_count = FlaggedNumber.fake_detection_count + 1
            existing.verdict = verdict
            existing.fake_probability = fake_probability
            existing.bonafide_score = bonafide_score
            existing.last_flagged_at = now
            db.commit()
            db.refresh(existing)
            logger.info(
                "Updated existing scam number: phone_number=%r count=%d fake_prob=%.4f",
                existing.phone_number,
                existing.fake_detection_count,
                fake_probability,
            )
            return existing

        new_record = FlaggedNumber(
            phone_number=normalized_phone,
            verdict=verdict,
            fake_probability=fake_probability,
            bonafide_score=bonafide_score,
            fake_detection_count=1,
            source="voice_detection",
            last_flagged_at=now,
        )
        try:
            db.add(new_record)
            db.commit()
            db.refresh(new_record)
            logger.info(
                "Created new scam number: phone_number=%r count=1 fake_prob=%.4f",
                normalized_phone,
                fake_probability,
            )
            return new_record
        except IntegrityError:
            # Race condition: concurrent insert for the same phone number
            db.rollback()
            logger.info(
                "Concurrent insert conflict for phone_number=%r, retrying update",
                normalized_phone,
            )
            existing = (
                db.query(FlaggedNumber)
                .filter(FlaggedNumber.phone_number.in_(candidates))
                .first()
            )
            if existing:
                existing.fake_detection_count = FlaggedNumber.fake_detection_count + 1
                existing.verdict = verdict
                existing.fake_probability = fake_probability
                existing.bonafide_score = bonafide_score
                existing.last_flagged_at = now
                db.commit()
                db.refresh(existing)
                logger.info(
                    "Updated scam number after concurrent insert: phone_number=%r count=%d",
                    existing.phone_number,
                    existing.fake_detection_count,
                )
                return existing
            logger.error("Failed to recover from concurrent insert for %r", normalized_phone)
            return None

    except Exception:
        db.rollback()
        logger.exception("Failed to persist/update scam number: %r", normalized_phone)
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
        if parsed_result is not None:
            verdict = parsed_result.get("verdict")
            if verdict == "FAKE":
                logger.info(
                    "Fake voice detected: fake_probability=%.4f bonafide_score=%.4f phone_number=%r",
                    parsed_result["fake_probability"],
                    parsed_result["bonafide_score"],
                    phone_number,
                )
                if phone_number:
                    scam_record = record_or_update_scam_number(
                        db=db,
                        phone_number=phone_number,
                        verdict=verdict,
                        fake_probability=parsed_result["fake_probability"],
                        bonafide_score=parsed_result["bonafide_score"],
                    )
                    if scam_record is not None:
                        try:
                            await manager.broadcast(
                                {
                                    "type": "scam_number_updated",
                                    "phone_number": scam_record.phone_number,
                                    "verdict": scam_record.verdict,
                                    "fake_probability": scam_record.fake_probability,
                                    "bonafide_score": scam_record.bonafide_score,
                                    "fake_detection_count": scam_record.fake_detection_count,
                                    "last_flagged_at": scam_record.last_flagged_at.isoformat(),
                                }
                            )
                            logger.info(
                                "Broadcast scam_number_updated: phone_number=%r count=%d",
                                scam_record.phone_number,
                                scam_record.fake_detection_count,
                            )
                        except Exception:
                            logger.exception(
                                "WebSocket broadcast failure for scam number: phone_number=%r",
                                scam_record.phone_number,
                            )
            elif verdict == "REAL":
                logger.info(
                    "Bonafide (REAL) voice detected: fake_probability=%.4f bonafide_score=%.4f phone_number=%r",
                    parsed_result["fake_probability"],
                    parsed_result["bonafide_score"],
                    phone_number,
                )
                # REAL voice: Do not add number to scam list

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


@router.get("/scam-numbers", response_model=List[FlaggedNumberResponse])
def get_scam_numbers(
    limit: int = Query(50, ge=1, le=200),
    phone_number: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    query = db.query(FlaggedNumber)
    if phone_number:
        clean = phone_number.strip()
        normalized = normalize_phone_number(clean) or clean
        candidates = {normalized, f"+{normalized.lstrip('+')}", normalized.lstrip("+")}
        query = query.filter(FlaggedNumber.phone_number.in_(candidates))
    return query.order_by(FlaggedNumber.last_flagged_at.desc()).limit(limit).all()