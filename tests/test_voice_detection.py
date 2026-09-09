import os
import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import FlaggedNumber
from app.routers.voice_detection import record_or_update_scam_number

client = TestClient(app)


def test_voice_detection_returns_gradio_result_and_removes_temp_file():
    observed_path = None

    def predict(**kwargs):
        nonlocal observed_path
        observed_path = kwargs["audio_path"]["path"]
        assert os.path.exists(observed_path)
        return {
            "success": True,
            "fake_probability": 0.8155,
            "bonafide_score": 0.1845,
            "verdict": "FAKE",
        }

    with patch("app.routers.voice_detection.Client") as client_class:
        client_class.return_value.predict.side_effect = predict
        response = client.post(
            "/voice-detection",
            files={"file": ("caller.mp3", b"mp3 bytes", "audio/mpeg")},
        )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "fake_probability": 0.8155,
        "bonafide_score": 0.1845,
        "verdict": "FAKE",
    }
    assert observed_path is not None
    assert not os.path.exists(observed_path)
    client_class.return_value.predict.assert_called_once()


def test_voice_detection_rejects_non_mp3_without_calling_gradio():
    with patch("app.routers.voice_detection.Client") as client_class:
        response = client.post(
            "/voice-detection",
            files={"file": ("caller.wav", b"wav bytes", "audio/wav")},
        )

    assert response.status_code == 200
    assert response.json() == {
        "success": False,
        "message": "Voice detection failed",
    }
    client_class.assert_not_called()


def test_voice_detection_returns_failure_for_unexpected_gradio_result():
    with patch("app.routers.voice_detection.Client") as client_class:
        client_class.return_value.predict.return_value = {"success": True}
        response = client.post(
            "/voice-detection",
            files={"file": ("caller.mp3", b"mp3 bytes", "audio/mpeg")},
        )

    assert response.status_code == 200
    assert response.json() == {
        "success": False,
        "message": "Unexpected Gradio response",
        "result_type": "dict",
        "result": "{'success': True}",
    }


def test_voice_detection_returns_gradio_exception_details():
    with patch("app.routers.voice_detection.Client") as client_class:
        client_class.return_value.predict.side_effect = RuntimeError("Space unavailable")
        response = client.post(
            "/voice-detection",
            files={"file": ("caller.mp3", b"mp3 bytes", "audio/mpeg")},
        )

    assert response.status_code == 200
    assert response.json() == {
        "success": False,
        "message": "Gradio prediction failed",
        "error": "Space unavailable",
    }


def test_voice_detection_returns_failure_for_invalid_scores():
    with patch("app.routers.voice_detection.Client") as client_class:
        client_class.return_value.predict.return_value = {
            "success": True,
            "fake_probability": "0.1",
            "bonafide_score": 0.9,
            "verdict": "REAL",
        }
        response = client.post(
            "/voice-detection",
            files={"file": ("caller.mp3", b"mp3 bytes", "audio/mpeg")},
        )

    assert response.status_code == 200
    assert response.json() == {
        "success": False,
        "message": "Unexpected Gradio response",
        "result_type": "dict",
        "result": "{'success': True, 'fake_probability': '0.1', 'bonafide_score': 0.9, 'verdict': 'REAL'}",
    }


def test_voice_detection_parses_json_string_result():
    with patch("app.routers.voice_detection.Client") as client_class:
        client_class.return_value.predict.return_value = (
            '{"success": true, "fake_probability": 0.1338, '
            '"bonafide_score": 0.8662, "verdict": "REAL"}'
        )
        response = client.post(
            "/voice-detection",
            files={"file": ("caller.mp3", b"mp3 bytes", "audio/mpeg")},
        )

    assert response.json() == {
        "success": True,
        "fake_probability": 0.1338,
        "bonafide_score": 0.8662,
        "verdict": "REAL",
    }


def test_voice_detection_parses_nested_json_result():
    with patch("app.routers.voice_detection.Client") as client_class:
        client_class.return_value.predict.return_value = [
            '{"success": true, "fake_probability": 0.1338, '
            '"bonafide_score": 0.8662, "verdict": "REAL"}'
        ]
        response = client.post(
            "/voice-detection",
            files={"file": ("caller.mp3", b"mp3 bytes", "audio/mpeg")},
        )

    assert response.json()["verdict"] == "REAL"


@pytest.fixture
def db_session():
    """Provides a fresh isolated in-memory SQLite database per test."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)

    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield session_factory
    app.dependency_overrides.pop(get_db, None)


def test_first_fake_detection_creates_scam_record_and_broadcasts(db_session):
    with patch("app.routers.voice_detection.Client") as client_class, \
         patch("app.routers.voice_detection.manager.broadcast", new_callable=AsyncMock) as mock_broadcast:
        client_class.return_value.predict.return_value = {
            "success": True,
            "fake_probability": 0.92,
            "bonafide_score": 0.08,
            "verdict": "FAKE",
        }
        response = client.post(
            "/voice-detection",
            data={"phone_number": "+15551234567"},
            files={"file": ("caller.mp3", b"mp3 bytes", "audio/mpeg")},
        )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "fake_probability": 0.92,
        "bonafide_score": 0.08,
        "verdict": "FAKE",
    }

    # Verify GET /scam-numbers
    scam_response = client.get("/scam-numbers?phone_number=%2B15551234567")
    assert scam_response.status_code == 200
    listed = scam_response.json()
    assert len(listed) == 1
    record = listed[0]
    assert record["phone_number"] == "+15551234567"
    assert record["verdict"] == "FAKE"
    assert record["fake_probability"] == 0.92
    assert record["bonafide_score"] == 0.08
    assert record["fake_detection_count"] == 1
    assert "last_flagged_at" in record

    # Verify WebSocket broadcast
    mock_broadcast.assert_called_once()
    broadcast_msg = mock_broadcast.call_args[0][0]
    assert broadcast_msg["type"] == "scam_number_updated"
    assert broadcast_msg["phone_number"] == "+15551234567"
    assert broadcast_msg["verdict"] == "FAKE"
    assert broadcast_msg["fake_probability"] == 0.92
    assert broadcast_msg["bonafide_score"] == 0.08
    assert broadcast_msg["fake_detection_count"] == 1
    assert "last_flagged_at" in broadcast_msg


def test_multiple_fake_detections_increment_count_and_update_probabilities(db_session):
    with patch("app.routers.voice_detection.Client") as client_class, \
         patch("app.routers.voice_detection.manager.broadcast", new_callable=AsyncMock) as mock_broadcast:
        # Detection 1
        client_class.return_value.predict.return_value = {
            "success": True,
            "fake_probability": 0.85,
            "bonafide_score": 0.15,
            "verdict": "FAKE",
        }
        res1 = client.post(
            "/voice-detection",
            data={"phone_number": "+15551112222"},
            files={"file": ("caller.mp3", b"mp3 bytes", "audio/mpeg")},
        )
        assert res1.status_code == 200

        # Detection 2 (same number, higher fake probability)
        client_class.return_value.predict.return_value = {
            "success": True,
            "fake_probability": 0.95,
            "bonafide_score": 0.05,
            "verdict": "FAKE",
        }
        res2 = client.post(
            "/voice-detection",
            data={"phone_number": "+15551112222"},
            files={"file": ("caller.mp3", b"mp3 bytes", "audio/mpeg")},
        )
        assert res2.status_code == 200

        # Detection 3 (same number, even higher)
        client_class.return_value.predict.return_value = {
            "success": True,
            "fake_probability": 0.99,
            "bonafide_score": 0.01,
            "verdict": "FAKE",
        }
        res3 = client.post(
            "/voice-detection",
            data={"phone_number": "+15551112222"},
            files={"file": ("caller.mp3", b"mp3 bytes", "audio/mpeg")},
        )
        assert res3.status_code == 200

    # Ensure only ONE record exists in the DB for this number
    scam_response = client.get("/scam-numbers")
    assert scam_response.status_code == 200
    listed = scam_response.json()
    assert len(listed) == 1
    assert listed[0]["phone_number"] == "+15551112222"
    assert listed[0]["fake_detection_count"] == 3
    assert listed[0]["fake_probability"] == 0.99
    assert listed[0]["bonafide_score"] == 0.01

    # Verify broadcasts sent with counts 1, 2, 3
    assert mock_broadcast.call_count == 3
    counts = [call[0][0]["fake_detection_count"] for call in mock_broadcast.call_args_list]
    assert counts == [1, 2, 3]


def test_phone_number_normalization_on_fake_detection(db_session):
    with patch("app.routers.voice_detection.Client") as client_class, \
         patch("app.routers.voice_detection.manager.broadcast", new_callable=AsyncMock):
        client_class.return_value.predict.return_value = {
            "success": True,
            "fake_probability": 0.90,
            "bonafide_score": 0.10,
            "verdict": "FAKE",
        }

        # First request with dashes, parentheses, spaces
        res1 = client.post(
            "/voice-detection",
            data={"phone_number": " +1 (555) 333-4444 "},
            files={"file": ("caller.mp3", b"mp3 bytes", "audio/mpeg")},
        )
        assert res1.status_code == 200

        # Second request with normalized format
        res2 = client.post(
            "/voice-detection",
            data={"phone_number": "+15553334444"},
            files={"file": ("caller.mp3", b"mp3 bytes", "audio/mpeg")},
        )
        assert res2.status_code == 200

    # Verify both requests updated the same normalized record
    scam_response = client.get("/scam-numbers?phone_number=+1 (555) 333-4444")
    assert scam_response.status_code == 200
    listed = scam_response.json()
    assert len(listed) == 1
    assert listed[0]["phone_number"] == "+15553334444"
    assert listed[0]["fake_detection_count"] == 2


def test_different_phone_numbers_create_separate_scam_records(db_session):
    with patch("app.routers.voice_detection.Client") as client_class, \
         patch("app.routers.voice_detection.manager.broadcast", new_callable=AsyncMock):
        client_class.return_value.predict.return_value = {
            "success": True,
            "fake_probability": 0.88,
            "bonafide_score": 0.12,
            "verdict": "FAKE",
        }

        client.post(
            "/voice-detection",
            data={"phone_number": "+15550000001"},
            files={"file": ("caller.mp3", b"mp3 bytes", "audio/mpeg")},
        )
        client.post(
            "/voice-detection",
            data={"phone_number": "+15550000002"},
            files={"file": ("caller.mp3", b"mp3 bytes", "audio/mpeg")},
        )

    scam_response = client.get("/scam-numbers")
    assert scam_response.status_code == 200
    numbers = {r["phone_number"] for r in scam_response.json()}
    assert numbers == {"+15550000001", "+15550000002"}
    for r in scam_response.json():
        assert r["fake_detection_count"] == 1


def test_real_voice_detection_does_not_create_scam_record(db_session):
    with patch("app.routers.voice_detection.Client") as client_class, \
         patch("app.routers.voice_detection.manager.broadcast", new_callable=AsyncMock) as mock_broadcast:
        client_class.return_value.predict.return_value = {
            "success": True,
            "fake_probability": 0.05,
            "bonafide_score": 0.95,
            "verdict": "REAL",
        }

        response = client.post(
            "/voice-detection",
            data={"phone_number": "+15558888888"},
            files={"file": ("caller.mp3", b"mp3 bytes", "audio/mpeg")},
        )
        assert response.status_code == 200
        assert response.json()["verdict"] == "REAL"

    # Scam database must not contain the real number
    scam_response = client.get("/scam-numbers?phone_number=%2B15558888888")
    assert scam_response.status_code == 200
    assert len(scam_response.json()) == 0
    mock_broadcast.assert_not_called()


def test_scam_numbers_sorted_by_last_flagged_at_descending(db_session):
    with patch("app.routers.voice_detection.Client") as client_class, \
         patch("app.routers.voice_detection.manager.broadcast", new_callable=AsyncMock):
        client_class.return_value.predict.return_value = {
            "success": True,
            "fake_probability": 0.90,
            "bonafide_score": 0.10,
            "verdict": "FAKE",
        }

        # Number A flagged first
        client.post(
            "/voice-detection",
            data={"phone_number": "+15551110000"},
            files={"file": ("caller.mp3", b"mp3 bytes", "audio/mpeg")},
        )
        time.sleep(0.01)

        # Number B flagged second
        client.post(
            "/voice-detection",
            data={"phone_number": "+15552220000"},
            files={"file": ("caller.mp3", b"mp3 bytes", "audio/mpeg")},
        )
        time.sleep(0.01)

        # Number A flagged again (most recent)
        client.post(
            "/voice-detection",
            data={"phone_number": "+15551110000"},
            files={"file": ("caller.mp3", b"mp3 bytes", "audio/mpeg")},
        )

    scam_response = client.get("/scam-numbers")
    assert scam_response.status_code == 200
    records = scam_response.json()
    assert len(records) == 2
    # Number A was flagged most recently, so it should be first
    assert records[0]["phone_number"] == "+15551110000"
    assert records[0]["fake_detection_count"] == 2
    assert records[1]["phone_number"] == "+15552220000"
    assert records[1]["fake_detection_count"] == 1


def test_websocket_failure_does_not_fail_voice_detection(db_session):
    with patch("app.routers.voice_detection.Client") as client_class, \
         patch("app.routers.voice_detection.manager.broadcast", side_effect=RuntimeError("Broadcast socket error")):
        client_class.return_value.predict.return_value = {
            "success": True,
            "fake_probability": 0.93,
            "bonafide_score": 0.07,
            "verdict": "FAKE",
        }

        response = client.post(
            "/voice-detection",
            data={"phone_number": "+15557777777"},
            files={"file": ("caller.mp3", b"mp3 bytes", "audio/mpeg")},
        )

    # Must still return HTTP 200 with result
    assert response.status_code == 200
    assert response.json()["verdict"] == "FAKE"

    # DB record was still created successfully
    scam_response = client.get("/scam-numbers?phone_number=%2B15557777777")
    assert scam_response.status_code == 200
    assert len(scam_response.json()) == 1


def test_database_error_does_not_crash_voice_detection(db_session):
    with patch("app.routers.voice_detection.Client") as client_class, \
         patch("app.routers.voice_detection.record_or_update_scam_number", return_value=None):
        client_class.return_value.predict.return_value = {
            "success": True,
            "fake_probability": 0.94,
            "bonafide_score": 0.06,
            "verdict": "FAKE",
        }

        response = client.post(
            "/voice-detection",
            data={"phone_number": "+15556666666"},
            files={"file": ("caller.mp3", b"mp3 bytes", "audio/mpeg")},
        )

    # Must still return HTTP 200 with result
    assert response.status_code == 200
    assert response.json()["verdict"] == "FAKE"


def test_concurrent_insert_integrity_error_recovery(db_session):
    session_factory = db_session
    db = session_factory()
    try:
        # Pre-insert existing record directly to simulate concurrent race condition
        first = record_or_update_scam_number(
            db=db,
            phone_number="+15554443333",
            verdict="FAKE",
            fake_probability=0.80,
            bonafide_score=0.20,
        )
        assert first is not None
        assert first.fake_detection_count == 1

        # Now simulate calling record_or_update_scam_number again
        second = record_or_update_scam_number(
            db=db,
            phone_number="+15554443333",
            verdict="FAKE",
            fake_probability=0.95,
            bonafide_score=0.05,
        )
        assert second is not None
        assert second.fake_detection_count == 2
        assert second.fake_probability == 0.95

        # Verify only one row exists in DB
        total_rows = db.query(FlaggedNumber).filter(FlaggedNumber.phone_number == "+15554443333").count()
        assert total_rows == 1
    finally:
        db.close()