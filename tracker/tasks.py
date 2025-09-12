# tracker/tasks.py
from celery import shared_task
from .models import Event
from django.conf import settings
import json

try:
    from geoip2.database import Reader
    GEOIP_READER = Reader(getattr(settings, "GEOIP_DATABASE_PATH", ""))
except Exception:
    GEOIP_READER = None

def canonicalize_address(address: str) -> str:
    # TODO: integrate a real normalizer; placeholder passthrough
    return address

@shared_task
def process_event_data(event_pk: int):
    try:
        event = Event.objects.select_related("session","lead").get(pk=event_pk)
    except Event.DoesNotExist:
        return

    # Address canonicalization
    if event.lead and event.lead.property_address:
        norm = canonicalize_address(event.lead.property_address)
        if norm and norm != event.lead.property_address:
            event.lead.property_address = norm
            event.lead.save(update_fields=["property_address"])

    # GeoIP enrichment (server-side IP from Session)
    sess = event.session
    if sess and not sess.location_data and GEOIP_READER and sess.ip_address:
        try:
            resp = GEOIP_READER.city(sess.ip_address)
            geo = {
                "country": resp.country.name,
                "city": resp.city.name,
                "latitude": resp.location.latitude,
                "longitude": resp.location.longitude,
                "ip": sess.ip_address,
            }
            sess.location_data = json.dumps(geo)
            sess.save(update_fields=["location_data"])
        except Exception:
            pass
