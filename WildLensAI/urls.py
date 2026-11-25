"""
URL configuration for WildLensAI project.
"""
from django.contrib import admin
from django.urls import path, include
from animal.views import health_check, protected_media

urlpatterns = [
    path('health/', health_check, name='health_check'),
    path('media/<path:media_path>', protected_media, name='protected_media'),
    path('admin/', admin.site.urls),
    path('', include('animal.urls')),
    path('transactions/', include('transactions.urls')),
]
