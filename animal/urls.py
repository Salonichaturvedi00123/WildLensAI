
from django.urls import path, include

from django.conf import settings
from . import views
from django.conf.urls.static import static

urlpatterns = [
    # path('', views.base, name='base'),
   path('', views.home, name='home'),
   path('predict/', views.predict, name='predict'),
   path('contact/', views.contact, name='contact'),
   path('history/', views.history, name='history'),

]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)