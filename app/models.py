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
    jellyfin_user_id = db.Column(db.String(64))
    jellyfin_password = db.Column(db.String(256))

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
            "has_jellyfin": bool(self.jellyfin_user_id),
        }

    @staticmethod
    def get_or_create(discord_id, username, display_name=None, avatar_hash=None, email=None):
        """Find existing user by Discord ID (or email fallback) or create a new one.
        Updates profile info on each login."""
        user = User.query.filter_by(discord_id=str(discord_id)).first()
        found_by_email = False

        # If not found by discord_id, try email — this handles the case where
        # the user was already synced with their real Discord ID but Cloudflare
        # Access sends its own UUID as the sub claim on the next login.
        if not user and email:
            user = User.query.filter_by(email=email).first()
            found_by_email = True

        if user:
            if found_by_email:
                # Found by email fallback — the stored discord_id is likely a
                # stale Cloudflare UUID. Update to the real Discord ID if we
                # have one (numeric snowflake), and update profile data.
                if discord_id and discord_id != user.discord_id and discord_id.isdigit():
                    user.discord_id = str(discord_id)
                # Always update profile when we have data — the caller
                # determines whether data is "real" or "fallback"
                user.username = username
                if display_name:
                    user.display_name = display_name
                if avatar_hash:
                    user.avatar_hash = avatar_hash
            else:
                # Found by discord_id — update profile info
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


class CalendarEvent(db.Model):
    __tablename__ = "calendar_events"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(256), nullable=False)
    description = db.Column(db.Text)
    event_date = db.Column(db.DateTime, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    creator = db.relationship("User", backref="calendar_events")
    rsvps = db.relationship("EventRSVP", backref="event", cascade="all, delete-orphan")

    def to_dict(self):
        rsvp_list = []
        for r in self.rsvps:
            rsvp_list.append(
                {
                    "user_id": r.user_id,
                    "discord_id": r.user.discord_id if r.user else None,
                    "display_name": r.user.display_name or r.user.username if r.user else None,
                    "avatar_url": r.user.avatar_url if r.user else None,
                    "status": r.status,
                }
            )
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "event_date": self.event_date.isoformat() if self.event_date else None,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "rsvps": rsvp_list,
        }


class EventRSVP(db.Model):
    __tablename__ = "event_rsvps"

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(
        db.Integer, db.ForeignKey("calendar_events.id", ondelete="CASCADE"), nullable=False
    )
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    status = db.Column(db.String(16), nullable=False)  # 'going', 'maybe', 'not_going'
    updated_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    user = db.relationship("User", backref="rsvps")

    __table_args__ = (db.UniqueConstraint("event_id", "user_id", name="uq_event_user_rsvp"),)
