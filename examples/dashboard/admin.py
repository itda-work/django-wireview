from django.contrib import admin

from .models import Activity, Stat


@admin.register(Stat)
class StatAdmin(admin.ModelAdmin):
    list_display = ["name", "label", "value", "change_percent", "updated_at"]
    search_fields = ["name", "label"]


@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    list_display = ["type", "user_name", "description", "created_at"]
    list_filter = ["type"]
    search_fields = ["user_name", "description"]
