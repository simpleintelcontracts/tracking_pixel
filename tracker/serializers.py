
from rest_framework import serializers
from .models import Lead, Session, Event
from django.db import models
import uuid # Import uuid for event_id validation


class EventSerializer(serializers.Serializer):
    v = serializers.IntegerField(source='schema_version') # Map 'v' from client to 'schema_version' model field
    site_key = serializers.CharField(max_length=255)
    event_id = serializers.UUIDField(default=uuid.uuid4, format='hex')
    client_id = serializers.CharField(max_length=255, required=False, allow_blank=True)
    session_id = serializers.CharField(max_length=255)
    event_type = serializers.ChoiceField(choices=Event.EVENT_TYPE_CHOICES)
    url = serializers.URLField(max_length=2048, required=False, allow_blank=True)
    referrer = serializers.URLField(max_length=2048, required=False, allow_blank=True)
    utm_source = serializers.CharField(max_length=255, required=False, allow_blank=True)
    utm_medium = serializers.CharField(max_length=255, required=False, allow_blank=True)
    utm_campaign = serializers.CharField(max_length=255, required=False, allow_blank=True)
    utm_term = serializers.CharField(max_length=255, required=False, allow_blank=True)
    utm_content = serializers.CharField(max_length=255, required=False, allow_blank=True)
    device_info = serializers.CharField(required=False, allow_blank=True)
    location_data = serializers.CharField(required=False, allow_blank=True)
    event_data = serializers.JSONField(required=False, allow_null=True)

    # Fields for Lead model (if present in form submission)
    first_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    property_address = serializers.CharField(max_length=255, required=False, allow_blank=True)

    def validate_event_data(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Event data must be a dictionary.")

        event_type = self.initial_data.get('event_type')

        if event_type == 'form_submission':
            # For form submissions, ensure all values are simple types (strings, numbers, booleans)
            for key, val in value.items():
                if not isinstance(key, str):
                    raise serializers.ValidationError(f"Event data keys must be strings. Found type {type(key)} for key {key}.")
                if not isinstance(val, (str, int, float, bool, type(None))):
                    raise serializers.ValidationError(f"Event data value for '{key}' must be a simple type (string, number, boolean, or null). Found type {type(val)}.")
            # Further, more specific validation for expected form fields could be added here
            # Example: if 'email' in value and not re.match(r"[^@]+@[^@]+\.[^@]+", value['email']):
            #     raise serializers.ValidationError({'event_data': {'email': 'Invalid email format.'}})

        elif event_type == 'page_load':
            # Page load event data is typically minimal or empty
            if value and len(value) > 0:
                # Consider if any specific fields are expected for page_load event_data
                # For now, a warning or stricter check can be implemented.
                print("Warning: Unexpected event_data for page_load event.")

        # For custom_event, validation could be more flexible but still ensure structure
        return value

    def create(self, validated_data):
        # Pop new fields for Event model
        event_id = validated_data.pop('event_id')
        schema_version = validated_data.pop('schema_version')
        site_key = validated_data.pop('site_key')
        client_id = validated_data.pop('client_id', None)

        session_id = validated_data.pop('session_id')
        session, created = Session.objects.get_or_create(
            session_id=session_id,
            defaults={
                'client_id': client_id,
                'site_key': site_key, # Pass site_key to Session
                'device_info': validated_data.pop('device_info', ''),
                'location_data': validated_data.pop('location_data', ''),
            }
        )
        # Update client_id and site_key if session already exists and fields are provided and not set
        if not created:
            updated_fields = []
            if client_id and not session.client_id:
                session.client_id = client_id
                updated_fields.append('client_id')
            if site_key and not session.site_key:
                session.site_key = site_key
                updated_fields.append('site_key')
            if updated_fields:
                session.save(update_fields=updated_fields)

        lead = None
        lead_email = validated_data.pop('email', None)
        lead_phone = validated_data.pop('phone', None)
        lead_first_name = validated_data.pop('first_name', None)
        lead_last_name = validated_data.pop('last_name', None)
        lead_property_address = validated_data.pop('property_address', None)

        # Canonicalize email (lowercase)
        if lead_email: lead_email = lead_email.lower()

        # Canonicalize phone (E.164 - placeholder, requires a library like phonenumbers)
        # For now, just remove non-digits for basic normalization.
        if lead_phone: lead_phone = ''.join(filter(str.isdigit, lead_phone)) # Placeholder

        lead_data_for_create = {
            'first_name': lead_first_name,
            'last_name': lead_last_name,
            'email': lead_email,
            'phone': lead_phone,
            'property_address': lead_property_address,
        }
        lead_data_for_create = {k: v for k, v in lead_data_for_create.items() if v is not None and v != ''}

        # Deduplicate/Merge Lead based on precedence: email > phone > (address + name)
        if lead_email:
            lead, created = Lead.objects.get_or_create(email=lead_email, defaults=lead_data_for_create)
            if not created: # Update existing lead with new info
                for key, value in lead_data_for_create.items():
                    if value and getattr(lead, key, None) is None:
                        setattr(lead, key, value)
                lead.save()
        elif lead_phone:
            lead, created = Lead.objects.get_or_create(phone=lead_phone, defaults=lead_data_for_create)
            if not created: # Update existing lead with new info
                for key, value in lead_data_for_create.items():
                    if value and getattr(lead, key, None) is None:
                        setattr(lead, key, value)
                lead.save()
        elif lead_property_address or (lead_first_name and lead_last_name):
            # Try to find by address or full name, or create if not found
            query_params = models.Q()
            if lead_property_address: query_params |= models.Q(property_address=lead_property_address)
            if lead_first_name and lead_last_name: query_params |= models.Q(first_name=lead_first_name, last_name=lead_last_name)

            if query_params:
                try:
                    lead = Lead.objects.get(query_params)
                    # Update existing lead with new info
                    for key, value in lead_data_for_create.items():
                        if value and getattr(lead, key, None) is None:
                            setattr(lead, key, value)
                    lead.save()
                except Lead.DoesNotExist:
                    if lead_data_for_create: # Ensure there's data to create with
                        lead = Lead.objects.create(**lead_data_for_create)
                except Lead.MultipleObjectsReturned:
                    # PROPOSAL: For a more robust solution, this logic should be moved to a dedicated Lead deduplication/merging service.
                    # For now, we will log all matching lead IDs and attempt to update the first one found.
                    matching_leads = Lead.objects.filter(query_params)
                    matching_lead_ids = [str(l.id) for l in matching_leads]
                    print(f"Multiple leads found for query: {query_params}. Matching IDs: {', '.join(matching_lead_ids)}. Attempting to update the first one.")
                    lead = matching_leads.first()
                    if lead:
                        for key, value in lead_data_for_create.items():
                            if value and getattr(lead, key, None) is None:
                                setattr(lead, key, value)
                        lead.save()

        event = Event.objects.create(
            event_id=event_id,
            schema_version=schema_version,
            site_key=site_key,
            session=session,
            lead=lead,
            **validated_data
        )
        return event 