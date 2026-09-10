"""Shared game and lobby state, backed by Redis.

Previously the consumers kept everything in module-level Python objects
(``game_states`` dict, two ``deque`` lobby queues). That only works with a
single server process: every worker gets its own private copy, so matchmaking
and in-flight games break the moment there is more than one worker, and all
state is lost on restart. This module moves that state into Redis - which we
already run for the Channels layer - so every worker sees the same thing.

Game state lives in a Redis hash per room (``game:<room>``); the lobby queues
are Redis lists (``lobby:<mode>``). Everything is async so it can be awaited
directly from the async consumers.
"""

import asyncio
import random
import time

import redis.asyncio as aioredis
from django.conf import settings

# One client (with its own connection pool) per process. decode_responses so
# reads come back as str rather than bytes.
_redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)

# Abandoned rooms clean themselves up instead of leaking forever (the old
# in-memory dict never freed entries for games nobody finished).
GAME_TTL_SECONDS = 60 * 60

# Serializes the lobby pop *within this process*. Combined with the atomic
# LPOPs and the guards in enqueue_and_match, matchmaking is also safe if the
# lobby list is ever shared across multiple worker processes.
_lobby_lock = asyncio.Lock()


def _game_key(room):
    return f"game:{room}"


def _lobby_key(mode):
    return f"lobby:{mode}"


# --------------------------------------------------------------------------- #
# Game state
# --------------------------------------------------------------------------- #

async def create_or_join(room, word, game_type, username, turn="p1"):
    """Register ``username`` in ``room``, creating the game if needed.

    The first player to arrive claims the room (via HSETNX on the word, which
    is atomic) and becomes player1; the ``word`` argument is only used by that
    first caller. The second arrival claims player2 (also via HSETNX, so a
    third connection can't overwrite them). Returns ``"player1"``,
    ``"player2"``, or ``None`` if the room is already full.
    """
    key = _game_key(room)
    claimed = await _redis.hsetnx(key, "word", word)
    if claimed:
        # We created the room. Set the remaining fields in one atomic HSET.
        # Note: we deliberately do NOT write player2 here, so we can never
        # clobber a player2 that a fast-joining opponent has already set.
        await _redis.hset(
            key,
            mapping={
                "game_type": game_type,
                "player1": username,
                "turn": turn,
                "p1_solved": "0",
            },
        )
        await _redis.expire(key, GAME_TTL_SECONDS)
        return "player1"

    # Room exists; take the player2 slot only if it is still unclaimed.
    if await _redis.hsetnx(key, "player2", username):
        return "player2"
    return None


async def get_game(room):
    """Return the game state as a dict, or ``None`` if the room doesn't exist.

    ``player1``/``player2`` default to ``""`` when unset, and ``p1_solved`` is
    normalized to a bool, so callers get the same shape the old dict had.
    """
    data = await _redis.hgetall(_game_key(room))
    if not data:
        return None
    data.setdefault("player1", "")
    data.setdefault("player2", "")
    data["p1_solved"] = data.get("p1_solved") == "1"
    return data


async def is_full(room):
    game = await get_game(room)
    return bool(game and game["player1"] and game["player2"])


async def update(room, **fields):
    """Set one or more fields on the game hash (values are stringified)."""
    mapping = {k: ("1" if v is True else "0" if v is False else str(v))
               for k, v in fields.items()}
    await _redis.hset(_game_key(room), mapping=mapping)


async def clear_player(room, username):
    """Empty whichever player slot belongs to ``username`` (on disconnect)."""
    game = await get_game(room)
    if not game:
        return
    if game["player1"] == username:
        await _redis.hset(_game_key(room), "player1", "")
    elif game["player2"] == username:
        await _redis.hset(_game_key(room), "player2", "")


async def delete_game(room):
    await _redis.delete(_game_key(room))


# --------------------------------------------------------------------------- #
# Lobby / matchmaking
# --------------------------------------------------------------------------- #

async def enqueue_and_match(mode, user_id):
    """Add ``user_id`` to the ``mode`` lobby; pair them if someone is waiting.

    Returns ``(room_name, id_a, id_b)`` when a match is formed, else ``None``.
    The caller sends ``game.start`` to both ``user_<id>`` groups.
    """
    key = _lobby_key(mode)
    user_id = str(user_id)
    async with _lobby_lock:
        await _redis.rpush(key, user_id)
        if await _redis.llen(key) < 2:
            return None

        a = await _redis.lpop(key)
        b = await _redis.lpop(key)

        # Another process may have popped one out from under us: put back
        # whatever survived and bail rather than pairing with a ghost.
        if a is None or b is None:
            for survivor in (a, b):
                if survivor is not None:
                    await _redis.lpush(key, survivor)
            return None

        # Never match a user against themselves (e.g. two open tabs).
        if a == b:
            await _redis.rpush(key, a)
            return None

        room = f"{random.randint(0, 999)}_{int(time.time() * 1000)}"
        return room, a, b


async def leave_lobby(mode, user_id):
    """Remove a waiting user from the lobby queue (on disconnect)."""
    await _redis.lrem(_lobby_key(mode), 0, str(user_id))
