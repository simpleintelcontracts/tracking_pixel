# tracker/serializers.py
from rest_framework import serializers
from .models import Lead, Session, Event
from django.db import models
from datetime import datetime
import uuid as _uuid

class SessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Session
        fields = (
            "session_id", "client_id", "site_key", "user_agent", "device_info",
            "ip_address", "location_data", "first_seen", "last_seen",
            "user_external_id", "user_email", "user_name",
        )


class EventSerializer(serializers.ModelSerializer):
    # Re-add session-related fields for validation
    client_id = serializers.CharField(max_length=255, allow_null=True, required=False, write_only=True)
    session_id = serializers.CharField(max_length=255, write_only=True)
    user_id = serializers.CharField(max_length=255, allow_null=True, required=False, write_only=True)
    user_email = serializers.EmailField(allow_null=True, required=False, write_only=True)
    user_name = serializers.CharField(max_length=255, allow_null=True, required=False, write_only=True)

    # Basic
    v = serializers.IntegerField(source='schema_version')
    site_key = serializers.CharField(max_length=255)
    event_id = serializers.UUIDField()  # <-- remove format='hex'
    event_type = serializers.ChoiceField(choices=Event.EVENT_TYPE_CHOICES)

    class Meta:
        model = Event
        fields = (
            "v", "site_key", "event_id", "event_type",
            "url", "page_title", "referrer", "language", "tz_offset_min",
            "viewport", "screen", "utm_source", "utm_medium", "utm_campaign",
            "utm_term", "utm_content", "device_info", "location_data", "client_ts",
            "event_data",
            # Fields that are not directly on Event model but are handled in create()
            "client_id", "session_id", "user_id", "user_email", "user_name",
            "first_name", "last_name", "email", "phone", "property_address",
        )

    # Page/meta
    url = serializers.URLField(max_length=2048, required=False, allow_blank=True)
    page_title = serializers.CharField(max_length=512, required=False, allow_blank=True)
    referrer = serializers.URLField(max_length=2048, required=False, allow_blank=True)
    language = serializers.CharField(max_length=32, required=False, allow_blank=True)
    tz_offset_min = serializers.IntegerField(required=False)
    viewport = serializers.JSONField(required=False)
    screen = serializers.JSONField(required=False)

    # UTMs
    utm_source = serializers.CharField(max_length=255, required=False, allow_blank=True)
    utm_medium = serializers.CharField(max_length=255, required=False, allow_blank=True)
    utm_campaign = serializers.CharField(max_length=255, required=False, allow_blank=True)
    utm_term = serializers.CharField(max_length=255, required=False, allow_blank=True)
    utm_content = serializers.CharField(max_length=255, required=False, allow_blank=True)

    # Device info
    device_info = serializers.CharField(required=False, allow_blank=True)
    location_data = serializers.CharField(required=False, allow_blank=True)

    # Client clock
    client_ts = serializers.DateTimeField(required=False)

    # Payload
    event_data = serializers.JSONField(required=False, allow_null=True)

    # Optional Lead hints
    first_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    property_address = serializers.CharField(max_length=255, required=False, allow_blank=True)

    def validate_event_data(self, value):
        if value is None:
            return value
        if not isinstance(value, dict):
            raise serializers.ValidationError("event_data must be an object.")
        # Basic safety: only primitives
        for k, v in value.items():
            if not isinstance(k, str):
                raise serializers.ValidationError("event_data keys must be strings.")
            if not isinstance(v, (str, int, float, bool, type(None))):
                raise serializers.ValidationError(f"event_data['{k}'] must be primitive.")
        return value

    def create(self, validated_data):
        # Pop all fields that belong to Session or Lead before creating Event
        # These are now direct fields on EventSerializer, so pop directly by their names.
        client_id = validated_data.pop("client_id", None)
        session_id = validated_data.pop("session_id")
        user_external_id = validated_data.pop("user_id", None)
        user_email = validated_data.pop("user_email", None)
        user_name = validated_data.pop("user_name", None)

        site_key = validated_data.pop("site_key") # Event also uses site_key, so pop here for session
        device_info = validated_data.pop("device_info", "")
        location_data = validated_data.pop("location_data", None)

        lead_data = {
            "first_name": validated_data.pop("first_name", None),
            "last_name": validated_data.pop("last_name", None),
            "email": validated_data.pop("email", None),
            "phone": validated_data.pop("phone", None),
            "property_address": validated_data.pop("property_address", None),
        }

        request = self.context.get("request")
        ip = None; ua = None
        if request:
            xff = request.META.get("HTTP_X_FORWARDED_FOR")
            ip = (xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR"))
            ua = request.META.get("HTTP_USER_AGENT")

        # Create or get Session
        session, created = Session.objects.get_or_create(
            session_id=session_id,
            defaults={
                "client_id": client_id,
                "site_key": site_key,
                "user_agent": ua,
                "device_info": device_info,
                "ip_address": ip,
                "location_data": location_data,
                "user_external_id": user_external_id,
                "user_email": user_email,
                "user_name": user_name,
            },
        )

        # Update session identity if newly provided
        changed = False
        if client_id and not session.client_id: session.client_id = client_id; changed = True
        if site_key and not session.site_key: session.site_key = site_key; changed = True
        if ua and not session.user_agent: session.user_agent = ua; changed = True
        if ip and not session.ip_address: session.ip_address = ip; changed = True
        if user_external_id and not session.user_external_id:
            session.user_external_id = user_external_id; changed = True
        if user_email and user_email != session.user_email:
            session.user_email = user_email.lower(); changed = True
        if user_name and not session.user_name:
            session.user_name = user_name; changed = True
        if changed: session.save()

        # Lead upsert logic
        lead = None
        cleaned_lead_data = {k: v for k, v in lead_data.items() if v}

        if cleaned_lead_data:
            lead_filter = models.Q()
            if cleaned_lead_data.get("email"): lead_filter |= models.Q(email__iexact=cleaned_lead_data["email"])
            if cleaned_lead_data.get("phone"): lead_filter |= models.Q(phone=cleaned_lead_data["phone"])
            if cleaned_lead_data.get("property_address"): lead_filter |= models.Q(property_address__iexact=cleaned_lead_data["property_address"])
            elif cleaned_lead_data.get("first_name") and cleaned_lead_data.get("last_name"): lead_filter |= models.Q(first_name__iexact=cleaned_lead_data["first_name"], last_name__iexact=cleaned_lead_data["last_name"])

            if lead_filter: 
                try:
                    lead = Lead.objects.get(lead_filter)
                    for k, v in cleaned_lead_data.items():
                        setattr(lead, k, v)
                    lead.save()
                except Lead.DoesNotExist:
                    lead = Lead.objects.create(**cleaned_lead_data)

        event = Event.objects.create(
            event_id=validated_data.pop("event_id"),
            schema_version=validated_data.pop("schema_version"),
            site_key=site_key,
            session=session,
            lead=lead,
            **validated_data
        )
        return event