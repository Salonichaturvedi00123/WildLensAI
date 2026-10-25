from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('predict/', views.predict, name='predict'),
    path('contact/', views.contact, name='contact'),
    path('history/', views.history, name='history'),
    path('predictions/<int:prediction_id>/', views.prediction_detail, name='prediction_detail'),
    path('assistant/chat/', views.assistant_chat, name='assistant_chat'),
]
