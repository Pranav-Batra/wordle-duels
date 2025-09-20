from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.utils.safestring import mark_safe
import json

# Create your views here.
@login_required
def index(request):
    return render(request, 'chat/index.html')

@login_required
def room(request, game_type, room_name):
    return render(request, 'chat/room.html', context={'room_name': room_name, 'game_type': game_type})

@login_required
def lobby(request, game_type):
    return render(request, 'chat/lobby.html', context={'game_type': game_type})

