from django.contrib import admin
from .models import Lead, Session, Event

class LeadAdmin(admin.ModelAdmin):
    list_display = ('first_name', 'last_name', 'email', 'phone', 'created_at')
    search_fields = ('first_name', 'last_name', 'email', 'phone')

class SessionAdmin(admin.ModelAdmin):
    list_display = ('session_id', 'client_id', 'first_seen', 'last_seen')
    search_fields = ('session_id', 'client_id')

class EventAdmin(admin.ModelAdmin):
    list_display = ('event_id', 'event_type', 'session', 'lead', 'created_at')
    search_fields = ('event_id', 'event_type', 'site_key', 'session__session_id', 'lead__email')
    list_filter = ('event_type', 'site_key')
    raw_id_fields = ('session', 'lead') # For ForeignKey fields

admin.site.register(Lead, LeadAdmin)
admin.site.register(Session, SessionAdmin)
admin.site.register(Event, EventAdmin)
