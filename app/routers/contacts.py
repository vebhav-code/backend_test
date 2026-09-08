from typing import List
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.connection_manager import manager
from app.database import get_db
from app.models import Contact, User
from app.schemas import ContactCreate, ContactResponse, UserSearchResult

router = APIRouter(prefix="/contacts", tags=["Contacts"])


@router.post("", response_model=ContactResponse, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=ContactResponse, status_code=status.HTTP_201_CREATED, include_in_schema=False)
def add_contact(payload: ContactCreate, db: Session = Depends(get_db)):
    """
    Add a new contact connection between user_id and contact_id.
    - Validates both users exist (404 if either not found).
    - Rejects self-addition (400).
    - Rejects duplicates (409).
    - Returns created contact joined with the contact's name and phone number.
    """
    user_id = payload.user_id.strip()
    contact_id = payload.contact_id.strip()

    # Reject self-add
    if user_id == contact_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot add yourself as a contact",
        )

    # Validate requesting user exists
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID '{user_id}' not found",
        )

    # Validate target contact user exists
    contact_user = db.query(User).filter(User.id == contact_id).first()
    if not contact_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Contact user with ID '{contact_id}' not found",
        )

    # Reject duplicate contact
    existing = (
        db.query(Contact)
        .filter(Contact.user_id == user_id, Contact.contact_id == contact_id)
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Contact already exists",
        )

    # Insert into Contact table
    new_contact = Contact(user_id=user_id, contact_id=contact_id)
    db.add(new_contact)
    try:
        db.commit()
        db.refresh(new_contact)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Contact already exists",
        )

    return ContactResponse(
        id=new_contact.id,
        user_id=new_contact.user_id,
        contact_id=new_contact.contact_id,
        name=contact_user.name,
        phone_number=contact_user.phone_number,
        username=contact_user.username,
        created_at=new_contact.created_at,
        is_online=manager.is_online(contact_user.id),
        contact=UserSearchResult(
            id=contact_user.id,
            name=contact_user.name,
            phone_number=contact_user.phone_number,
            username=contact_user.username,
            is_online=manager.is_online(contact_user.id),
        ),
    )


@router.get("/{user_id}", response_model=List[ContactResponse])
def get_user_contacts(user_id: str, db: Session = Depends(get_db)):
    """
    Return the user's contacts joined with the contact's name and phone number.
    Formatted for the Flutter home screen rendering.
    """
    clean_user_id = user_id.strip()
    user = db.query(User).filter(User.id == clean_user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID '{clean_user_id}' not found",
        )

    # Join Contact with User on Contact.contact_id == User.id
    results = (
        db.query(Contact, User)
        .join(User, Contact.contact_id == User.id)
        .filter(Contact.user_id == clean_user_id)
        .order_by(Contact.created_at.desc())
        .all()
    )

    contacts_list: List[ContactResponse] = []
    for contact_record, contact_user in results:
        contacts_list.append(
            ContactResponse(
                id=contact_record.id,
                user_id=contact_record.user_id,
                contact_id=contact_record.contact_id,
                name=contact_user.name,
                phone_number=contact_user.phone_number,
                username=contact_user.username,
                created_at=contact_record.created_at,
                is_online=manager.is_online(contact_user.id),
                contact=UserSearchResult(
                    id=contact_user.id,
                    name=contact_user.name,
                    phone_number=contact_user.phone_number,
                    username=contact_user.username,
                    is_online=manager.is_online(contact_user.id),
                ),
            )
        )

    return contacts_list


@router.delete("/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_contact(contact_id: int, db: Session = Depends(get_db)):
    """
    Delete a contact by the Contact table's own primary key ID.
    Returns HTTP 204 No Content.
    """
    contact = db.query(Contact).filter(Contact.id == contact_id).first()
    if not contact:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Contact with ID {contact_id} not found",
        )

    db.delete(contact)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
