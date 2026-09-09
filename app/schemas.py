from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class UserBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    phone_number: str = Field(..., min_length=7, max_length=16, pattern=r"^\+?[0-9]{7,15}$")
    username: Optional[str] = Field(None, min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_.-]+$")


class UserCreate(UserBase):
    pass


class UserResponse(UserBase):
    id: str
    created_at: datetime
    is_online: Optional[bool] = None

    model_config = ConfigDict(from_attributes=True)


class UserSearchResult(BaseModel):
    id: str
    name: str
    phone_number: str
    username: Optional[str] = None
    is_online: Optional[bool] = None

    model_config = ConfigDict(from_attributes=True)


class ContactCreate(BaseModel):
    user_id: str
    contact_id: str


class ContactResponse(BaseModel):
    id: int
    user_id: str
    contact_id: str
    name: str
    phone_number: str
    username: Optional[str] = None
    created_at: datetime
    is_online: Optional[bool] = None
    contact: Optional[UserSearchResult] = None

    model_config = ConfigDict(from_attributes=True)


class SignalingMessage(BaseModel):
    type: str
    to_user_id: Optional[str] = None
    call_id: Optional[str] = None
    sdp: Optional[Any] = None
    candidate: Optional[Any] = None
    reason: Optional[str] = None


class FlaggedNumberResponse(BaseModel):
    phone_number: str
    verdict: str
    fake_probability: float
    bonafide_score: float
    fake_detection_count: int = 1
    last_flagged_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ScamNumberUpdatedEvent(BaseModel):
    type: str = "scam_number_updated"
    phone_number: str
    verdict: str
    fake_probability: float
    bonafide_score: float
    fake_detection_count: int
    last_flagged_at: str


class RazorpayCreateOrderRequest(BaseModel):
    amount: int = Field(..., gt=0, description="Amount in the smallest currency unit (e.g. 9900 paise for ₹99)")
    currency: str = Field("INR", min_length=3, max_length=5, description="Currency code (e.g. INR)")

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        clean = v.strip().upper()
        if not clean.isalpha() or len(clean) != 3:
            raise ValueError("Currency must be a valid 3-letter currency code (e.g. INR)")
        return clean


class RazorpayCreateOrderResponse(BaseModel):
    success: bool
    order_id: str
    amount: int
    currency: str
    key_id: str


class RazorpayVerifyPaymentRequest(BaseModel):
    razorpay_payment_id: str = Field(..., min_length=1, description="Razorpay payment ID")
    razorpay_order_id: str = Field(..., min_length=1, description="Razorpay order ID")
    razorpay_signature: str = Field(..., min_length=1, description="Razorpay payment signature")

    @field_validator("razorpay_payment_id", "razorpay_order_id", "razorpay_signature")
    @classmethod
    def strip_whitespace(cls, v: str) -> str:
        clean = v.strip()
        if not clean:
            raise ValueError("Field cannot be empty")
        return clean


class RazorpayVerifyPaymentResponse(BaseModel):
    success: bool
    verified: bool
    message: str

