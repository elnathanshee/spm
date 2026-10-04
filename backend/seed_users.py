import sys
from os.path import abspath, dirname

# Ensure backend package can be imported
sys.path.insert(0, dirname(dirname(abspath(__file__))))

from backend.database import SessionLocal, engine
from backend.models import Base, User
from backend.security import hash_password


def seed():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        sample_users = [
            {
                "username": "organiser1",
                "password": "password123",
                "role": "organiser",
                "is_active_coordinator": False,
            },
            {
                "username": "organiser2",
                "password": "password123",
                "role": "organiser",
                "is_active_coordinator": False,
            },
            {
                "username": "coord1",
                "password": "password123",
                "role": "coordinator",
                "is_active_coordinator": True,
            },
            {
                "username": "coord2",
                "password": "password123",
                "role": "coordinator",
                "is_active_coordinator": True,
            },
            {
                "username": "coord_inactive",
                "password": "password123",
                "role": "coordinator",
                "is_active_coordinator": False,
            },
            # [SCRUM-23 | Renee] NEW: Event Coordinator Lead (Week 7 Change #5)
            {
                "username": "lead1",
                "password": "password123",
                "role": "lead",
                "is_active_coordinator": False,
            },
            {
                "username": "admin1",
                "password": "password123",
                "role": "admin",
                "is_active_coordinator": False,
            },
        ]

        created_count = 0
        for u in sample_users:
            existing = db.query(User).filter(User.username == u["username"]).first()
            if not existing:
                user = User(
                    username=u["username"],
                    password_hash=hash_password(u["password"]),
                    role=u["role"],
                    is_active_coordinator=u["is_active_coordinator"],
                )
                db.add(user)
                created_count += 1

        db.commit()
        print(f"✅ Seeding complete. Created {created_count} users.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
