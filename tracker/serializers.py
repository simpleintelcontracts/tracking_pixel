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
    # These fields are now nested within event_data in the JS payload, so remove them as direct serializer fields.
    # client_id and session_id remain as they are always top-level identifiers for session.
    client_id = serializers.CharField(max_length=255, allow_null=True, required=False, write_only=True)
    session_id = serializers.CharField(max_length=255, write_only=True)
    # Removed: user_id, user_email, user_name, as they are now extracted from event_data.

    # Basic
    # Explicitly define 'v' with source mapping and make it required.
    v = serializers.IntegerField(source='schema_version', required=True)
    site_key = serializers.CharField(max_length=255)
    event_id = serializers.UUIDField()  # <-- remove format='hex'
    event_type = serializers.ChoiceField(choices=Event.EVENT_TYPE_CHOICES)

    class Meta:
        model = Event
        fields = (
            "v", "site_key", "event_id", "event_type",
            "event_data", # event_data is now the primary holder of dynamic/meta fields
            # Keep client_id and session_id explicitly as they are critical for session linking.
            "client_id", "session_id",
            # Removed: user_id, user_email, user_name, url, page_title, referrer, language, tz_offset_min,
            # viewport, screen, utm_source, utm_medium, utm_campaign, utm_term, utm_content,
            # device_info, location_data, client_ts as they are now nested in event_data or directly handled.
            "first_name", "last_name", "email", "phone", "property_address",
        )

    # The following fields are now expected to be INSIDE event_data, so remove their direct serializer definitions.
    # We'll access them from event_data in the create method.
    # Removed: url, page_title, referrer, language, tz_offset_min, viewport, screen, utm_source, utm_medium, utm_campaign, utm_term, utm_content, device_info, location_data, client_ts

    # Optional Lead hints - these remain direct fields if they are for specific forms.
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
        # Removed strict primitive check for values within event_data
        # This allows nested JSON objects for custom events like 'input_change'.
        # for k, v in value.items():
        #     if not isinstance(k, str):
        #         raise serializers.ValidationError("event_data keys must be strings.")
        #     if not isinstance(v, (str, int, float, bool, type(None))):
        #         raise serializers.ValidationError(f"event_data['{k}'] must be primitive.")
        return value

    def create(self, validated_data):
        # Pop top-level fields for Event model
        site_key = validated_data.pop("site_key")
        # Access schema_version directly as 'v' is mapped via source.
        schema_version = validated_data.pop("schema_version") 
        event_id = validated_data.pop("event_id")
        event_type = validated_data.pop("event_type")

        # Extract fields for Session or Lead from validated_data and event_data
        client_id = validated_data.pop("client_id", None)
        session_id = validated_data.pop("session_id")

        event_data = validated_data.pop("event_data", {}) or {}

        # Extract identity fields from event_data (they should always be here now)
        user_external_id = event_data.pop("identity_user_id", None)
        user_email = event_data.pop("identity_user_email", None)
        user_name = event_data.pop("identity_user_name", None)

        # Extract meta fields: prioritize top-level (validated_data) then fallback to event_data (meta_ prefix)
        url = validated_data.pop("url", None) or event_data.pop("meta_url", None)
        page_title = validated_data.pop("page_title", None) or event_data.pop("meta_page_title", None)
        referrer = validated_data.pop("referrer", None) or event_data.pop("meta_referrer", None)
        language = validated_data.pop("language", None) or event_data.pop("meta_language", None)
        tz_offset_min = validated_data.pop("tz_offset_min", None) or event_data.pop("meta_tz_offset_min", None)

        viewport_w = validated_data.pop("viewport", {}).get("w") or event_data.pop("meta_vw", None)
        viewport_h = validated_data.pop("viewport", {}).get("h") or event_data.pop("meta_vh", None)
        viewport = {"w": viewport_w, "h": viewport_h} if viewport_w is not None or viewport_h is not None else None

        screen_w = validated_data.pop("screen", {}).get("w") or event_data.pop("meta_sw", None)
        screen_h = validated_data.pop("screen", {}).get("h") or event_data.pop("meta_sh", None)
        screen_dpr = validated_data.pop("screen", {}).get("dpr") or event_data.pop("meta_dpr", None)
        screen = {"w": screen_w, "h": screen_h, "dpr": screen_dpr} if screen_w is not None or screen_h is not None else None

        client_ts_str = validated_data.pop("client_ts", None) or event_data.pop("meta_client_ts", None)
        client_ts = datetime.fromisoformat(client_ts_str.replace("Z", "+00:00")) if client_ts_str else None

        device_info = validated_data.pop("device_info", None)
        location_data = validated_data.pop("location_data", None)

        # UTMs (still at top level in tracker.js buildCore, but keep robust parsing from event_data too)
        utm_source = validated_data.pop("utm_source", None) or event_data.pop("utm_source", None)
        utm_medium = validated_data.pop("utm_medium", None) or event_data.pop("utm_medium", None)
        utm_campaign = validated_data.pop("utm_campaign", None) or event_data.pop("utm_campaign", None)
        utm_term = validated_data.pop("utm_term", None) or event_data.pop("utm_term", None)
        utm_content = validated_data.pop("utm_content", None) or event_data.pop("utm_content", None)

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

        session, created = Session.objects.get_or_create(
            session_id=session_id,
            defaults={
                "client_id": client_id,
                "site_key": site_key,
                "user_agent": ua or device_info, # Fallback to device_info if ua is None
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
        # Update user_agent if new data is available and existing is empty
        if ua and not session.user_agent: session.user_agent = ua; changed = True
        if ip and not session.ip_address: session.ip_address = ip; changed = True
        if user_external_id and not session.user_external_id:
            session.user_external_id = user_external_id; changed = True
        if user_email and user_email != session.user_email:
            session.user_email = user_email.lower(); changed = True
        if user_name and not session.user_name:
            session.user_name = user_name; changed = True
        if changed: session.save()

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
            event_id=event_id,
            schema_version=schema_version,
            site_key=site_key,
            session=session,
            lead=lead,
            url=url,
            page_title=page_title,
            referrer=referrer,
            language=language,
            tz_offset_min=tz_offset_min,
            viewport=viewport,
            screen=screen,
            utm_source=utm_source,
            utm_medium=utm_medium,
            utm_campaign=utm_campaign,
            utm_term=utm_term,
            utm_content=utm_content,
            client_ts=client_ts,
            event_data=event_data, # remaining event_data
            **validated_data # This will be empty now
        )
        return event