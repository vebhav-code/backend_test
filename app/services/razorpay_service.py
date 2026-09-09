import logging
import os
from typing import Any, Dict, Tuple
from dotenv import load_dotenv
from fastapi import HTTPException, status
import razorpay
import razorpay.errors

load_dotenv()

logger = logging.getLogger("calling.payments")


def get_razorpay_client() -> Tuple[razorpay.Client, str]:
    """
    Initialize and return the Razorpay Client and public Key ID.
    Validates that RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET are set.
    Never exposes RAZORPAY_KEY_SECRET in logs or exceptions.
    """
    key_id = os.environ.get("RAZORPAY_KEY_ID")
    key_secret = os.environ.get("RAZORPAY_KEY_SECRET")

    if not key_id or not key_secret:
        logger.error(
            "Razorpay configuration error: RAZORPAY_KEY_ID or RAZORPAY_KEY_SECRET is missing."
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Payment service configuration error",
        )

    clean_key_id = key_id.strip()
    clean_key_secret = key_secret.strip()

    client = razorpay.Client(auth=(clean_key_id, clean_key_secret))
    return client, clean_key_id


def create_razorpay_order(amount: int, currency: str = "INR") -> Dict[str, Any]:
    """
    Create a Razorpay order in test/live mode according to configured credentials.
    Amount must be in smallest currency unit (e.g., paise for INR).
    """
    client, key_id = get_razorpay_client()

    order_payload = {
        "amount": amount,
        "currency": currency,
        "payment_capture": 1,
    }

    logger.info(
        "Razorpay order creation request started for amount=%d %s",
        amount,
        currency,
    )

    try:
        order = client.order.create(data=order_payload)
    except razorpay.errors.BadRequestError as exc:
        logger.warning("Razorpay order creation bad request: %s", str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid order request parameters",
        )
    except razorpay.errors.GatewayError as exc:
        logger.error("Razorpay gateway error: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Payment gateway communication error",
        )
    except razorpay.errors.ServerError as exc:
        logger.error("Razorpay server error: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Payment gateway returned server error",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Razorpay order creation unexpected error: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Payment gateway request failed",
        )

    order_id = order.get("id")
    if not order_id:
        logger.error("Razorpay order creation response missing 'id'")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Payment gateway returned invalid response",
        )

    logger.info(
        "Razorpay order successfully created: order_id=%s, amount=%s, currency=%s",
        order_id,
        order.get("amount", amount),
        order.get("currency", currency),
    )

    return {
        "order_id": order_id,
        "amount": int(order.get("amount", amount)),
        "currency": str(order.get("currency", currency)),
        "key_id": key_id,
    }


def verify_razorpay_payment(
    order_id: str,
    payment_id: str,
    signature: str,
) -> bool:
    """
    Verify the payment signature securely using the Razorpay Python SDK.
    Raises HTTPException(400) if signature is invalid.
    """
    client, _ = get_razorpay_client()

    params = {
        "razorpay_order_id": order_id,
        "razorpay_payment_id": payment_id,
        "razorpay_signature": signature,
    }

    logger.info(
        "Razorpay payment signature verification started for order_id=%s, payment_id=%s",
        order_id,
        payment_id,
    )

    try:
        result = client.utility.verify_payment_signature(params)
        if result is False:
            logger.warning(
                "Razorpay signature verification rejected: invalid signature for order_id=%s, payment_id=%s",
                order_id,
                payment_id,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Payment verification failed: Invalid signature",
            )
        logger.info(
            "Razorpay payment signature verified successfully for order_id=%s, payment_id=%s",
            order_id,
            payment_id,
        )
        return True
    except razorpay.errors.SignatureVerificationError:
        logger.warning(
            "Razorpay SignatureVerificationError for order_id=%s, payment_id=%s",
            order_id,
            payment_id,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payment verification failed: Invalid signature",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Razorpay payment verification unexpected error: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payment verification failed",
        )
