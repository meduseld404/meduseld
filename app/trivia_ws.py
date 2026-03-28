"""
Trivia multiplayer lobby system using Flask-SocketIO.

Manages lobby creation, joining, synchronized gameplay, and score tracking.
All game state is held in-memory (lobby_games dict) for speed; only final
results are persisted to the database via TriviaWin.
"""

import re
import random
import string
import html as html_mod
import logging
import threading
import jwt
import requests
from datetime import datetime, timezone
from flask import request
from flask_socketio import SocketIO, emit, join_room, leave_room

logger = logging.getLogger(__name__)

socketio = SocketIO(
    cors_allowed_origins="*", async_mode="gevent", ping_timeout=30, ping_interval=15
)

# In-memory lobby state: code -> LobbyState
lobby_games = {}

# Flask app reference — set by register_trivia_rest() so background tasks
# can push an app context for DB operations.
_flask_app = None


class LobbyState:
    """In-memory state for an active lobby."""

    def __init__(self, code, host_user_id, host_info, settings):
        self.code = code
        self.host_user_id = host_user_id
        self.status = "waiting"  # waiting, countdown, playing, results
        self.settings = (
            settings  # {num_questions, difficulty, category, category_name, max_players}
        )
        self.players = (
            {}
        )  # user_id -> {sid, display_name, avatar_url, discord_id, score, answers, connected}
        self.questions = []  # fetched from Open Trivia DB
        self.current_question = -1
        self.question_timer = None
        self.question_deadline = None  # timestamp when current question expires
        self.question_revealed = False  # guard against double reveal
        self._cleanup_pending = False  # set True when game ends, cancelled by play_again
        self.sudden_death = False  # True when in sudden death tiebreaker
        self.sudden_death_players = []  # user_ids of tied players in sudden death
        self.sudden_death_round = 0  # which sudden death question we're on
        self.lock = threading.Lock()

        # Add host as first player
        self.players[host_user_id] = {
            "sid": None,
            "display_name": host_info.get("display_name", "Host"),
            "avatar_url": host_info.get("avatar_url"),
            "discord_id": host_info.get("discord_id", ""),
            "score": 0,
            "answers": [],
            "connected": True,
        }

    def player_count(self):
        return len([p for p in self.players.values() if p["connected"]])

    def to_dict(self):
        players_list = []
        for uid, p in self.players.items():
            if p["connected"]:
                players_list.append(
                    {
                        "user_id": uid,
                        "display_name": p["display_name"],
                        "avatar_url": p["avatar_url"],
                        "score": p["score"],
                        "answered": (
                            len(p["answers"]) > self.current_question
                            if self.current_question >= 0
                            else False
                        ),
                    }
                )
        return {
            "code": self.code,
            "status": self.status,
            "settings": self.settings,
            "players": players_list,
            "current_question": self.current_question,
            "total_questions": (
                len(self.questions) if self.questions else self.settings.get("num_questions", 10)
            ),
            "host_user_id": self.host_user_id,
            "host_name": self.players.get(self.host_user_id, {}).get("display_name", "Host"),
            "sudden_death": self.sudden_death,
            "sudden_death_players": self.sudden_death_players,
        }


def _generate_code():
    """Generate a unique 6-char lobby code."""
    for _ in range(50):
        code = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        if code not in lobby_games:
            return code
    return None


def _authenticate_socket():
    """Authenticate a WebSocket connection from the CF_Authorization token."""
    token = request.args.get("token", "")
    if not token:
        return None
    try:
        payload = jwt.decode(token, options={"verify_signature": False})
        custom = payload.get("custom", {})
        discord_user = custom.get("discord_user", {}) if isinstance(custom, dict) else {}
        if not discord_user or not discord_user.get("id"):
            return None

        from models import User

        user = User.query.filter_by(discord_id=str(discord_user["id"])).first()
        if not user:
            return None
        return user
    except Exception as e:
        logger.error("Socket auth failed: %s", e)
        return None


QUESTION_TIME_SECONDS = 20  # seconds per question
RESULTS_PAUSE_SECONDS = 5  # seconds to show answer before next question
COUNTDOWN_SECONDS = 5  # countdown before game starts

CUSTOM_CATEGORY_FLAGS = "flags"

# Common country name aliases for fuzzy matching (lowercase -> canonical name)
# Canonical names must match restcountries "common" name exactly
COUNTRY_ALIASES = {
    "usa": "United States",
    "us": "United States",
    "america": "United States",
    "united states of america": "United States",
    "uk": "United Kingdom",
    "great britain": "United Kingdom",
    "britain": "United Kingdom",
    "england": "United Kingdom",
    "russia": "Russia",
    "south korea": "South Korea",
    "north korea": "North Korea",
    "czech republic": "Czechia",
    "ivory coast": "Côte d'Ivoire",
    "cote d'ivoire": "Côte d'Ivoire",
    "cote divoire": "Côte d'Ivoire",
    "east timor": "Timor-Leste",
    "burma": "Myanmar",
    "holland": "Netherlands",
    "the netherlands": "Netherlands",
    "vatican": "Vatican City",
    "vatican city state": "Vatican City",
    "holy see": "Vatican City",
    "congo": "Republic of the Congo",
    "republic of congo": "Republic of the Congo",
    "republic of the congo": "Republic of the Congo",
    "dr congo": "DR Congo",
    "drc": "DR Congo",
    "democratic republic of the congo": "DR Congo",
    "democratic republic of congo": "DR Congo",
    "swaziland": "Eswatini",
    "cape verde": "Cape Verde",
    "cabo verde": "Cape Verde",
    "uae": "United Arab Emirates",
    "emirates": "United Arab Emirates",
    "macau": "Macau",
    "macao": "Macau",
    "hong kong sar": "Hong Kong",
    "falklands": "Falkland Islands",
    "falkland islands": "Falkland Islands",
    "micronesia": "Micronesia",
    "federated states of micronesia": "Micronesia",
    "palestine": "Palestine",
    "taiwan": "Taiwan",
    "republic of china": "Taiwan",
    "chinese taipei": "Taiwan",
    "the gambia": "Gambia",
    "the bahamas": "Bahamas",
    "turks and caicos": "Turks and Caicos Islands",
    "bvi": "British Virgin Islands",
    "usvi": "United States Virgin Islands",
    "us virgin islands": "United States Virgin Islands",
    "st kitts": "Saint Kitts and Nevis",
    "st lucia": "Saint Lucia",
    "st vincent": "Saint Vincent and the Grenadines",
    "st helena": "Saint Helena, Ascension and Tristan da Cunha",
    "st martin": "Saint Martin",
    "st barthelemy": "Saint Barthélemy",
    "st barts": "Saint Barthélemy",
    "st pierre": "Saint Pierre and Miquelon",
    "curacao": "Curaçao",
    "reunion": "Réunion",
    "sint maarten": "Sint Maarten",
    "cocos islands": "Cocos (Keeling) Islands",
    "pitcairn": "Pitcairn Islands",
    "svalbard": "Svalbard and Jan Mayen",
    "heard island": "Heard Island and McDonald Islands",
    "christmas island": "Christmas Island",
    "norfolk island": "Norfolk Island",
    "wallis and futuna": "Wallis and Futuna",
    "new caledonia": "New Caledonia",
    "french polynesia": "French Polynesia",
    "french guiana": "French Guiana",
    "martinique": "Martinique",
    "guadeloupe": "Guadeloupe",
    "mayotte": "Mayotte",
    "bermuda": "Bermuda",
    "cayman islands": "Cayman Islands",
    "gibraltar": "Gibraltar",
    "guernsey": "Guernsey",
    "jersey": "Jersey",
    "isle of man": "Isle of Man",
    "aruba": "Aruba",
    "greenland": "Greenland",
    "faroe islands": "Faroe Islands",
    "faroes": "Faroe Islands",
    "cook islands": "Cook Islands",
    "niue": "Niue",
    "tokelau": "Tokelau",
    "american samoa": "American Samoa",
    "guam": "Guam",
    "puerto rico": "Puerto Rico",
    "northern mariana islands": "Northern Mariana Islands",
}

# Cache for REST Countries data (fetched once per process lifetime)
_countries_cache = None
_countries_cache_lock = threading.Lock()


def _get_countries():
    """Fetch and cache all countries from REST Countries API."""
    global _countries_cache
    if _countries_cache is not None:
        return _countries_cache

    with _countries_cache_lock:
        if _countries_cache is not None:
            return _countries_cache
        try:
            resp = requests.get(
                "https://restcountries.com/v3.1/all?fields=name,flags,cca2",
                timeout=15,
            )
            data = resp.json()
            countries = []
            for c in data:
                common = c.get("name", {}).get("common", "")
                cca2 = c.get("cca2", "")
                flag_url = c.get("flags", {}).get("svg") or c.get("flags", {}).get("png", "")
                if common and cca2:
                    countries.append({"name": common, "flag": flag_url, "cca2": cca2})
            _countries_cache = countries
            logger.info("Cached %d countries from REST Countries API", len(countries))
            return countries
        except Exception as e:
            logger.error("Failed to fetch countries from REST Countries API: %s", e)
            return None


def _normalize_answer(text):
    """Normalize a country name for fuzzy comparison."""
    text = text.strip().lower()
    # Remove common prefixes/articles
    for prefix in ["the ", "republic of ", "kingdom of ", "state of "]:
        if text.startswith(prefix):
            text = text[len(prefix) :]
    # Remove accents and special chars for comparison
    text = re.sub(r"[''`]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _check_flag_answer(submitted, correct_name):
    """Check if a submitted answer matches the correct country name (fuzzy)."""
    sub = submitted.strip()
    sub_lower = sub.lower()
    correct_lower = correct_name.lower()

    # Exact match (case-insensitive)
    if sub_lower == correct_lower:
        return True

    # Check aliases
    canonical = COUNTRY_ALIASES.get(sub_lower)
    if canonical and canonical.lower() == correct_lower:
        return True

    # Normalized comparison (strips articles, accents, etc.)
    if _normalize_answer(sub) == _normalize_answer(correct_name):
        return True

    return False


def _fetch_flag_questions(settings):
    """Generate flag identification questions from REST Countries API."""
    countries = _get_countries()
    if not countries:
        return None

    num = settings.get("num_questions", 10)
    # Pick random countries (more than needed in case of issues)
    pool = list(countries)
    random.shuffle(pool)
    selected = pool[: min(num, len(pool))]

    questions = []
    for c in selected:
        questions.append(
            {
                "question": c["cca2"].lower(),  # Country code (client builds flag URL)
                "correct_answer": c["name"],
                "incorrect_answers": [],  # Not used for text-input mode
                "category": "Country Flags",
                "difficulty": "mixed",
                "type": "flags",
            }
        )
    return questions


def _fetch_questions(settings):
    """Fetch questions from Open Trivia Database or custom sources."""
    # Custom category: Country Flags
    if settings.get("category") == CUSTOM_CATEGORY_FLAGS:
        return _fetch_flag_questions(settings)

    url = f"https://opentdb.com/api.php?amount={settings.get('num_questions', 10)}&type=multiple"
    if settings.get("category"):
        url += f"&category={settings['category']}"
    if settings.get("difficulty"):
        url += f"&difficulty={settings['difficulty']}"
    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()
        if data.get("response_code") != 0 or not data.get("results"):
            logger.error("Open Trivia DB returned code %s", data.get("response_code"))
            return None
        # Decode HTML entities (opentdb returns &amp; &#039; &quot; etc.)
        for q in data["results"]:
            q["question"] = html_mod.unescape(q["question"])
            q["correct_answer"] = html_mod.unescape(q["correct_answer"])
            q["incorrect_answers"] = [html_mod.unescape(a) for a in q["incorrect_answers"]]
            q["category"] = html_mod.unescape(q["category"])
        return data["results"]
    except Exception as e:
        logger.error("Failed to fetch trivia questions: %s", e)
        return None


def _prepare_question(q, index):
    """Prepare a question for sending to clients (shuffled answers, no correct answer revealed)."""
    if q.get("type") == "flags":
        return {
            "index": index,
            "question": q["question"],  # Country code (client builds flag URL)
            "category": q["category"],
            "difficulty": q["difficulty"],
            "answers": [],  # Empty for text-input mode
            "type": "flags",
        }
    answers = q["incorrect_answers"][:] + [q["correct_answer"]]
    random.shuffle(answers)
    return {
        "index": index,
        "question": q["question"],
        "category": q["category"],
        "difficulty": q["difficulty"],
        "answers": answers,
    }


def _advance_question(code):
    """Advance to the next question or end the game."""
    lobby = lobby_games.get(code)
    if not lobby or lobby.status != "playing":
        return

    with lobby.lock:
        lobby.current_question += 1
        lobby.question_revealed = False

        if lobby.current_question >= len(lobby.questions):
            # Don't call _end_game inside the lock — it does DB ops and socketio emits
            pass
        else:
            q = lobby.questions[lobby.current_question]
            q_data = _prepare_question(q, lobby.current_question)
            lobby.question_deadline = datetime.now(timezone.utc).timestamp() + QUESTION_TIME_SECONDS

    # Check if game is over (outside the lock)
    if lobby.current_question >= len(lobby.questions):
        _end_game(code)
        return

    socketio.emit(
        "question",
        {
            **q_data,
            "time_limit": QUESTION_TIME_SECONDS,
        },
        room=code,
        namespace="/trivia",
    )

    # Schedule auto-advance when time runs out
    lobby.question_timer = socketio.start_background_task(
        _question_timeout, code, lobby.current_question
    )


def _question_timeout(code, question_index):
    """Called when question time expires. Reveal answer and advance."""
    socketio.sleep(QUESTION_TIME_SECONDS)
    lobby = lobby_games.get(code)
    if not lobby or lobby.status != "playing":
        return
    if lobby.current_question != question_index:
        return  # Already advanced

    _reveal_and_advance(code)


def _all_answered(lobby):
    """Check if all relevant players have answered the current question."""
    for uid, p in lobby.players.items():
        if not p["connected"]:
            continue
        # During sudden death, only check the dueling players
        if lobby.sudden_death and uid not in lobby.sudden_death_players:
            continue
        if len(p["answers"]) <= lobby.current_question:
            return False
    return True


def _reveal_and_advance(code):
    """Reveal the correct answer, show scores, then advance after a pause."""
    lobby = lobby_games.get(code)
    if not lobby or lobby.status != "playing":
        return

    with lobby.lock:
        if lobby.question_revealed:
            return  # Already revealed for this question
        lobby.question_revealed = True

    q = lobby.questions[lobby.current_question]
    correct = q["correct_answer"]
    is_flag = q.get("type") == "flags"

    # Build per-player results for this question
    player_results = []
    for uid, p in lobby.players.items():
        if not p["connected"]:
            continue
        if len(p["answers"]) > lobby.current_question:
            ans = p["answers"][lobby.current_question]
            is_correct = _check_flag_answer(ans, correct) if is_flag else ans == correct
            player_results.append(
                {
                    "user_id": uid,
                    "display_name": p["display_name"],
                    "answer": ans,
                    "correct": is_correct,
                    "score": p["score"],
                }
            )
        else:
            # Didn't answer in time
            p["answers"].append(None)
            player_results.append(
                {
                    "user_id": uid,
                    "display_name": p["display_name"],
                    "answer": None,
                    "correct": False,
                    "score": p["score"],
                }
            )

    socketio.emit(
        "answer_reveal",
        {
            "correct_answer": correct,
            "player_results": player_results,
            "question_index": lobby.current_question,
        },
        room=code,
        namespace="/trivia",
    )

    # During sudden death, check if someone got eliminated
    if lobby.sudden_death:
        if _check_sudden_death_result(code, player_results):
            # Someone was eliminated — end the game after a pause
            socketio.start_background_task(_delayed_finalize, code)
            return
        # Everyone got it right or everyone got it wrong — continue
        lobby.sudden_death_round += 1
        # Check if we've run out of sudden death questions
        if lobby.current_question + 1 >= len(lobby.questions):
            # Ran out of questions, end as a draw
            lobby.sudden_death = False
            socketio.start_background_task(_delayed_finalize, code)
            return

    # Pause then advance
    socketio.start_background_task(_delayed_advance, code)


def _delayed_advance(code):
    """Wait then advance to next question."""
    socketio.sleep(RESULTS_PAUSE_SECONDS)
    _advance_question(code)


def _delayed_finalize(code):
    """Wait then finalize the game (used after sudden death resolution)."""
    socketio.sleep(RESULTS_PAUSE_SECONDS)
    _finalize_game(code)


def _end_game(code):
    """End the game, persist results, emit final scores. If top players are tied, enter sudden death."""
    lobby = lobby_games.get(code)
    if not lobby:
        return

    # Check for tie at the top (only if not already in sudden death that resolved)
    if not lobby.sudden_death:
        scores = {}
        for uid, p in lobby.players.items():
            if p["connected"] or p["score"] > 0:
                scores[uid] = p["score"]
        if scores:
            top_score = max(scores.values())
            tied = [uid for uid, s in scores.items() if s == top_score]
            if len(tied) >= 2:
                _start_sudden_death(code, tied)
                return

    _finalize_game(code)


def _start_sudden_death(code, tied_player_ids):
    """Enter sudden death mode for tied players."""
    lobby = lobby_games.get(code)
    if not lobby:
        return

    lobby.sudden_death = True
    lobby.sudden_death_players = tied_player_ids
    lobby.sudden_death_round = 0

    tied_names = [
        lobby.players[uid]["display_name"]
        for uid in tied_player_ids
        if uid in lobby.players
    ]
    logger.info(
        "Sudden death in lobby %s between %s (score: %d)",
        code,
        ", ".join(tied_names),
        lobby.players[tied_player_ids[0]]["score"],
    )

    socketio.emit(
        "sudden_death",
        {
            "players": [
                {
                    "user_id": uid,
                    "display_name": lobby.players[uid]["display_name"],
                    "avatar_url": lobby.players[uid].get("avatar_url"),
                }
                for uid in tied_player_ids
                if uid in lobby.players
            ],
            "score": lobby.players[tied_player_ids[0]]["score"],
        },
        room=code,
        namespace="/trivia",
    )

    # Fetch sudden death questions and start after a pause
    socketio.start_background_task(_sudden_death_start, code)


def _sudden_death_start(code):
    """Fetch questions for sudden death and start the first round."""
    socketio.sleep(4)  # Dramatic pause for the announcement
    lobby = lobby_games.get(code)
    if not lobby or not lobby.sudden_death:
        return

    # Fetch extra questions for sudden death (up to 10 rounds max)
    sd_settings = dict(lobby.settings)
    sd_settings["num_questions"] = 10
    extra_qs = _fetch_questions(sd_settings)
    if not extra_qs:
        # Fallback: can't fetch more questions, just end the game as-is
        logger.warning("Could not fetch sudden death questions for lobby %s, ending game", code)
        lobby.sudden_death = False
        _finalize_game(code)
        return

    # Append sudden death questions to the existing list
    lobby.questions.extend(extra_qs)
    lobby.status = "playing"

    # current_question is already past the last regular question (== original length).
    # _advance_question increments before emitting, so step back by one so the
    # first sudden death question (at the old length index) isn't skipped.
    lobby.current_question -= 1

    _advance_question(code)


def _check_sudden_death_result(code, player_results):
    """After a sudden death reveal, check if anyone got eliminated.
    Returns True if sudden death is resolved (game should end), False to continue."""
    lobby = lobby_games.get(code)
    if not lobby or not lobby.sudden_death:
        return False

    # Check results for the sudden death players only
    sd_results = {}
    for pr in player_results:
        if pr["user_id"] in lobby.sudden_death_players:
            sd_results[pr["user_id"]] = pr["correct"]

    got_right = [uid for uid, correct in sd_results.items() if correct]
    got_wrong = [uid for uid, correct in sd_results.items() if not correct]

    if len(got_right) == 0:
        # Everyone got it wrong — continue sudden death, no elimination
        return False

    if len(got_wrong) == 0:
        # Everyone got it right — continue sudden death
        return False

    # Some got it right, some got it wrong — eliminate the wrong ones
    # Award +1 to survivors so they pull ahead in the standings
    for uid in got_right:
        if uid in lobby.players:
            lobby.players[uid]["score"] += 1

    # End sudden death — the remaining players won
    lobby.sudden_death = False
    return True


def _finalize_game(code):
    """Persist results and emit final scores."""
    lobby = lobby_games.get(code)
    if not lobby:
        return

    lobby.status = "results"

    # Build final standings sorted by score desc
    num_questions = lobby.settings.get("num_questions", len(lobby.questions))
    standings = []
    for uid, p in lobby.players.items():
        standings.append(
            {
                "user_id": uid,
                "display_name": p["display_name"],
                "avatar_url": p["avatar_url"],
                "discord_id": p["discord_id"],
                "score": p["score"],
                "total": num_questions,
            }
        )
    standings.sort(key=lambda x: x["score"], reverse=True)

    # Persist each player's result as a TriviaWin
    try:
        from models import TriviaWin
        from database import db

        # Background tasks (gevent greenlets) don't inherit the Flask app
        # context, so push one explicitly for DB operations.
        ctx = _flask_app.app_context() if _flask_app else None
        if ctx:
            ctx.push()
        try:
            cat_name = lobby.settings.get("category_name", "")
            persisted = 0
            # Determine the highest score to identify winner(s)
            top_score = max((p["score"] for p in lobby.players.values()), default=0)
            for uid, p in lobby.players.items():
                if not p["connected"] and p["score"] == 0:
                    continue  # Skip fully disconnected players with no score
                win = TriviaWin(
                    user_id=uid,
                    score=p["score"],
                    total_questions=lobby.settings.get("num_questions", len(lobby.questions)),
                    category=cat_name,
                    won=(p["score"] == top_score and top_score > 0),
                )
                db.session.add(win)
                persisted += 1
            db.session.commit()
            logger.info("Persisted %d trivia results for lobby %s", persisted, code)
        except Exception as e:
            logger.error("Failed to persist trivia results for lobby %s: %s", code, e)
            try:
                db.session.rollback()
            except Exception as rollback_err:
                logger.error("Failed to rollback after trivia persist error: %s", rollback_err)

        # Update DB lobby status
        try:
            from models import TriviaLobby

            db_lobby = TriviaLobby.query.filter_by(code=code).first()
            if db_lobby:
                db_lobby.status = "finished"
                db_lobby.finished_at = datetime.now(timezone.utc)
                db.session.commit()
        except Exception as e:
            logger.error("Failed to update lobby status for %s: %s", code, e)
        finally:
            if ctx:
                ctx.pop()
    except Exception as e:
        logger.error("Failed to set up DB context for lobby %s: %s", code, e)

    socketio.emit("game_over", {"standings": standings}, room=code, namespace="/trivia")

    # Schedule cleanup after a delay (cancelled if play_again is triggered)
    lobby._cleanup_pending = True
    socketio.start_background_task(_cleanup_lobby, code)


def _cleanup_lobby(code):
    """Remove lobby from memory after a delay (unless play_again cancelled it)."""
    socketio.sleep(120)  # Keep for 2 minutes so players can see results
    lobby = lobby_games.get(code)
    if lobby and getattr(lobby, "_cleanup_pending", False):
        # Still pending cleanup — no play_again was triggered, so clean up
        # Mark all players disconnected first
        for uid, p in lobby.players.items():
            p["connected"] = False
            if p.get("sid"):
                leave_room(code, sid=p["sid"], namespace="/trivia")
        lobby_games.pop(code, None)


def _abort_game(code):
    """End the game early without persisting any results."""
    lobby = lobby_games.get(code)
    if not lobby:
        return

    lobby.status = "results"

    # Build standings but don't persist
    num_questions = lobby.settings.get("num_questions", len(lobby.questions))
    standings = []
    for uid, p in lobby.players.items():
        standings.append(
            {
                "user_id": uid,
                "display_name": p["display_name"],
                "avatar_url": p["avatar_url"],
                "discord_id": p["discord_id"],
                "score": p["score"],
                "total": num_questions,
            }
        )
    standings.sort(key=lambda x: x["score"], reverse=True)

    # Update DB lobby status only (no TriviaWin rows)
    try:
        from models import TriviaLobby
        from database import db

        db_lobby = TriviaLobby.query.filter_by(code=code).first()
        if db_lobby:
            db_lobby.status = "finished"
            db_lobby.finished_at = datetime.now(timezone.utc)
            db.session.commit()
    except Exception as e:
        logger.error("Failed to update lobby status for aborted game %s: %s", code, e)

    socketio.emit("game_aborted", {"standings": standings}, room=code, namespace="/trivia")
    logger.info("Game aborted in lobby %s by host", code)

    # Schedule cleanup after a delay (cancelled if play_again is triggered)
    lobby._cleanup_pending = True
    socketio.start_background_task(_cleanup_lobby, code)


# ==================== SOCKET EVENT HANDLERS ====================


@socketio.on("connect", namespace="/trivia")
def on_connect():
    user = _authenticate_socket()
    if not user:
        logger.warning("Trivia WS: unauthenticated connection rejected")
        return False  # Reject connection

    # Store user info on the socket session
    request.environ["trivia_user"] = user
    logger.info("Trivia WS: %s connected (sid=%s)", user.username, request.sid)

    # Send the user their DB ID so the client can identify themselves in lobby data
    emit("welcome", {"user_id": user.id})
    return True


@socketio.on("disconnect", namespace="/trivia")
def on_disconnect():
    user = request.environ.get("trivia_user")
    if not user:
        return

    # Mark player as disconnected in any lobby they're in
    for code, lobby in list(lobby_games.items()):
        if user.id in lobby.players:
            lobby.players[user.id]["connected"] = False
            lobby.players[user.id]["sid"] = None

            # Notify remaining players
            socketio.emit(
                "player_left",
                {
                    "user_id": user.id,
                    "display_name": lobby.players[user.id]["display_name"],
                    "lobby": lobby.to_dict(),
                },
                room=code,
                namespace="/trivia",
            )

            # If host left during waiting, close the lobby
            if lobby.status == "waiting" and user.id == lobby.host_user_id:
                _close_lobby(code, "Host disconnected")
                break

            # If all players disconnected during a game, end it
            if lobby.status == "playing" and lobby.player_count() == 0:
                _end_game(code)
                break

            # If playing and all remaining players answered, advance
            if lobby.status == "playing" and _all_answered(lobby):
                _reveal_and_advance(code)

    logger.info("Trivia WS: %s disconnected", user.username if user else "unknown")


def _close_lobby(code, reason="Lobby closed"):
    """Close a lobby and notify all players."""
    lobby = lobby_games.pop(code, None)
    if not lobby:
        return

    socketio.emit("lobby_closed", {"reason": reason}, room=code, namespace="/trivia")

    # Update DB
    try:
        from models import TriviaLobby
        from database import db

        db_lobby = TriviaLobby.query.filter_by(code=code).first()
        if db_lobby:
            db_lobby.status = "finished"
            db_lobby.finished_at = datetime.now(timezone.utc)
            db.session.commit()
    except Exception as e:
        logger.error("Failed to close lobby %s in DB: %s", code, e)


@socketio.on("create_lobby", namespace="/trivia")
def on_create_lobby(data):
    user = request.environ.get("trivia_user")
    if not user:
        emit("error", {"message": "Not authenticated"})
        return

    # Check if user is already in a lobby
    for code, lobby in lobby_games.items():
        if user.id in lobby.players and lobby.players[user.id]["connected"]:
            emit("error", {"message": "You are already in a lobby", "code": code})
            return

    code = _generate_code()
    if not code:
        emit("error", {"message": "Could not generate lobby code"})
        return

    settings = {
        "num_questions": min(max(int(data.get("num_questions", 10)), 5), 20),
        "difficulty": data.get("difficulty", ""),
        "category": data.get("category", ""),
        "category_name": data.get("category_name", "Any Category"),
        "max_players": min(max(int(data.get("max_players", 8)), 2), 12),
    }

    lobby = LobbyState(
        code,
        user.id,
        {
            "display_name": user.display_name or user.username,
            "avatar_url": user.avatar_url,
            "discord_id": user.discord_id,
        },
        settings,
    )
    lobby.players[user.id]["sid"] = request.sid
    lobby_games[code] = lobby

    # Persist to DB
    try:
        from models import TriviaLobby
        from database import db

        db_lobby = TriviaLobby(
            code=code,
            host_user_id=user.id,
            status="waiting",
            num_questions=settings["num_questions"],
            difficulty=settings["difficulty"],
            category=settings["category"],
            category_name=settings["category_name"],
            max_players=settings["max_players"],
        )
        db.session.add(db_lobby)
        db.session.commit()
    except Exception as e:
        logger.error("Failed to persist lobby %s: %s", code, e)

    join_room(code)
    emit("lobby_created", {"lobby": lobby.to_dict()})
    logger.info("Lobby %s created by %s", code, user.username)


@socketio.on("join_lobby", namespace="/trivia")
def on_join_lobby(data):
    user = request.environ.get("trivia_user")
    if not user:
        emit("error", {"message": "Not authenticated"})
        return

    code = str(data.get("code", "")).upper().strip()
    lobby = lobby_games.get(code)

    if not lobby:
        emit("error", {"message": "Lobby not found"})
        return

    if lobby.status != "waiting":
        emit("error", {"message": "Game already in progress"})
        return

    if lobby.player_count() >= lobby.settings.get("max_players", 8):
        emit("error", {"message": "Lobby is full"})
        return

    # Check if already in another lobby
    for other_code, other_lobby in lobby_games.items():
        if (
            other_code != code
            and user.id in other_lobby.players
            and other_lobby.players[user.id]["connected"]
        ):
            emit("error", {"message": "You are already in another lobby"})
            return

    # Rejoin if was previously in this lobby
    if user.id in lobby.players:
        lobby.players[user.id]["connected"] = True
        lobby.players[user.id]["sid"] = request.sid
    else:
        lobby.players[user.id] = {
            "sid": request.sid,
            "display_name": user.display_name or user.username,
            "avatar_url": user.avatar_url,
            "discord_id": user.discord_id,
            "score": 0,
            "answers": [],
            "connected": True,
        }

    join_room(code)
    emit("lobby_joined", {"lobby": lobby.to_dict()})

    # Notify others
    socketio.emit(
        "player_joined",
        {
            "user_id": user.id,
            "display_name": user.display_name or user.username,
            "avatar_url": user.avatar_url,
            "lobby": lobby.to_dict(),
        },
        room=code,
        skip_sid=request.sid,
        namespace="/trivia",
    )

    logger.info("%s joined lobby %s", user.username, code)


@socketio.on("leave_lobby", namespace="/trivia")
def on_leave_lobby(data):
    user = request.environ.get("trivia_user")
    if not user:
        return

    code = str(data.get("code", "")).upper().strip()
    lobby = lobby_games.get(code)
    if not lobby or user.id not in lobby.players:
        return

    leave_room(code)
    lobby.players[user.id]["connected"] = False
    lobby.players[user.id]["sid"] = None

    # If host leaves during waiting, close lobby
    if lobby.status == "waiting" and user.id == lobby.host_user_id:
        _close_lobby(code, "Host left the lobby")
        return

    socketio.emit(
        "player_left",
        {
            "user_id": user.id,
            "display_name": lobby.players[user.id]["display_name"],
            "lobby": lobby.to_dict(),
        },
        room=code,
        namespace="/trivia",
    )


@socketio.on("start_game", namespace="/trivia")
def on_start_game(data):
    user = request.environ.get("trivia_user")
    if not user:
        emit("error", {"message": "Not authenticated"})
        return

    code = str(data.get("code", "")).upper().strip()
    lobby = lobby_games.get(code)

    if not lobby:
        emit("error", {"message": "Lobby not found"})
        return

    if user.id != lobby.host_user_id:
        emit("error", {"message": "Only the host can start the game"})
        return

    if lobby.status != "waiting":
        emit("error", {"message": "Game already started"})
        return

    # Fetch questions
    questions = _fetch_questions(lobby.settings)
    if not questions:
        emit("error", {"message": "Could not fetch questions. Try different settings."})
        return

    lobby.questions = questions
    lobby.status = "countdown"

    # Update DB
    try:
        from models import TriviaLobby
        from database import db

        db_lobby = TriviaLobby.query.filter_by(code=code).first()
        if db_lobby:
            db_lobby.status = "playing"
            db_lobby.started_at = datetime.now(timezone.utc)
            db.session.commit()
    except Exception as e:
        logger.error("Failed to update lobby %s start status: %s", code, e)

    socketio.emit(
        "game_starting",
        {
            "countdown": COUNTDOWN_SECONDS,
            "total_questions": len(questions),
            "category_name": lobby.settings.get("category_name", ""),
        },
        room=code,
        namespace="/trivia",
    )

    # Start countdown then first question
    socketio.start_background_task(_start_after_countdown, code)
    logger.info(
        "Game starting in lobby %s (%d players, %d questions)",
        code,
        lobby.player_count(),
        len(questions),
    )


def _start_after_countdown(code):
    """Wait for countdown then start the first question."""
    socketio.sleep(COUNTDOWN_SECONDS)
    lobby = lobby_games.get(code)
    if not lobby or lobby.status != "countdown":
        return
    lobby.status = "playing"
    _advance_question(code)


@socketio.on("submit_answer", namespace="/trivia")
def on_submit_answer(data):
    user = request.environ.get("trivia_user")
    if not user:
        return

    code = str(data.get("code", "")).upper().strip()
    lobby = lobby_games.get(code)

    if not lobby or lobby.status != "playing":
        return

    if user.id not in lobby.players:
        return

    player = lobby.players[user.id]
    qi = lobby.current_question

    # Already answered this question
    if len(player["answers"]) > qi:
        return

    answer = data.get("answer", "")
    correct_answer = lobby.questions[qi]["correct_answer"]
    is_flag = lobby.questions[qi].get("type") == "flags"
    is_correct = _check_flag_answer(answer, correct_answer) if is_flag else answer == correct_answer

    # During sudden death, only dueling players' answers count for scoring
    if is_correct and (not lobby.sudden_death or user.id in lobby.sudden_death_players):
        player["score"] += 1

    player["answers"].append(answer)

    # Notify all players that this person answered (but not what they answered)
    socketio.emit(
        "player_answered",
        {
            "user_id": user.id,
            "question_index": qi,
            "lobby": lobby.to_dict(),
        },
        room=code,
        namespace="/trivia",
    )

    # If all connected players have answered, reveal immediately
    if _all_answered(lobby):
        _reveal_and_advance(code)


@socketio.on("kick_player", namespace="/trivia")
def on_kick_player(data):
    user = request.environ.get("trivia_user")
    if not user:
        return

    code = str(data.get("code", "")).upper().strip()
    lobby = lobby_games.get(code)

    if not lobby or user.id != lobby.host_user_id:
        emit("error", {"message": "Only the host can kick players"})
        return

    if lobby.status != "waiting":
        emit("error", {"message": "Cannot kick during a game"})
        return

    target_id = data.get("user_id")
    if target_id == user.id:
        return  # Can't kick yourself

    if target_id in lobby.players:
        kicked = lobby.players.pop(target_id)
        # Notify the kicked player
        if kicked.get("sid"):
            socketio.emit(
                "kicked",
                {"reason": "You were removed from the lobby"},
                room=kicked["sid"],
                namespace="/trivia",
            )
        socketio.emit(
            "player_left",
            {
                "user_id": target_id,
                "display_name": kicked["display_name"],
                "lobby": lobby.to_dict(),
            },
            room=code,
            namespace="/trivia",
        )


@socketio.on("end_game", namespace="/trivia")
def on_end_game(data):
    user = request.environ.get("trivia_user")
    if not user:
        return

    code = str(data.get("code", "")).upper().strip()
    lobby = lobby_games.get(code)

    if not lobby:
        emit("error", {"message": "Lobby not found"})
        return

    if user.id != lobby.host_user_id:
        emit("error", {"message": "Only the host can end the game"})
        return

    if lobby.status not in ("playing", "countdown"):
        emit("error", {"message": "No game in progress"})
        return

    _abort_game(code)


@socketio.on("play_again", namespace="/trivia")
def on_play_again(data):
    user = request.environ.get("trivia_user")
    if not user:
        return

    code = str(data.get("code", "")).upper().strip()
    lobby = lobby_games.get(code)

    if not lobby:
        emit("error", {"message": "Lobby not found"})
        return

    if user.id != lobby.host_user_id:
        emit("error", {"message": "Only the host can restart the lobby"})
        return

    if lobby.status != "results":
        emit("error", {"message": "Game is not finished"})
        return

    # Cancel pending cleanup
    lobby._cleanup_pending = False

    # Reset lobby state for a new game
    lobby.status = "waiting"
    lobby.questions = []
    lobby.current_question = -1
    lobby.question_timer = None
    lobby.question_deadline = None
    lobby.question_revealed = False
    lobby.sudden_death = False
    lobby.sudden_death_players = []
    lobby.sudden_death_round = 0

    # Reset player scores and answers, keep connected players
    for uid, p in list(lobby.players.items()):
        if p["connected"]:
            p["score"] = 0
            p["answers"] = []
        else:
            # Remove disconnected players
            lobby.players.pop(uid, None)

    # Update settings if host sent new ones
    new_settings = data.get("settings")
    if new_settings and isinstance(new_settings, dict):
        if "num_questions" in new_settings:
            lobby.settings["num_questions"] = min(max(int(new_settings["num_questions"]), 5), 20)
        if "difficulty" in new_settings:
            lobby.settings["difficulty"] = new_settings["difficulty"]
        if "category" in new_settings:
            lobby.settings["category"] = new_settings["category"]
        if "category_name" in new_settings:
            lobby.settings["category_name"] = new_settings["category_name"]
        if "max_players" in new_settings:
            lobby.settings["max_players"] = min(max(int(new_settings["max_players"]), 2), 12)

    # Update DB lobby status back to waiting
    try:
        from models import TriviaLobby
        from database import db

        db_lobby = TriviaLobby.query.filter_by(code=code).first()
        if db_lobby:
            db_lobby.status = "waiting"
            db_lobby.started_at = None
            db_lobby.finished_at = None
            db.session.commit()
    except Exception as e:
        logger.error("Failed to reset lobby %s in DB: %s", code, e)

    socketio.emit("lobby_reset", {"lobby": lobby.to_dict()}, room=code, namespace="/trivia")
    logger.info("Lobby %s reset for play again by %s", code, user.username)


def register_trivia_rest(app):
    """Register REST endpoints for trivia lobbies on the Flask app.
    Note: This is a no-op — lobby listing is handled inside check_service()
    in webserver.py via the 'trivia-lobbies' service name. This function
    stores the Flask app reference so background tasks can push an app
    context for DB operations."""
    global _flask_app
    _flask_app = app
