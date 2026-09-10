import unittest
from collections import Counter

import redis.asyncio as aioredis
from django.conf import settings
from django.test import TestCase

from chat import game_store
from chat.consumers import GameConsumer, GuessCountGameConsumer


def _redis_available():
    import redis
    try:
        redis.from_url(settings.REDIS_URL, socket_connect_timeout=1).ping()
        return True
    except Exception:
        return False


class ValidationPatternTests(TestCase):
    """Wordle coloring must handle duplicate letters correctly.

    ``validation_pattern`` doesn't use ``self``, so we call it with ``None``
    to exercise the real consumer code without a live socket.
    """

    def color(self, guess, actual):
        info = GameConsumer.validation_pattern(None, guess, actual)
        # The guess-count consumer shares the same logic; keep them in lockstep.
        self.assertEqual(info, GuessCountGameConsumer.validation_pattern(None, guess, actual))
        return [info[f"letter_{i}"] for i in range(len(guess))]

    def test_all_correct(self):
        self.assertEqual(self.color("crane", "crane"), ["correct"] * 5)

    def test_all_absent(self):
        self.assertEqual(self.color("crumb", "field"),
                         ["absent", "absent", "absent", "absent", "absent"])

    def test_duplicate_guess_letter_single_answer(self):
        # SPEED vs ABIDE: only one E in the answer -> first E present, second absent.
        self.assertEqual(self.color("speed", "abide"),
                         ["absent", "absent", "present", "absent", "present"])

    def test_greens_consume_before_yellows(self):
        # GEESE vs THESE: the matched E's are greens, so the leading E is absent.
        self.assertEqual(self.color("geese", "these"),
                         ["absent", "absent", "correct", "correct", "correct"])

    def test_invariants_over_word_list(self):
        # For every letter: greens+yellows == min(count in guess, count in answer),
        # and greens land exactly on equal positions. This *defines* correct coloring.
        with open("words_answers.txt") as f:
            words = [w.strip() for w in f if len(w.strip()) == 5]
        sample = words[:150]
        for guess in sample:
            for actual in sample:
                res = self.color(guess, actual)
                for i in range(5):
                    self.assertEqual(res[i] == "correct", guess[i] == actual[i])
                colored = Counter(
                    guess[i] for i in range(5) if res[i] in ("correct", "present")
                )
                gc, ac = Counter(guess), Counter(actual)
                for letter in set(guess):
                    self.assertEqual(colored[letter], min(gc[letter], ac[letter]))


class AnswerSecrecyTests(TestCase):
    """Regression guard for the answer-leak fix: the answer must never appear
    in a client-bound connect payload."""

    def test_source_does_not_send_word_choice_to_client(self):
        import inspect
        src = inspect.getsource(GameConsumer.connect)
        src += inspect.getsource(GuessCountGameConsumer.connect)
        # The previous bug sent the raw answer via these payload keys.
        self.assertNotIn('"word_choice": word_choice', src)
        self.assertNotIn("already_chosen_word", src)


@unittest.skipUnless(_redis_available(), "Redis not reachable")
class GameStoreTests(unittest.IsolatedAsyncioTestCase):
    """The Redis-backed store is what makes game/lobby state consistent across
    workers. Each test uses its own client bound to the test's event loop and
    cleans up its keys, so it never touches real game data."""

    async def asyncSetUp(self):
        game_store._redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        self.room = "test_room_ci"
        self.mode = "test_mode_ci"
        await self._cleanup()

    async def asyncTearDown(self):
        await self._cleanup()
        await game_store._redis.aclose()

    async def _cleanup(self):
        await game_store._redis.delete(game_store._game_key(self.room))
        await game_store._redis.delete(game_store._lobby_key(self.mode))

    async def test_create_join_then_full(self):
        self.assertEqual(await game_store.create_or_join(self.room, "crane", "speed", "alice"), "player1")
        self.assertEqual(await game_store.create_or_join(self.room, "IGNORED", "speed", "bob"), "player2")
        # A third connection is refused rather than overwriting player2.
        self.assertIsNone(await game_store.create_or_join(self.room, "IGNORED", "speed", "carol"))
        game = await game_store.get_game(self.room)
        self.assertEqual(game["word"], "crane")  # creator's word, never clobbered
        self.assertEqual(game["player1"], "alice")
        self.assertEqual(game["player2"], "bob")

    async def test_clear_player_and_delete(self):
        await game_store.create_or_join(self.room, "crane", "speed", "alice")
        await game_store.create_or_join(self.room, "x", "speed", "bob")
        await game_store.clear_player(self.room, "alice")
        game = await game_store.get_game(self.room)
        self.assertEqual(game["player1"], "")
        self.assertEqual(game["player2"], "bob")
        await game_store.delete_game(self.room)
        self.assertIsNone(await game_store.get_game(self.room))

    async def test_update_normalizes_bool(self):
        await game_store.create_or_join(self.room, "crane", "guess", "alice", turn="p1")
        await game_store.update(self.room, p1_solved=True, turn="p2")
        game = await game_store.get_game(self.room)
        self.assertIs(game["p1_solved"], True)
        self.assertEqual(game["turn"], "p2")

    async def test_matchmaking_pairs_two(self):
        # Simulates two players who could be on different workers: the shared
        # Redis queue is what lets them find each other.
        self.assertIsNone(await game_store.enqueue_and_match(self.mode, 1))  # waits
        match = await game_store.enqueue_and_match(self.mode, 2)             # pairs
        self.assertIsNotNone(match)
        room, a, b = match
        self.assertEqual({a, b}, {"1", "2"})
        self.assertEqual(await game_store._redis.llen(game_store._lobby_key(self.mode)), 0)

    async def test_matchmaking_never_self_matches(self):
        self.assertIsNone(await game_store.enqueue_and_match(self.mode, 5))
        # The same user connecting again must not be paired with themselves.
        self.assertIsNone(await game_store.enqueue_and_match(self.mode, 5))
