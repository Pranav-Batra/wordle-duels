import json
from asgiref.sync import async_to_sync
from channels.generic.websocket import AsyncWebsocketConsumer
from collections import deque
from .word_list import WORDS_SET, WORDS_LIST, ANSWER_WORDS
import random, time


game_states = dict() ##store room code, word to guess, players
word_choices = len(ANSWER_WORDS)
lobby_members = deque()
##synchronous web socket that accepts all connections, receives messages from its client, and echos those messages back to client
##NOW MAKING IT ASYNC - better performance, practically the same code

##every time someone JOINS the lobby -> we check if there is someone else waiting -> if so , they get paired and shipped to a game room
##else, wait
class LobbyConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = self.scope['user']
        self.lobby_name = 'global_lobby'
        lobby_members.append((self.user, self.channel_name, self.user.id))

        await self.channel_layer.group_add(
            f'user_{self.user.id}',
            self.channel_name
        )
        await self.accept()
        print('WE ARE NOW CONNECTED.')
        print(f'DEQUE: {lobby_members}')

        ##polling lobby queue -> send message to everyone in lobby -> they will be rerouted -> send them the room we create
        if len(lobby_members) > 1:
            print(f'WE ARE CHECKING FOR LOBBY MEMBERS LENGTH.')
            player_1 = lobby_members.pop()
            player_2 = lobby_members.pop()

            room_name = f"{random.randint(0, 999)}_{int(time.time() * 1000)}"

            await self.channel_layer.group_send(
                f'user_{player_1[2]}',
                {
                    "type": "game.start",
                    "room_name": room_name
                }
            )

            await self.channel_layer.group_send(
                f'user_{player_2[2]}',
                {
                    "type": "game.start",
                    "room_name": room_name
                }
            )
        print(lobby_members)

    async def disconnect(self, close_code):
        for member in list(lobby_members):
            if member[2] == self.user.id:
                lobby_members.remove(member)
        print(f'AFTER DISCONNECTION: {lobby_members}')
        await self.channel_layer.group_discard(f'user_{self.user.id}', self.channel_name)


    async def game_start(self, event):
        await self.send(text_data = json.dumps(event))
    
class GameConsumer(AsyncWebsocketConsumer):
    def validate_guess(self, guess):
        return guess in WORDS_SET
    
    def validation_pattern(self, guess, actual):
        #first - guess
        #crash - actual
        validation_info = dict()
        for i in range(len(guess)):
            cur_letter = f'letter_{i}'
            for j in range(len(actual)):
                if guess[i] == actual[j]:
                    if i == j:
                        validation_info[cur_letter] = 'correct'
                        break
                    elif i != j:
                        validation_info[cur_letter] = 'present'
            if cur_letter not in validation_info:
                validation_info[cur_letter] = 'absent'
        
        return validation_info


    async def connect(self):
        print(len(WORDS_SET))
        print(len(ANSWER_WORDS))
        print(f'game_state currently: {game_states}')
        self.room_name = self.scope['url_route']['kwargs']['room_name'] ##obtians room_name parameter from URL route
        self.user = self.scope['user']
        self.room_group_name = f'chat_{self.room_name}' #constructs Channels group name directly from room name
        #Join room group
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        print('channel layer sent')
        if self.room_name in game_states:
            if game_states[self.room_name]['players']['player1'] != '' and game_states[self.room_name]['players']['player2'] != '':
                return
        await self.accept()
        print(f'chnl: {self.channel_name}')
        print(f'room: {self.room_name}')
        print(f'group: {self.room_group_name}')
        print(f'user logged in: {self.user}')
        print('user', self.user)

        if self.room_name not in game_states:
            word_choice = random.randint(0, word_choices - 1)
            word_choice = ANSWER_WORDS[word_choice]
            print('Word choice: ', word_choice)
            game_states[self.room_name] = {'word_choice': word_choice, 'players': {'player1': '', 'player2': ''}}
            game_states[self.room_name]['players']['player1'] = self.user
            await self.send(text_data = json.dumps({"word_choice": word_choice}))
        else:
            already_chosen_word = game_states[self.room_name]['word_choice']
            game_states[self.room_name]['players']['player2'] = self.user
            await self.send(text_data = json.dumps({"message": already_chosen_word})) 
        print(f'game states after joining: {game_states}')
        # print(f'{game_states[self.room_name]['players']['player1'].username}')
        
        # async_to_sync(self.channel_layer.group_add)(
        #     self.room_group_name self.channel_name
        # ) #joins a group -> needs asynctosync wrapper because chat consumer is synchonous but calling async channel layer metho
        
     ##accepts websocket connection(may want to turn this off if user is not authorized to accept the connection)

    #leave room group
    async def disconnect(self, close_code):
        if self.room_name in game_states:
            if self.user.username == game_states[self.room_name]['players']['player1'].username:
                game_states[self.room_name]['players']['player1'] = ''
            elif self.user.username == game_states[self.room_name]['players']['player2'].username:
                game_states[self.room_name]['players']['player2'] = ''
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)
        # async_to_sync(self.channel_layer.group_discard)(
        #     self.room_group_name, self.channel_name
        # ) #leaves a group

    #receive message from websocket
    async def receive(self, text_data):
        text_data_json = json.loads(text_data)
        message = text_data_json['message']

        #send message to room group
        # await self.channel_layer.group_send(self.room_group_name, {"type": "chat.message",s "message": message})
        if not self.validate_guess(message):
            await self.channel_layer.send(self.channel_name, {"type": "game.update_dom", "guess": "invalid_guess"})
        elif self.validate_guess(message) and message != game_states[self.room_name]['word_choice']:
            await self.channel_layer.send(self.channel_name, 
                                          {"type": "game.update_dom", 
                                           "guess": message, 
                                           "how_to_update_dom": 
                                           self.validation_pattern(message, game_states[self.room_name]['word_choice'])})
            await self.channel_layer.group_send(self.room_group_name, {"type": "game.update_secondary_dom", 
                                                                       "how_to_update_dom": 
                                                                       self.validation_pattern(message, game_states[self.room_name]['word_choice']),
                                                                       "sender": self.channel_name})
        elif message == game_states[self.room_name]['word_choice']:
            await self.channel_layer.send(self.channel_name, {"type": "game.update_dom", 
                                                              "guess": message, 
                                                              "how_to_update_dom": 
                                                              self.validation_pattern(message, game_states[self.room_name]['word_choice']),
                                                              "winner": True})
            
            await self.channel_layer.group_send(self.room_group_name, {"type": "game.update_secondary_dom", 
                                                                       "how_to_update_dom": 
                                                                       self.validation_pattern(message, game_states[self.room_name]['word_choice']),
                                                                       "sender": self.channel_name,
                                                                       "winner": False}) ##everyone BUT the sender gets this update
            del game_states[self.room_name]
            await self.channel_layer.group_send(self.room_group_name, {"type": "chat.disconnect", "message": "The game is over!"})
            print("Solved!")
        # async_to_sync(self.channel_layer.group_send)(
        #     self.room_group_name, {"type": "chat.message", "message": message}
        # ) #sends event to group, event. has special type corresponding to name of method that is invoked on consumers receiving this event
        #(replaces . with _, so chat.message becomes function chat_message as seen below)
    
    #receive message from room group
    async def chat_message(self, event):
        message = event['message']

        #send message to WebSocket
        await self.send(text_data = json.dumps({"message": message}))
    
    async def game_update_dom(self, event):
        await self.send(text_data = json.dumps(event))

    async def game_update_secondary_dom(self, event):
        sender = event['sender']
        if sender != self.channel_name:
            await self.send(text_data = json.dumps(event))

    async def chat_disconnect(self, event):
        await self.close()

#when user posts a message, js function transmits message over websocket to chatconsumer
#chatconsumer receives that message and forwards it to group corresponding to the group name
#every chat consumer int he same group/room then receives the mssage and sends it over websocket back to js
##that message is then appended to chat log


