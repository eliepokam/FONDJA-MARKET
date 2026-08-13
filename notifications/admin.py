from django.contrib import admin
from .models import Notification

# Register your models here.
@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ['utilisateur', 'type', 'canal', 'envoye_le', 'lu_le']
    list_filter = ['type', 'canal']
    search_fields = ['utilisateur__nom', 'utilisateur__telephone']
