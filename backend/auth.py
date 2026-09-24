import secrets
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from .database import get_db
from .models import LoginSession, User
from .security import verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginForm(BaseModel):
    username: str
    password: str

    @field_validator("username", "password")
    @classmethod
    def check_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Field cannot be empty")
        return v.strip()


def create_session(db: Session, user: User) -> LoginSession:
    token = secrets.token_urlsafe(32)
    session = LoginSession(user_id=user.id, token=token, valid=True)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get("connectsphere_session")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    session = (
        db.query(LoginSession)
        .filter(LoginSession.token == token, LoginSession.valid == True)
        .first()
    )
    if not session or not session.user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
        )

    return session.user


@router.post("/login")
def login(form: LoginForm, response: Response, db: Session = Depends(get_db)):
    if not form.username or not form.password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username and password are required",
        )

    user = db.query(User).filter(User.username == form.username).first()
    if not user or not verify_password(form.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    session = create_session(db, user)

    response.set_cookie(
        key="connectsphere_session",
        value=session.token,
        httponly=True,
        samesite="lax",
        path="/",
    )

    role_redirects = {
        "organiser": "/events",
        "coordinator": "/coordinator/events",
        "admin": "/admin",
    }
    redirect_url = role_redirects.get(user.role.lower(), "/dashboard")

    return {
        "message": "Login successful",
        "role": user.role,
        "username": user.username,
        "redirect_url": redirect_url,
    }


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    token = request.cookies.get("connectsphere_session")
    if token:
        session = (
            db.query(LoginSession)
            .filter(LoginSession.token == token, LoginSession.valid == True)
            .first()
        )
        if session:
            session.valid = False
            db.commit()

    response.delete_cookie(key="connectsphere_session", path="/")
    return {
        "message": "Logged out successfully",
        "redirect_url": "/login",
    }
