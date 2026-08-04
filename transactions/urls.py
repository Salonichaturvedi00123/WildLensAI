
from django.urls import path, include

from django.conf import settings
from . import views
from django.conf.urls.static import static
from django.contrib.auth.views import LoginView

urlpatterns = [
    path('profile/', views.profile, name='profile'),
     path("login/", LoginView.as_view(template_name="auth/login.html"),name="login"),
    path('logout/', views.logout, name='logout'),
    path('register/', views.register, name='register'),
    path('forgot_password/', views.forgot_password, name='forgot_password'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)