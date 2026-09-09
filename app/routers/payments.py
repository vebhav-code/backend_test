import logging
from fastapi import APIRouter, status

from app.schemas import (
    RazorpayCreateOrderRequest,
    RazorpayCreateOrderResponse,
    RazorpayVerifyPaymentRequest,
    RazorpayVerifyPaymentResponse,
)
from app.services.razorpay_service import create_razorpay_order, verify_razorpay_payment

logger = logging.getLogger("calling.payments")

router = APIRouter(prefix="/payments/razorpay", tags=["Payments"])


@router.post(
    "/create-order",
    response_model=RazorpayCreateOrderResponse,
    status_code=status.HTTP_200_OK,
    summary="Create Razorpay Order",
)
@router.post(
    "/create-order/",
    response_model=RazorpayCreateOrderResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
def create_order(payload: RazorpayCreateOrderRequest):
    """
    Create a Razorpay order in the smallest currency unit (e.g. paise for INR).
    Returns order details and public key_id for client checkout integration.
    """
    logger.info(
        "Incoming Razorpay create-order endpoint reached: amount=%d paise, currency=%s",
        payload.amount,
        payload.currency,
    )
    order_data = create_razorpay_order(
        amount=payload.amount,
        currency=payload.currency,
    )

    return RazorpayCreateOrderResponse(
        success=True,
        order_id=order_data["order_id"],
        amount=order_data["amount"],
        currency=order_data["currency"],
        key_id=order_data["key_id"],
    )


@router.post(
    "/verify-payment",
    response_model=RazorpayVerifyPaymentResponse,
    status_code=status.HTTP_200_OK,
    summary="Verify Razorpay Payment Signature",
)
@router.post(
    "/verify-payment/",
    response_model=RazorpayVerifyPaymentResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
def verify_payment(payload: RazorpayVerifyPaymentRequest):
    """
    Verify the payment signature returned by the Razorpay checkout.
    Uses HMAC-SHA256 signature verification via the official Razorpay SDK.
    """
    logger.info(
        "Incoming Razorpay verify-payment endpoint reached: order_id=%s, payment_id=%s",
        payload.razorpay_order_id,
        payload.razorpay_payment_id,
    )
    verify_razorpay_payment(
        order_id=payload.razorpay_order_id,
        payment_id=payload.razorpay_payment_id,
        signature=payload.razorpay_signature,
    )

    return RazorpayVerifyPaymentResponse(
        success=True,
        verified=True,
        message="Payment verified successfully",
    )
