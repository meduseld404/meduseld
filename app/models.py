from database import db
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)


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


class GameVote(db.Model):
    __tablename__ = "game_votes"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    game_app_id = db.Column(db.String(32), nullable=False)
    rank = db.Column(db.Integer, nullable=False)  # 1 = top pick
    updated_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    user = db.relationship("User", backref="game_votes")

    __table_args__ = (db.UniqueConstraint("user_id", "game_app_id", name="uq_user_game_vote"),)


class GameListEntry(db.Model):
    __tablename__ = "game_list_entries"

    id = db.Column(db.Integer, primary_key=True)
    app_id = db.Column(db.String(32), unique=True, nullable=False)
    name = db.Column(db.String(256), nullable=False)
    url = db.Column(db.String(512), nullable=False)
    tooltip = db.Column(db.String(512))
    added_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    creator = db.relationship("User", backref="game_list_entries")

    def to_dict(self):
        return {
            "app_id": self.app_id,
            "name": self.name,
            "url": self.url,
            "tooltip": self.tooltip,
            "added_by": self.added_by,
            "added_by_name": (
                (self.creator.display_name or self.creator.username) if self.creator else None
            ),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class TriviaLobby(db.Model):
    """A multiplayer trivia lobby where users can host and join games."""

    __tablename__ = "trivia_lobbies"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(8), unique=True, nullable=False, index=True)
    host_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    status = db.Column(
        db.String(16), nullable=False, default="waiting"
    )  # waiting, playing, finished
    num_questions = db.Column(db.Integer, nullable=False, default=10)
    difficulty = db.Column(db.String(16), nullable=False, default="")  # empty = any
    category = db.Column(db.String(8), nullable=False, default="")  # empty = any
    category_name = db.Column(db.String(128), nullable=False, default="")
    max_players = db.Column(db.Integer, nullable=False, default=8)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    started_at = db.Column(db.DateTime)
    finished_at = db.Column(db.DateTime)

    host = db.relationship("User", backref="hosted_lobbies")

    def to_dict(self):
        return {
            "id": self.id,
            "code": self.code,
            "host_user_id": self.host_user_id,
            "host_name": (self.host.display_name or self.host.username) if self.host else None,
            "host_avatar": self.host.avatar_url if self.host else None,
            "status": self.status,
            "num_questions": self.num_questions,
            "difficulty": self.difficulty,
            "category": self.category,
            "category_name": self.category_name,
            "max_players": self.max_players,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class TriviaWin(db.Model):
    __tablename__ = "trivia_wins"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    score = db.Column(db.Integer, nullable=False)
    total_questions = db.Column(db.Integer, nullable=False)
    category = db.Column(db.String(128))
    won = db.Column(db.Boolean, default=False)
    played_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    user = db.relationship("User", backref="trivia_wins")

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "discord_id": self.user.discord_id if self.user else None,
            "display_name": self.user.display_name or self.user.username if self.user else None,
            "avatar_url": self.user.avatar_url if self.user else None,
            "score": self.score,
            "total_questions": self.total_questions,
            "category": self.category,
            "played_at": self.played_at.isoformat() if self.played_at else None,
        }


# ===== Achievement Definitions =====
# Hardcoded list of all achievements. The check functions are in webserver.py.
# Custom achievements created by admins are stored in the custom_achievements table.
ACHIEVEMENTS = {
    "first_login": {
        "name": "First Steps",
        "description": "Log in to Meduseld for the first time",
        "icon": "bi-door-open",
        "category": "general",
    },
    "trivia_rookie": {
        "name": "Trivia Rookie",
        "description": "Complete your first trivia game",
        "icon": "bi-question-circle",
        "category": "trivia",
    },
    "trivia_veteran": {
        "name": "Trivia Veteran",
        "description": "Complete 10 trivia games",
        "icon": "bi-mortarboard",
        "category": "trivia",
    },
    "trivia_master": {
        "name": "Trivia Master",
        "description": "Complete 50 trivia games",
        "icon": "bi-trophy",
        "category": "trivia",
    },
    "perfect_score": {
        "name": "Perfect Score",
        "description": "Get 100% on a trivia game",
        "icon": "bi-star-fill",
        "category": "trivia",
    },
    "trivia_streak_3": {
        "name": "On a Roll",
        "description": "Get 3 perfect scores",
        "icon": "bi-fire",
        "category": "trivia",
    },
    "trivia_hard_win": {
        "name": "Big Brain",
        "description": "Score 80%+ on a hard difficulty trivia game",
        "icon": "bi-lightbulb",
        "category": "trivia",
    },
    "trivia_all_categories": {
        "name": "Renaissance Mind",
        "description": "Play trivia in 10 different categories",
        "icon": "bi-grid-3x3-gap",
        "category": "trivia",
    },
    "night_owl": {
        "name": "Night Owl",
        "description": "Play a trivia game between midnight and 5 AM",
        "icon": "bi-moon-stars",
        "category": "trivia",
    },
    "media_explorer": {
        "name": "Media Explorer",
        "description": "Access Edoras (Jellyfin) for the first time",
        "icon": "bi-film",
        "category": "media",
    },
    "rsvp_king": {
        "name": "RSVP King",
        "description": "RSVP to 5 different events",
        "icon": "bi-hand-thumbs-up",
        "category": "social",
    },
    "game_critic": {
        "name": "Game Critic",
        "description": "Vote on the Games Up Next list",
        "icon": "bi-controller",
        "category": "general",
    },
    "server_starter": {
        "name": "Ignition",
        "description": "Start the game server 5 times",
        "icon": "bi-play-circle",
        "category": "server",
    },
    "server_stopper": {
        "name": "Lights Out",
        "description": "Stop the game server 10 times",
        "icon": "bi-stop-circle",
        "category": "server",
    },
    "server_killer": {
        "name": "Chaos Agent",
        "description": "Force kill the game server 5 times",
        "icon": "bi-lightning",
        "category": "server",
    },
    "easter_egg": {
        "name": "Secret Passage",
        "description": "Find the hidden link on the control panel",
        "icon": "bi-egg",
        "category": "secret",
    },
}


class UserActionCount(db.Model):
    """Tracks cumulative action counts per user for achievement purposes."""

    __tablename__ = "user_action_counts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    action = db.Column(
        db.String(64), nullable=False
    )  # e.g. 'server_start', 'server_stop', 'server_kill'
    count = db.Column(db.Integer, nullable=False, default=0)

    user = db.relationship("User", backref="action_counts")

    __table_args__ = (db.UniqueConstraint("user_id", "action", name="uq_user_action"),)

    @staticmethod
    def increment(user_id, action):
        """Increment an action counter for a user. Creates the row if it doesn't exist."""
        row = UserActionCount.query.filter_by(user_id=user_id, action=action).first()
        if row:
            row.count += 1
        else:
            row = UserActionCount(user_id=user_id, action=action, count=1)
            db.session.add(row)
        db.session.flush()
        return row.count


class CustomAchievement(db.Model):
    """Admin-created achievements that can be manually awarded to users."""

    __tablename__ = "custom_achievements"

    id = db.Column(db.Integer, primary_key=True)
    achievement_id = db.Column(db.String(64), unique=True, nullable=False)
    name = db.Column(db.String(128), nullable=False)
    description = db.Column(db.String(256), nullable=False)
    icon = db.Column(db.String(64), nullable=False, default="bi-award")
    category = db.Column(db.String(32), nullable=False, default="custom")
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    creator = db.relationship("User", backref="custom_achievements_created")

    def to_definition(self):
        return {
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "category": self.category,
        }

    def to_dict(self):
        return {
            "achievement_id": self.achievement_id,
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "category": self.category,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


def get_all_achievements():
    """Returns merged dict of hardcoded + custom achievements."""
    all_achs = dict(ACHIEVEMENTS)
    try:
        for ca in CustomAchievement.query.all():
            all_achs[ca.achievement_id] = ca.to_definition()
    except Exception as e:
        logger.error("Failed to load custom achievements: %s", e)
    return all_achs


class UserAchievement(db.Model):
    __tablename__ = "user_achievements"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    achievement_id = db.Column(db.String(64), nullable=False)
    unlocked_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    user = db.relationship("User", backref="achievements")

    __table_args__ = (db.UniqueConstraint("user_id", "achievement_id", name="uq_user_achievement"),)

    def to_dict(self):
        all_achs = get_all_achievements()
        defn = all_achs.get(self.achievement_id, {})
        return {
            "achievement_id": self.achievement_id,
            "name": defn.get("name", self.achievement_id),
            "description": defn.get("description", ""),
            "icon": defn.get("icon", "bi-award"),
            "category": defn.get("category", "general"),
            "unlocked_at": self.unlocked_at.isoformat() if self.unlocked_at else None,
        }


class PickerGame(db.Model):
    __tablename__ = "picker_games"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(256), nullable=False)
    image_url = db.Column(db.String(512))
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    added_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    creator = db.relationship("User", backref="picker_games")

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "image_url": self.image_url,
            "is_active": self.is_active,
            "added_by": self.added_by,
            "added_by_name": (
                (self.creator.display_name or self.creator.username) if self.creator else None
            ),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class WeeklyPick(db.Model):
    __tablename__ = "weekly_picks"

    id = db.Column(db.Integer, primary_key=True)
    game_id = db.Column(db.Integer, db.ForeignKey("picker_games.id"), nullable=False)
    spun_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    spun_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    week_start = db.Column(db.Date, unique=True, nullable=False)

    game = db.relationship("PickerGame", backref="picks")
    spinner = db.relationship("User", backref="weekly_picks")

    def to_dict(self):
        return {
            "id": self.id,
            "game_id": self.game_id,
            "game_name": self.game.name if self.game else None,
            "game_image": self.game.image_url if self.game else None,
            "spun_by": self.spun_by,
            "spun_by_name": (
                (self.spinner.display_name or self.spinner.username) if self.spinner else None
            ),
            "spun_by_avatar": self.spinner.avatar_url if self.spinner else None,
            "spun_at": self.spun_at.isoformat() if self.spun_at else None,
            "week_start": self.week_start.isoformat() if self.week_start else None,
        }


class FameEntry(db.Model):
    __tablename__ = "fame_entries"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    title = db.Column(db.String(256), nullable=False)
    caption = db.Column(db.Text)
    media_type = db.Column(db.String(16), nullable=False)  # 'image' or 'video'
    source_type = db.Column(db.String(16), nullable=False)  # 'upload' or 'link'
    file_path = db.Column(db.String(512))  # for uploads
    url = db.Column(db.String(512))  # for external links
    vote_count = db.Column(db.Integer, nullable=False, default=0)
    tag = db.Column(db.String(64))  # game tag, e.g. 'PEAK', 'R.E.P.O.', 'Icarus'
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    creator = db.relationship("User", backref="fame_entries")

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "display_name": (
                (self.creator.display_name or self.creator.username) if self.creator else None
            ),
            "avatar_url": self.creator.avatar_url if self.creator else None,
            "title": self.title,
            "caption": self.caption,
            "media_type": self.media_type,
            "source_type": self.source_type,
            "file_path": self.file_path,
            "url": self.url,
            "vote_count": self.vote_count,
            "tag": self.tag,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class FameVote(db.Model):
    __tablename__ = "fame_votes"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    entry_id = db.Column(
        db.Integer, db.ForeignKey("fame_entries.id", ondelete="CASCADE"), nullable=False
    )

    __table_args__ = (db.UniqueConstraint("user_id", "entry_id", name="uq_fame_vote"),)

    user = db.relationship("User", backref="fame_votes")
    entry = db.relationship("FameEntry", backref="votes")
