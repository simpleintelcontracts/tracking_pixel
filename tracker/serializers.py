# tracker/serializers.py
from rest_framework import serializers
from .models import Lead, Session, Event
from django.db import models
from datetime import datetime
import uuid as _uuid

class EventSerializer(serializers.Serializer):
    # Basic
    v = serializers.IntegerField(source='schema_version')
    site_key = serializers.CharField(max_length=255)
    event_id = serializers.UUIDField()  # <-- remove format='hex'
    client_id = serializers.CharField(max_length=255, required=False, allow_blank=True)
    session_id = serializers.CharField(max_length=255)
    event_type = serializers.ChoiceField(choices=Event.EVENT_TYPE_CHOICES)
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

    def create(self, validated):
        # Pull out Session-ish fields
        site_key = validated.pop("site_key")
        schema_version = validated.pop("schema_version")
        event_id = validated.pop("event_id")
        client_id = validated.pop("client_id", None)
        session_id = validated.pop("session_id")
        device_info = validated.pop("device_info", "")
        location_data = validated.pop("location_data", "")

        request = self.context.get("request")
        ip = None
        ua = None
        if request:
            # Prefer proxy header, fall back to REMOTE_ADDR
            xff = request.META.get("HTTP_X_FORWARDED_FOR")
            ip = (xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR"))
            ua = request.META.get("HTTP_USER_AGENT")

        session, created = Session.objects.get_or_create(
            session_id=session_id,
            defaults={
                "client_id": client_id,
                "site_key": site_key,
                "user_agent": ua,
                "device_info": device_info,
                "ip_address": ip,
                "location_data": location_data or None,
            },
        )
        if not created:
            changed = False
            if client_id and not session.client_id:
                session.client_id = client_id; changed = True
            if site_key and not session.site_key:
                session.site_key = site_key; changed = True
            if ua and not session.user_agent:
                session.user_agent = ua; changed = True
            if ip and not session.ip_address:
                session.ip_address = ip; changed = True
            if changed:
                session.save(update_fields=["client_id","site_key","user_agent","ip_address","last_seen"])

        # Lead upsert (email > phone > address+name)
        lead = None
        lead_email = (validated.pop("email", None) or "").lower() or None
        lead_phone = validated.pop("phone", None)
        if lead_phone: lead_phone = "".join(ch for ch in lead_phone if ch.isdigit())
        lead_first = validated.pop("first_name", None)
        lead_last  = validated.pop("last_name", None)
        lead_addr  = validated.pop("property_address", None)

        lead_defaults = {
            "first_name": lead_first or None,
            "last_name": lead_last or None,
            "email": lead_email,
            "phone": lead_phone,
            "property_address": lead_addr or None,
        }
        lead_defaults = {k: v for k, v in lead_defaults.items() if v}

        def _update_lead_fields(_lead):
            changed = False
            for k, v in lead_defaults.items():
                if v and not getattr(_lead, k):
                    setattr(_lead, k, v); changed = True
            if changed: _lead.save()

        if lead_email:
            lead, created = Lead.objects.get_or_create(email=lead_email, defaults=lead_defaults)
            if not created: _update_lead_fields(lead)
        elif lead_phone:
            lead, created = Lead.objects.get_or_create(phone=lead_phone, defaults=lead_defaults)
            if not created: _update_lead_fields(lead)
        elif lead_addr or (lead_first and lead_last):
            q = models.Q()
            if lead_addr: q |= models.Q(property_address=lead_addr)
            if lead_first and lead_last: q |= models.Q(first_name=lead_first, last_name=lead_last)
            if q:
                try:
                    lead = Lead.objects.get(q)
                    _update_lead_fields(lead)
                except Lead.DoesNotExist:
                    if lead_defaults:
                        lead = Lead.objects.create(**lead_defaults)
                except Lead.MultipleObjectsReturned:
                    lead = Lead.objects.filter(q).first()
                    if lead: _update_lead_fields(lead)

        # Client timestamp parsing fallback
        client_ts = validated.get("client_ts")
        if isinstance(client_ts, str):
            try:
                validated["client_ts"] = datetime.fromisoformat(client_ts.replace("Z","+00:00"))
            except Exception:
                validated["client_ts"] = None

        event = Event.objects.create(
            event_id=event_id,
            schema_version=schema_version,
            site_key=site_key,
            session=session,
            lead=lead,
            **validated,
        )
        return event
