from django.db import models

# Create your models here.
from django.db import models
import uuid


class Lead(models.Model):
    first_name = models.CharField(max_length=255, blank=True, null=True)
    last_name = models.CharField(max_length=255, blank=True, null=True)
    email = models.EmailField(unique=True, blank=True, null=True)
    phone = models.CharField(max_length=20, unique=True, blank=True, null=True)
    property_address = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.email})"


class Session(models.Model):
    session_id = models.CharField(max_length=255, unique=True, db_index=True)
    client_id = models.CharField(max_length=255, db_index=True, blank=True, null=True)
    site_key = models.CharField(max_length=255, blank=True, null=True)
    device_info = models.TextField(blank=True, null=True)
    location_data = models.TextField(blank=True, null=True)  # GeoIP data
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.session_id


class Event(models.Model):
    EVENT_TYPE_CHOICES = [
        ('page_load', 'Page Load'),
        ('form_submission', 'Form Submission'),
        ('custom_event', 'Custom Event'),
    ]

    event_id = models.UUIDField(default=uuid.uuid4, unique=True, db_index=True)
    schema_version = models.IntegerField()
    site_key = models.CharField(max_length=255)
    session = models.ForeignKey(Session, on_delete=models.CASCADE)
    lead = models.ForeignKey(Lead, on_delete=models.SET_NULL, blank=True, null=True)
    event_type = models.CharField(max_length=50, choices=EVENT_TYPE_CHOICES)
    url = models.URLField(max_length=2048, blank=True, null=True)
    referrer = models.URLField(max_length=2048, blank=True, null=True)
    utm_source = models.CharField(max_length=255, blank=True, null=True)
    utm_medium = models.CharField(max_length=255, blank=True, null=True)
    utm_campaign = models.CharField(max_length=255, blank=True, null=True)
    utm_term = models.CharField(max_length=255, blank=True, null=True)
    utm_content = models.CharField(max_length=255, blank=True, null=True)
    event_data = models.JSONField(blank=True, null=True)  # Store all captured fields
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.event_type} - {self.session.session_id}"
