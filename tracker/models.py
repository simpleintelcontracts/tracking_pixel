# tracker/models.py
from django.db import models
import uuid

class Lead(models.Model):
    first_name = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    last_name  = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    email      = models.EmailField(unique=True, blank=True, null=True)
    phone      = models.CharField(max_length=20, unique=True, blank=True, null=True)
    property_address = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        core = self.email or self.phone or self.property_address or "unknown"
        return f"{self.first_name or ''} {self.last_name or ''} ({core})".strip()


class Session(models.Model):
    session_id   = models.CharField(max_length=255, unique=True, db_index=True)
    client_id    = models.CharField(max_length=255, db_index=True, blank=True, null=True)
    site_key     = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    user_agent   = models.TextField(blank=True, null=True)
    device_info  = models.TextField(blank=True, null=True)
    ip_address   = models.GenericIPAddressField(blank=True, null=True)
    location_data = models.TextField(blank=True, null=True)  # GeoIP data JSON
    first_seen   = models.DateTimeField(auto_now_add=True)
    last_seen    = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.session_id


class Event(models.Model):
    EVENT_PAGE_LOAD     = "page_load"
    EVENT_FORM_SUBMIT   = "form_submission"
    EVENT_CUSTOM        = "custom_event"
    EVENT_TYPE_CHOICES = [
        (EVENT_PAGE_LOAD, "Page Load"),
        (EVENT_FORM_SUBMIT, "Form Submission"),
        (EVENT_CUSTOM, "Custom Event"),
    ]

    id            = models.BigAutoField(primary_key=True)
    event_id      = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True)
    schema_version = models.IntegerField()
    site_key      = models.CharField(max_length=255, db_index=True)
    session       = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="events")
    lead          = models.ForeignKey(Lead, on_delete=models.SET_NULL, blank=True, null=True)
    event_type    = models.CharField(max_length=50, choices=EVENT_TYPE_CHOICES, db_index=True)

    # Page context
    url           = models.URLField(max_length=2048, blank=True, null=True)
    page_title    = models.CharField(max_length=512, blank=True, null=True)
    referrer      = models.URLField(max_length=2048, blank=True, null=True)
    language      = models.CharField(max_length=32, blank=True, null=True)
    tz_offset_min = models.IntegerField(blank=True, null=True)
    viewport      = models.JSONField(blank=True, null=True)
    screen        = models.JSONField(blank=True, null=True)

    # UTMs
    utm_source    = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    utm_medium    = models.CharField(max_length=255, blank=True, null=True)
    utm_campaign  = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    utm_term      = models.CharField(max_length=255, blank=True, null=True)
    utm_content   = models.CharField(max_length=255, blank=True, null=True)

    event_data    = models.JSONField(blank=True, null=True)
    client_ts     = models.DateTimeField(blank=True, null=True)  # optional client timestamp
    created_at    = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["site_key", "created_at"]),
            models.Index(fields=["session", "created_at"]),
        ]

    def __str__(self):
        return f"{self.event_type} - {self.session.session_id}"
