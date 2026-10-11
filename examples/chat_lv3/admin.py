from django.contrib import admin

from .models import Membership, Message, Room


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ["name", "created_at"]
    search_fields = ["name"]


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ["room", "user", "joined_at"]
    list_filter = ["room"]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ["room", "author", "content", "created_at"]
    list_filter = ["room"]
    search_fields = ["author__username", "content"]
