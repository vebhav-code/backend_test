import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship

from app.database import Base


def get_utc_now():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)
    phone_number = Column(String, unique=True, index=True, nullable=False)
    username = Column(String, unique=True, index=True, nullable=True)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)

    contacts = relationship(
        "Contact",
        foreign_keys="[Contact.user_id]",
        back_populates="user",
        cascade="all, delete-orphan",
    )


class Contact(Base):
    __tablename__ = "contacts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    contact_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "contact_id", name="uq_user_contact"),
    )

    user = relationship("User", foreign_keys=[user_id], back_populates="contacts")
    contact = relationship("User", foreign_keys=[contact_id])


class FlaggedNumber(Base):
    __tablename__ = "flagged_numbers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    phone_number = Column(String, unique=True, index=True, nullable=False)
    verdict = Column(String, nullable=False)
    fake_probability = Column(Float, nullable=False)
    bonafide_score = Column(Float, nullable=False)
    fake_detection_count = Column(Integer, nullable=False, default=1)
    source = Column(String, nullable=False, default="voice_detection")
    last_flagged_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("phone_number", name="uq_flagged_numbers_phone_number"),
    )

    @property
    def flagged_at(self):
        return self.last_flagged_at

    @flagged_at.setter
    def flagged_at(self, value):
        self.last_flagged_at = value

