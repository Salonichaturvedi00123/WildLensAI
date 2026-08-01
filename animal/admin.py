from django.contrib import admin
from .models import AnimalPrediction, Contact



# Register your models here.
class AnimalPredictionAdmin(admin.ModelAdmin):
    list_display = ('species', 'confidence', 'scientific_name', 'credit_used', 'created_at')
    list_filter = ('species', 'created_at')
    search_fields = ('species', 'scientific_name')
    ordering = ('-created_at',)

admin.site.register(AnimalPrediction, AnimalPredictionAdmin)

class ContactAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'subject', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('name', 'email', 'subject')
    ordering = ('-created_at',)

admin.site.register(Contact, ContactAdmin)