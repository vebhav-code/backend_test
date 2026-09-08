from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.connection_manager import manager
from app.database import get_db
from app.models import User
from app.schemas import UserCreate, UserResponse, UserSearchResult

router = APIRouter(prefix="/users", tags=["Users"])
login_router = APIRouter(tags=["Users"])


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=UserResponse, status_code=status.HTTP_201_CREATED, include_in_schema=False)
def create_user(user_in: UserCreate, db: Session = Depends(get_db)):
    """
    Register a new user with a display name and unique phone number.
    Returns HTTP 409 Conflict if the phone number is already registered.
    """
    clean_name = user_in.name.strip()
    clean_phone_number = user_in.phone_number.strip()

    existing_user = db.query(User).filter(User.phone_number == clean_phone_number).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Phone number '{clean_phone_number}' already exists",
        )

    clean_username = user_in.username.strip().lower() if user_in.username else None
    new_user = User(
        name=clean_name,
        phone_number=clean_phone_number,
        username=clean_username,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    response_data = UserResponse.model_validate(new_user)
    response_data.is_online = manager.is_online(new_user.id)
    return response_data


@router.get("/by-phone/{phone_number}", response_model=UserResponse)
def get_user_by_phone(phone_number: str, db: Session = Depends(get_db)):
    clean_phone_number = phone_number.strip()
    user = db.query(User).filter(User.phone_number == clean_phone_number).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with phone number '{clean_phone_number}' not found",
        )

    response_data = UserResponse.model_validate(user)
    response_data.is_online = manager.is_online(user.id)
    return response_data


@login_router.post("/login", response_model=UserResponse)
def login_user(user_in: dict, db: Session = Depends(get_db)):
    phone_number = user_in.get("phone_number", "").strip()
    user = db.query(User).filter(User.phone_number == phone_number).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with phone number '{phone_number}' not found",
        )

    response_data = UserResponse.model_validate(user)
    response_data.is_online = manager.is_online(user.id)
    return response_data


@router.get("/search", response_model=List[UserSearchResult])
def search_users(
    q: str = Query(..., min_length=1, description="Search term for name or phone number"),
    user_id: Optional[str] = Query(None, description="Optional requesting user ID to exclude from results"),
    db: Session = Depends(get_db),
):
    """
    Search for users with case-insensitive partial match on name, username, or phone number.
    Excludes the requesting user if user_id is provided.
    """
    term = f"%{q.strip().lower()}%"
    query = db.query(User).filter(
        (User.name.ilike(term))
        | (User.username.ilike(term))
        | (User.phone_number.ilike(term))
    )

    if user_id:
        query = query.filter(User.id != user_id.strip())

    users = query.all()

    results: List[UserSearchResult] = []
    for u in users:
        item = UserSearchResult(
            id=u.id,
            name=u.name,
            phone_number=u.phone_number,
            username=u.username,
            is_online=manager.is_online(u.id),
        )
        results.append(item)

    return results


@router.get("/{id}", response_model=UserResponse)
def get_user(id: str, db: Session = Depends(get_db)):
    """
    Return full user details by ID, or HTTP 404 if not found.
    """
    user = db.query(User).filter(User.id == id.strip()).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID '{id}' not found",
        )

    response_data = UserResponse.model_validate(user)
    response_data.is_online = manager.is_online(user.id)
    return response_data
