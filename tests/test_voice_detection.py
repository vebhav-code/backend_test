import os
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.database import Base, get_db
from app.main import app


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


def test_fake_voice_detection_persists_and_lists_flagged_number():
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
    try:
        with patch("app.routers.voice_detection.Client") as client_class:
            client_class.return_value.predict.return_value = {
                "success": True,
                "fake_probability": 0.91,
                "bonafide_score": 0.09,
                "verdict": "FAKE",
            }
            response = client.post(
                "/voice-detection",
                data={"phone_number": "+15551234567"},
                files={"file": ("caller.mp3", b"mp3 bytes", "audio/mpeg")},
            )

        assert response.json() == {
            "success": True,
            "fake_probability": 0.91,
            "bonafide_score": 0.09,
            "verdict": "FAKE",
        }

        scam_response = client.get("/scam-numbers?limit=5&phone_number=%2B15551234567")
        assert scam_response.status_code == 200
        listed = scam_response.json()
        assert len(listed) == 1
        assert listed[0]["phone_number"] == "+15551234567"
        assert listed[0]["verdict"] == "FAKE"
    finally:
        app.dependency_overrides.pop(get_db, None)