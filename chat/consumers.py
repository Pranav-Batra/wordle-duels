import json
from channels.generic.websocket import AsyncWebsocketConsumer
from .word_list import WORDS_SET, ANSWER_WORDS
from . import game_store
import random


word_choices = len(ANSWER_WORDS)

# Shared game and lobby state now lives in Redis (see chat/game_store.py) so it
# is consistent across worker processes and survives restarts. The consumers
# below hold no cross-request state of their own.


def _pick_word():
    return ANSWER_WORDS[random.randint(0, word_choices - 1)]


##every time someone JOINS the lobby -> we check if there is someone else waiting
##-> if so, they get paired and shipped to a game room; else, wait.
class LobbyConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = self.scope['user']
        self.game_type = self.scope['url_route']['kwargs']['mode']

        # Per-user group is how we deliver the "your match is ready" message,
        # regardless of which worker the two players connected to.
        await self.channel_layer.group_add(f'user_{self.user.id}', self.channel_name)
        await self.accept()

        match = await game_store.enqueue_and_match(self.game_type, self.user.id)
        if match:
            room_name, id_a, id_b = match
            for uid in (id_a, id_b):
                await self.channel_layer.group_send(
                    f'user_{uid}',
                    {
                        "type": "game.start",
                        "room_name": room_name,
                        "game_type": self.game_type,
                    },
                )

    async def disconnect(self, close_code):
        await game_store.leave_lobby(self.game_type, self.user.id)
        await self.channel_layer.group_discard(f'user_{self.user.id}', self.channel_name)

    async def game_start(self, event):
        await self.send(text_data=json.dumps(event))


class GameConsumer(AsyncWebsocketConsumer):
    """Speed mode: both players race the same word; first to solve wins."""

    def validate_guess(self, guess):
        return guess in WORDS_SET

    def validation_pattern(self, guess, actual):
        # Standard Wordle coloring: two passes with letter counts so that
        # duplicate letters are colored correctly.
        #   guess  - what the player typed
        #   actual - the answer
        validation_info = dict()
        remaining = dict()  # answer letters still available to match as 'present'

        # Pass 1: exact position matches ('correct'); tally the rest of the answer.
        for i in range(len(guess)):
            if guess[i] == actual[i]:
                validation_info[f'letter_{i}'] = 'correct'
            else:
                remaining[actual[i]] = remaining.get(actual[i], 0) + 1

        # Pass 2: 'present' only while an unmatched copy of the letter remains.
        for i in range(len(guess)):
            cur_letter = f'letter_{i}'
            if cur_letter in validation_info:
                continue
            if remaining.get(guess[i], 0) > 0:
                validation_info[cur_letter] = 'present'
                remaining[guess[i]] -= 1
            else:
                validation_info[cur_letter] = 'absent'

        return validation_info

    async def connect(self):
        self.room_name = self.scope['url_route']['kwargs']['room_name']
        self.user = self.scope['user']
        self.room_group_name = f'chat_{self.room_name}'
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)

        role = await game_store.create_or_join(self.room_name, _pick_word(), 'speed', self.user.username)
        if role is None:
            # Room already has two players; refuse this connection.
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)
            await self.close()
            return

        await self.accept()
        # NB: never send the answer to the client. Only a connect ack.
        await self.send(text_data=json.dumps({"status": "connected", "player": role}))

    async def disconnect(self, close_code):
        await game_store.clear_player(self.room_name, self.user.username)
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def receive(self, text_data):
        text_data_json = json.loads(text_data)
        message = text_data_json.get('message', '')

        game = await game_store.get_game(self.room_name)
        if game is None:
            return  # game already finished / cleaned up
        word = game['word']

        if not self.validate_guess(message):
            await self.channel_layer.send(self.channel_name, {"type": "game.update_dom", "guess": "invalid_guess"})
            return

        pattern = self.validation_pattern(message, word)

        if message != word:
            await self.channel_layer.send(self.channel_name, {
                "type": "game.update_dom",
                "guess": message,
                "how_to_update_dom": pattern,
            })
            await self.channel_layer.group_send(self.room_group_name, {
                "type": "game.update_secondary_dom",
                "how_to_update_dom": pattern,
                "sender": self.channel_name,
            })
        else:
            # Correct guess -> this player wins.
            await self.channel_layer.send(self.channel_name, {
                "type": "game.update_dom",
                "guess": message,
                "how_to_update_dom": pattern,
                "winner": True,
            })
            await self.channel_layer.group_send(self.room_group_name, {
                "type": "game.update_secondary_dom",
                "how_to_update_dom": pattern,
                "sender": self.channel_name,
                "winner": False,  # everyone BUT the sender gets this update
            })
            await game_store.delete_game(self.room_name)
            await self.channel_layer.group_send(self.room_group_name, {"type": "chat.disconnect", "message": "The game is over!"})

    async def game_update_dom(self, event):
        await self.send(text_data=json.dumps(event))

    async def game_update_secondary_dom(self, event):
        if event['sender'] != self.channel_name:
            await self.send(text_data=json.dumps(event))

    async def chat_disconnect(self, event):
        await self.close()


class GuessCountGameConsumer(AsyncWebsocketConsumer):
    """Turn-based mode: players alternate guesses. If p1 solves, p2 gets one
    final turn to force a draw; otherwise the first to solve wins."""

    def validate_guess(self, guess):
        return guess in WORDS_SET

    def validation_pattern(self, guess, actual):
        # Standard Wordle coloring: two passes with letter counts so that
        # duplicate letters are colored correctly.
        validation_info = dict()
        remaining = dict()  # answer letters still available to match as 'present'

        # Pass 1: exact position matches ('correct'); tally the rest of the answer.
        for i in range(len(guess)):
            if guess[i] == actual[i]:
                validation_info[f'letter_{i}'] = 'correct'
            else:
                remaining[actual[i]] = remaining.get(actual[i], 0) + 1

        # Pass 2: 'present' only while an unmatched copy of the letter remains.
        for i in range(len(guess)):
            cur_letter = f'letter_{i}'
            if cur_letter in validation_info:
                continue
            if remaining.get(guess[i], 0) > 0:
                validation_info[cur_letter] = 'present'
                remaining[guess[i]] -= 1
            else:
                validation_info[cur_letter] = 'absent'

        return validation_info

    async def connect(self):
        self.room_name = self.scope['url_route']['kwargs']['room_name']
        self.user = self.scope['user']
        self.room_group_name = f'chat_{self.room_name}'
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)

        role = await game_store.create_or_join(self.room_name, _pick_word(), 'guess', self.user.username, turn='p1')
        if role is None:
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)
            await self.close()
            return

        await self.accept()
        # NB: never send the answer to the client. Only a connect ack.
        await self.send(text_data=json.dumps({"status": "connected", "player": role}))

    async def disconnect(self, close_code):
        await game_store.clear_player(self.room_name, self.user.username)
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def receive(self, text_data):
        text_data_json = json.loads(text_data)
        message = text_data_json.get('message', '')

        game = await game_store.get_game(self.room_name)
        if game is None:
            return  # game already finished / cleaned up

        word = game['word']
        turn = game['turn']
        p1_solved = game['p1_solved']
        username = self.user.username
        is_p1 = game['player1'] == username
        is_p2 = game['player2'] == username

        # Reject invalid words or out-of-turn guesses.
        if not self.validate_guess(message):
            await self.channel_layer.send(self.channel_name, {"type": "game.update_dom", "guess": "invalid_guess"})
            return
        if (is_p1 and turn == 'p2') or (is_p2 and turn == 'p1'):
            await self.channel_layer.send(self.channel_name, {"type": "game.update_dom", "guess": "invalid_guess"})
            return

        pattern = self.validation_pattern(message, word)
        solved = message == word

        if not solved and not p1_solved:
            # Ordinary wrong guess: color both boards and pass the turn.
            await self.channel_layer.send(self.channel_name, {
                "type": "game.update_dom", "guess": message, "how_to_update_dom": pattern,
            })
            await self.channel_layer.group_send(self.room_group_name, {
                "type": "game.update_secondary_dom", "how_to_update_dom": pattern, "sender": self.channel_name,
            })
            await game_store.update(self.room_name, turn=('p1' if turn == 'p2' else 'p2'))

        elif solved and turn == 'p1':
            # p1 solves first: give p2 one final turn to force a draw.
            await game_store.update(self.room_name, p1_solved=True, turn='p2')
            await self.channel_layer.send(self.channel_name, {
                "type": "game.update_dom", "guess": message, "how_to_update_dom": pattern,
            })
            await self.channel_layer.group_send(self.room_group_name, {
                "type": "game.update_secondary_dom", "how_to_update_dom": pattern, "sender": self.channel_name,
            })

        elif p1_solved and turn == 'p2' and solved:
            # p2 also solves -> draw.
            await self.channel_layer.group_send(self.room_group_name, {
                "type": "game.update_dom", "guess": message, "how_to_update_dom": pattern, "draw": True,
            })
            await game_store.delete_game(self.room_name)
            await self.channel_layer.group_send(self.room_group_name, {"type": "chat.disconnect", "message": "The game is over!"})

        elif p1_solved and turn == 'p2' and not solved:
            # p2 misses their final chance -> p1 wins.
            await self.channel_layer.send(self.channel_name, {
                "type": "game.update_dom", "guess": message, "how_to_update_dom": pattern, "winner": False,
            })
            await self.channel_layer.group_send(self.room_group_name, {
                "type": "game.update_secondary_dom", "how_to_update_dom": pattern, "sender": self.channel_name, "winner": True,
            })
            await game_store.delete_game(self.room_name)
            await self.channel_layer.group_send(self.room_group_name, {"type": "chat.disconnect", "message": "The game is over!"})

        elif solved and turn == 'p2':
            # p2 solves while p1 hadn't yet -> p2 wins.
            await self.channel_layer.send(self.channel_name, {
                "type": "game.update_dom", "guess": message, "how_to_update_dom": pattern, "winner": True,
            })
            await self.channel_layer.group_send(self.room_group_name, {
                "type": "game.update_secondary_dom", "how_to_update_dom": pattern, "sender": self.channel_name, "winner": False,
            })
            await game_store.delete_game(self.room_name)
            await self.channel_layer.group_send(self.room_group_name, {"type": "chat.disconnect", "message": "The game is over!"})

    async def game_update_dom(self, event):
        await self.send(text_data=json.dumps(event))

    async def game_update_secondary_dom(self, event):
        if event['sender'] != self.channel_name:
            await self.send(text_data=json.dumps(event))

    async def chat_disconnect(self, event):
        await self.close()
