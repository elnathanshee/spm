import os
import sys
from os.path import abspath, dirname

# Ensure backend package can be imported
sys.path.insert(0, dirname(dirname(abspath(__file__))))

from backend.database import SessionLocal, engine
from backend.models import Base, User
from backend.security import hash_password


def seed():
    development_password = os.environ.get("CONNECTSPHERE_DEV_PASSWORD")
    if not development_password:
        raise RuntimeError(
            "Set CONNECTSPHERE_DEV_PASSWORD before seeding development accounts."
        )

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        sample_users = [
            ("organiser1", "organiser", False),
            ("organiser2", "organiser", False),
            ("coord1", "coordinator", True),
            ("coord2", "coordinator", True),
            ("coord_inactive", "coordinator", False),
            ("lead1", "lead", False),
            ("venue1", "venue staff", False),
            ("technical1", "technical support", False),
            ("attendee1", "attendee", False),
        ]

        created_count = 0
        for username, role, is_active_coordinator in sample_users:
            existing = db.query(User).filter(User.username == username).first()
            if not existing:
                db.add(
                    User(
                        username=username,
                        password_hash=hash_password(development_password),
                        role=role,
                        is_active_coordinator=is_active_coordinator,
                    )
                )
                created_count += 1

        db.commit()
        print(f"Seeding complete. Created {created_count} users.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
