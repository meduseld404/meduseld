from database import db
from datetime import datetime, timezone


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    discord_id = db.Column(db.String(64), unique=True, nullable=False, index=True)
    username = db.Column(db.String(128), nullable=False)
    display_name = db.Column(db.String(128))
    avatar_hash = db.Column(db.String(256))
    email = db.Column(db.String(256))
    role = db.Column(db.String(32), nullable=False, default="user")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    last_login = db.Column(db.DateTime)

    def __repr__(self):
        return f"<User {self.username} ({self.discord_id})>"

    @property
    def avatar_url(self):
        if self.avatar_hash:
            return f"https://cdn.discordapp.com/avatars/{self.discord_id}/{self.avatar_hash}.png"
        return None

    @property
    def is_admin(self):
        return self.role == "admin"

    def to_dict(self):
        return {
            "id": self.id,
            "discord_id": self.discord_id,
            "username": self.username,
            "display_name": self.display_name,
            "avatar_url": self.avatar_url,
            "role": self.role,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_login": self.last_login.isoformat() if self.last_login else None,
        }

    @staticmethod
    def get_or_create(discord_id, username, display_name=None, avatar_hash=None, email=None):
        """Find existing user by Discord ID (or email fallback) or create a new one.
        Updates profile info on each login."""
        user = User.query.filter_by(discord_id=str(discord_id)).first()

        # If not found by discord_id, try email — this handles the case where
        # the user was already synced with their real Discord ID but Cloudflare
        # Access sends its own UUID as the sub claim on the next login.
        if not user and email:
            user = User.query.filter_by(email=email).first()

        if user:
            # Update profile info on each login
            user.username = username
            if display_name:
                user.display_name = display_name
            if avatar_hash:
                user.avatar_hash = avatar_hash
            if email:
                user.email = email
            user.last_login = datetime.now(timezone.utc)
        else:
            user = User(
                discord_id=str(discord_id),
                username=username,
                display_name=display_name,
                avatar_hash=avatar_hash,
                email=email,
                role="user",
                last_login=datetime.now(timezone.utc),
            )
            db.session.add(user)

        db.session.commit()
        return user
