# tracker/tasks.py
from celery import shared_task
from .models import Event, Lead, Session
from django.conf import settings
import json
import os

# Try to import geoip2, but allow tasks to run without it if not installed
try:
    from geoip2.database import Reader
    GEOIP_READER = Reader(settings.GEOIP_DATABASE_PATH)
except ImportError:
    print("Warning: geoip2 library not installed. GeoIP enrichment will be skipped.")
    GEOIP_READER = None
except Exception as e:
    print(f"Warning: Could not load GeoIP2 database: {e}. GeoIP enrichment will be skipped.")
    GEOIP_READER = None


def extract_ip_from_device_info(device_info):
    # This is a placeholder. In a real scenario, device_info might contain the IP
    # or the IP would be passed separately from the request.META in the CollectView
    # For now, let's assume the IP is directly available in a field in `device_info` JSON or a dedicated field.
    # For this example, we'll just extract a dummy IP for demonstration if not available.
    if device_info and 'ip_address' in device_info:
        try:
            ip_data = json.loads(device_info)
            return ip_data.get('ip_address')
        except json.JSONDecodeError:
            pass
    # Fallback to a loopback address or None if not found/parsed
    return "127.0.0.1" # Or actual IP if available from request


def canonicalize_address(address):
    # Placeholder for a dedicated address canonicalization/geocoding service.
    # This function would take a raw address string and return a standardized version
    # and potentially other structured address components (city, state, zip, lat/lng).
    # For demonstration, we'll just return the address as is.
    print(f"Canonicalizing address: {address}")
    # Example of a real integration:
    # from .services import AddressNormalizationService
    # return AddressNormalizationService.normalize(address)
    return address

@shared_task
def process_event_data(event_id):
    try:
        event = Event.objects.get(id=event_id)
    except Event.DoesNotExist:
        print(f"Event with id {event_id} not found.")
        return

    # The serializer's create method already handles initial session and lead deduplication/creation
    # based on client_id, session_id, email, phone, and property address with precedence.
    # Further normalization and data cleaning can happen here.

    # --- Advanced Lead Data Processing (if applicable from form submissions) ---
    if event.lead and event.lead.property_address:
        # Canonicalize the property address if it hasn't been already
        if not hasattr(event.lead, '_canonicalized_address') or not event.lead._canonicalized_address:
            event.lead.property_address = canonicalize_address(event.lead.property_address)
            # In a real system, you might store canonicalized components separately or set a flag
            event.lead.save(update_fields=['property_address'])
            event.lead._canonicalized_address = True # Mark as canonicalized for this task run

    # --- GeoIP Enrichment ---
    # If session location data is missing, attempt to enrich it using GeoIP based on the client's IP.
    # The IP address should ideally be extracted from the request in the CollectView and passed to the serializer,
    # and then stored in the Session model (e.g., in a dedicated `ip_address` field).
    # For this example, we'll temporarily try to extract from device_info as a fallback.
    if event.session and not event.session.location_data and GEOIP_READER:
        client_ip = extract_ip_from_device_info(event.session.device_info) # Needs proper IP extraction
        if client_ip:
            print(f"Attempting GeoIP enrichment for session {event.session.session_id} with IP: {client_ip}")
            try:
                response = GEOIP_READER.city(client_ip)
                geo_data = {
                    'country': response.country.name,
                    'city': response.city.name,
                    'latitude': response.location.latitude,
                    'longitude': response.location.longitude,
                    'ip': client_ip
                }
                event.session.location_data = json.dumps(geo_data)
                event.session.save(update_fields=['location_data'])
                print(f"GeoIP data enriched for session {event.session.session_id}")
            except Exception as e:
                print(f"GeoIP lookup failed for IP {client_ip}: {e}")
        else:
            print(f"Could not extract client IP for GeoIP enrichment for session {event.session.session_id}")

    print(f"Processed event {event.event_id} (schema_version: {event.schema_version}, site_key: {event.site_key}): {event.event_type} for session {event.session.session_id} (client: {event.session.client_id})")
        