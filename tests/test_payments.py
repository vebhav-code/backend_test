import hashlib
import hmac
import os
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
import razorpay.errors

from app.main import app


class TestRazorpayPayments(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.test_key_id = "rzp_test_mockKey123"
        self.test_key_secret = "secret_super_secure_key_456"

    def test_missing_environment_variables_create_order(self):
        """Should safely return 500 without exposing secrets if env vars are missing."""
        with patch.dict(os.environ, {}, clear=True):
            response = self.client.post(
                "/payments/razorpay/create-order",
                json={"amount": 9900, "currency": "INR"},
            )
            self.assertEqual(response.status_code, 500)
            data = response.json()
            self.assertIn("detail", data)
            self.assertNotIn(self.test_key_secret, response.text)

    def test_missing_environment_variables_verify_payment(self):
        """Should safely return 500 without exposing secrets if env vars are missing."""
        with patch.dict(os.environ, {}, clear=True):
            response = self.client.post(
                "/payments/razorpay/verify-payment",
                json={
                    "razorpay_payment_id": "pay_123",
                    "razorpay_order_id": "order_123",
                    "razorpay_signature": "sig_123",
                },
            )
            self.assertEqual(response.status_code, 500)
            self.assertNotIn(self.test_key_secret, response.text)

    def test_create_order_validation_invalid_amounts(self):
        """Reject non-positive or invalid amounts."""
        with patch.dict(os.environ, {"RAZORPAY_KEY_ID": self.test_key_id, "RAZORPAY_KEY_SECRET": self.test_key_secret}):
            # Zero amount
            resp = self.client.post("/payments/razorpay/create-order", json={"amount": 0, "currency": "INR"})
            self.assertEqual(resp.status_code, 422)

            # Negative amount
            resp = self.client.post("/payments/razorpay/create-order", json={"amount": -500, "currency": "INR"})
            self.assertEqual(resp.status_code, 422)

            # Non-integer amount
            resp = self.client.post("/payments/razorpay/create-order", json={"amount": "invalid", "currency": "INR"})
            self.assertEqual(resp.status_code, 422)

    def test_create_order_validation_invalid_currency(self):
        """Reject invalid currency strings."""
        with patch.dict(os.environ, {"RAZORPAY_KEY_ID": self.test_key_id, "RAZORPAY_KEY_SECRET": self.test_key_secret}):
            resp = self.client.post("/payments/razorpay/create-order", json={"amount": 9900, "currency": "TOOLONG"})
            self.assertEqual(resp.status_code, 422)

            resp = self.client.post("/payments/razorpay/create-order", json={"amount": 9900, "currency": "12"})
            self.assertEqual(resp.status_code, 422)

    @patch("razorpay.Client")
    def test_create_order_success(self, mock_razorpay_client_cls):
        """Successfully create a Razorpay order in test mode."""
        mock_client = MagicMock()
        mock_razorpay_client_cls.return_value = mock_client
        mock_client.order.create.return_value = {
            "id": "order_test_123456",
            "entity": "order",
            "amount": 9900,
            "currency": "INR",
            "status": "created",
        }

        with patch.dict(os.environ, {"RAZORPAY_KEY_ID": self.test_key_id, "RAZORPAY_KEY_SECRET": self.test_key_secret}):
            response = self.client.post(
                "/payments/razorpay/create-order",
                json={"amount": 9900, "currency": "INR"},
            )

            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertTrue(data["success"])
            self.assertEqual(data["order_id"], "order_test_123456")
            self.assertEqual(data["amount"], 9900)
            self.assertEqual(data["currency"], "INR")
            self.assertEqual(data["key_id"], self.test_key_id)

            # Ensure secret key is NEVER exposed
            self.assertNotIn(self.test_key_secret, response.text)

            # Verify order creation payload
            mock_client.order.create.assert_called_once_with(
                data={"amount": 9900, "currency": "INR", "payment_capture": 1}
            )

    @patch("razorpay.Client")
    def test_create_order_logging(self, mock_razorpay_client_cls):
        """Verify safe debugging logs are emitted without leaking credentials."""
        mock_client = MagicMock()
        mock_razorpay_client_cls.return_value = mock_client
        mock_client.order.create.return_value = {
            "id": "order_log_test_789",
            "entity": "order",
            "amount": 9900,
            "currency": "INR",
            "status": "created",
        }

        with self.assertLogs("calling.payments", level="INFO") as cm:
            with patch.dict(os.environ, {"RAZORPAY_KEY_ID": self.test_key_id, "RAZORPAY_KEY_SECRET": self.test_key_secret}):
                response = self.client.post(
                    "/payments/razorpay/create-order",
                    json={"amount": 9900, "currency": "INR"},
                )
                self.assertEqual(response.status_code, 200)

            log_output = "\n".join(cm.output)
            # Check required logging elements
            self.assertIn("Incoming Razorpay create-order endpoint reached", log_output)
            self.assertIn("9900", log_output)
            self.assertIn("INR", log_output)
            self.assertIn("Razorpay order creation request started", log_output)
            self.assertIn("order_log_test_789", log_output)

            # Ensure secret is NOT logged
            self.assertNotIn(self.test_key_secret, log_output)

    @patch("razorpay.Client")
    def test_create_order_gateway_error(self, mock_razorpay_client_cls):
        """Handle Razorpay SDK gateway or connection error gracefully."""
        mock_client = MagicMock()
        mock_razorpay_client_cls.return_value = mock_client
        mock_client.order.create.side_effect = razorpay.errors.GatewayError("Connection timed out")

        with patch.dict(os.environ, {"RAZORPAY_KEY_ID": self.test_key_id, "RAZORPAY_KEY_SECRET": self.test_key_secret}):
            response = self.client.post(
                "/payments/razorpay/create-order",
                json={"amount": 9900, "currency": "INR"},
            )
            self.assertEqual(response.status_code, 502)
            self.assertNotIn(self.test_key_secret, response.text)

    def test_verify_payment_validation_empty_fields(self):
        """Reject empty or missing fields."""
        with patch.dict(os.environ, {"RAZORPAY_KEY_ID": self.test_key_id, "RAZORPAY_KEY_SECRET": self.test_key_secret}):
            # Missing payment ID
            resp = self.client.post(
                "/payments/razorpay/verify-payment",
                json={"razorpay_order_id": "order_1", "razorpay_signature": "sig_1"},
            )
            self.assertEqual(resp.status_code, 422)

            # Whitespace only
            resp = self.client.post(
                "/payments/razorpay/verify-payment",
                json={"razorpay_payment_id": "   ", "razorpay_order_id": "order_1", "razorpay_signature": "sig_1"},
            )
            self.assertEqual(resp.status_code, 422)

    def test_verify_payment_valid_signature(self):
        """Valid HMAC-SHA256 signature should return 200 with verified true."""
        order_id = "order_test_999"
        payment_id = "pay_test_888"
        msg = f"{order_id}|{payment_id}".encode("utf-8")
        valid_signature = hmac.new(self.test_key_secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()

        with patch.dict(os.environ, {"RAZORPAY_KEY_ID": self.test_key_id, "RAZORPAY_KEY_SECRET": self.test_key_secret}):
            response = self.client.post(
                "/payments/razorpay/verify-payment",
                json={
                    "razorpay_order_id": order_id,
                    "razorpay_payment_id": payment_id,
                    "razorpay_signature": valid_signature,
                },
            )

            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertTrue(data["success"])
            self.assertTrue(data["verified"])
            self.assertEqual(data["message"], "Payment verified successfully")

            # Verify secret is never exposed
            self.assertNotIn(self.test_key_secret, response.text)

    def test_verify_payment_invalid_signature_rejected(self):
        """Invalid signature should be rejected with HTTP 400."""
        order_id = "order_test_999"
        payment_id = "pay_test_888"
        invalid_signature = "invalid_fake_signature_hash_0000000000000000"

        with patch.dict(os.environ, {"RAZORPAY_KEY_ID": self.test_key_id, "RAZORPAY_KEY_SECRET": self.test_key_secret}):
            response = self.client.post(
                "/payments/razorpay/verify-payment",
                json={
                    "razorpay_order_id": order_id,
                    "razorpay_payment_id": payment_id,
                    "razorpay_signature": invalid_signature,
                },
            )

            self.assertEqual(response.status_code, 400)
            data = response.json()
            self.assertIn("detail", data)
            self.assertNotIn(self.test_key_secret, response.text)


if __name__ == "__main__":
    unittest.main()
