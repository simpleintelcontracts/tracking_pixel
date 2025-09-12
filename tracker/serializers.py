# tracker/serializers.py
from rest_framework import serializers
from .models import Lead, Session, Event
from django.db import models
from datetime import datetime


class SessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Session
        fields = (
            "session_id", "client_id", "site_key", "user_agent", "device_info",
            "ip_address", "location_data", "first_seen", "last_seen",
            "user_external_id", "user_email", "user_name",
        )


class EventSerializer(serializers.ModelSerializer):
    # Always top-level identifiers:
    client_id = serializers.CharField(max_length=255, allow_null=True, required=False, write_only=True)
    session_id = serializers.CharField(max_length=255, write_only=True)

    # Basic event fields
    v = serializers.IntegerField(source="schema_version", required=True)
    site_key = serializers.CharField(max_length=255)
    event_id = serializers.UUIDField()
    event_type = serializers.ChoiceField(choices=Event.EVENT_TYPE_CHOICES)

    # Optional lead hints (kept flat)
    first_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    property_address = serializers.CharField(max_length=255, required=False, allow_blank=True)

    class Meta:
        model = Event
        fields = (
            "v", "site_key", "event_id", "event_type",
            "event_data",
            "client_id", "session_id",
            "first_name", "last_name", "email", "phone", "property_address",
        )

    def validate_event_data(self, value):
        if value is None:
            return value
        if not isinstance(value, dict):
            raise serializers.ValidationError("event_data must be an object.")
        return value

    def create(self, validated_data):
        # Required basics
        site_key = validated_data.pop("site_key")
        schema_version = validated_data.pop("schema_version")
        event_id = validated_data.pop("event_id")
        event_type = validated_data.pop("event_type") or Event.EVENT_PAGE_LOAD

        # IDs
        client_id = validated_data.pop("client_id", None)
        session_id = validated_data.pop("session_id")

        # Everything else comes from event_data
        event_data = validated_data.pop("event_data", {}) or {}

        # Identity (provided by tracker into event_data)
        user_external_id = event_data.pop("identity_user_id", None)
        user_email       = event_data.pop("identity_user_email", None)
        user_name        = event_data.pop("identity_user_name", None)

        # Page/meta (prefer event_data meta_* first)
        url         = event_data.pop("meta_url", None) or event_data.get("url")
        page_title  = event_data.pop("meta_page_title", None)
        referrer    = event_data.pop("meta_referrer", None)
        language    = event_data.pop("meta_language", None)
        tz_offset_min = event_data.pop("meta_tz_offset_min", None)

        vw = event_data.pop("meta_vw", None)
        vh = event_data.pop("meta_vh", None)
        viewport = {"w": vw, "h": vh} if vw is not None or vh is not None else None

        sw  = event_data.pop("meta_sw", None)
        sh  = event_data.pop("meta_sh", None)
        dpr = event_data.pop("meta_dpr", None)
        screen = {"w": sw, "h": sh, "dpr": dpr} if sw is not None or sh is not None or dpr is not None else None

        client_ts_str = event_data.pop("meta_client_ts", None)
        client_ts = datetime.fromisoformat(client_ts_str.replace("Z", "+00:00")) if client_ts_str else None

        # UTMs (in event_data)
        utm_source   = event_data.pop("utm_source", None)
        utm_medium   = event_data.pop("utm_medium", None)
        utm_campaign = event_data.pop("utm_campaign", None)
        utm_term     = event_data.pop("utm_term", None)
        utm_content  = event_data.pop("utm_content", None)

        # Lead hints (kept flat)
        lead_data = {
            "first_name": validated_data.pop("first_name", None),
            "last_name": validated_data.pop("last_name", None),
            "email": validated_data.pop("email", None),
            "phone": validated_data.pop("phone", None),
            "property_address": validated_data.pop("property_address", None),
        }

        # Request env
        request = self.context.get("request")
        ip = ua = None
        if request:
            xff = request.META.get("HTTP_X_FORWARDED_FOR")
            ip = (xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR"))
            ua = request.META.get("HTTP_USER_AGENT")

        # Upsert session
        session, created = Session.objects.get_or_create(
            session_id=session_id,
            defaults={
                "client_id": client_id,
                "site_key": site_key,
                "user_agent": ua,
                "device_info": ua,  # mirror UA (tracker may also send user agent-like device_info)
                "ip_address": ip,
                "location_data": None,
                "user_external_id": user_external_id,
                "user_email": user_email.lower() if user_email else None,
                "user_name": user_name,
            },
        )

        changed = False
        if client_id and not session.client_id:
            session.client_id = client_id; changed = True
        if site_key and not session.site_key:
            session.site_key = site_key; changed = True
        if ua and not session.user_agent:
            session.user_agent = ua; changed = True
        if ip and not session.ip_address:
            session.ip_address = ip; changed = True
        if user_external_id and not session.user_external_id:
            session.user_external_id = user_external_id; changed = True
        if user_email and user_email.lower() != (session.user_email or ""):
            session.user_email = user_email.lower(); changed = True
        if user_name and not session.user_name:
            session.user_name = user_name; changed = True
        if changed:
            session.save()

        # Upsert/attach lead (very light heuristic)
        lead = None
        cleaned_lead = {k: v for k, v in lead_data.items() if v}
        if cleaned_lead:
            lead_filter = models.Q()
            if cleaned_lead.get("email"):
                lead_filter |= models.Q(email__iexact=cleaned_lead["email"])
            if cleaned_lead.get("phone"):
                lead_filter |= models.Q(phone=cleaned_lead["phone"])
            if cleaned_lead.get("property_address"):
                lead_filter |= models.Q(property_address__iexact=cleaned_lead["property_address"])
            elif cleaned_lead.get("first_name") and cleaned_lead.get("last_name"):
                lead_filter |= models.Q(first_name__iexact=cleaned_lead["first_name"],
                                        last_name__iexact=cleaned_lead["last_name"])
            if lead_filter:
                try:
                    lead = Lead.objects.get(lead_filter)
                    for k, v in cleaned_lead.items():
                        setattr(lead, k, v)
                    lead.save()
                except Lead.DoesNotExist:
                    lead = Lead.objects.create(**cleaned_lead)

        # Create event
        event = Event.objects.create(
            event_id=event_id,
            schema_version=schema_version,
            site_key=site_key,
            session=session,
            lead=lead,
            event_type=event_type,
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
            event_data=event_data,  # whatever remains (form fields, custom_event payload, etc.)
        )
        return event
