from django.contrib import admin
from django.urls import path, re_path
from . import views

urlpatterns = [
    # path('admin/', admin.site.urls),
    path('', views.index, name='chat-home'),
    path('room/<str:game_type>/<str:room_name>/', views.room, name='room'),
    path('lobby/<str:game_type>/', views.lobby, name='lobby')
]
