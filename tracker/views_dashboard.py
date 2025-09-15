from datetime import timedelta, date
import csv
import json

from django.db import models
from django.db.models import (
    Count, Q, Value, CharField, IntegerField, FloatField, TextField, F, Func
)
from django.db.models.functions import Coalesce, TruncDate, Cast
from django.shortcuts import render
from django.http import HttpResponse

from .models import Event, Lead, Session


# --- Helpers ---------------------------------------------------------------

def jsonb_text(field, *keys):
    """
    Extract TEXT from a JSON/JSONB field path using Postgres jsonb_extract_path_text.
    Works for JSONB and JSONField columns (Django will cast to JSONB in PG).
    """
    return Func(
        field, *[Value(k) for k in keys],
        function='jsonb_extract_path_text',
        output_field=TextField(),
    )


def nullif_empty(expr):
    """
    Produces NULLIF(expr, '') so empty strings don't break numeric casts.
    """
    return Func(
        expr, Value(''),
        function='NULLIF',
        template='NULLIF(%(expressions)s)',
        output_field=TextField(),  # result is text or NULL
    )


def as_int(expr):
    """
    Cast a text (or text-y) SQL expression to INTEGER safely:
    NULLIF handles '' and Cast handles numeric text; returns IntegerField.
    """
    return Cast(nullif_empty(expr), IntegerField())


def as_float(expr):
    """
    Cast a text (or text-y) SQL expression to FLOAT safely:
    NULLIF handles '' and Cast handles numeric text; returns FloatField.
    """
    return Cast(nullif_empty(expr), FloatField())


# --- View ------------------------------------------------------------------

def dashboard(request):
    # --- Date range defaults (last 14 days) ---
    today = date.today()
    two_weeks_ago = today - timedelta(days=14)
    from_date_str = request.GET.get('from', two_weeks_ago.isoformat())
    to_date_str = request.GET.get('to', today.isoformat())
    from_date = date.fromisoformat(from_date_str)
    to_date = date.fromisoformat(to_date_str)

    # --- Site Key filter options ---
    site_keys = (
        Event.objects.exclude(site_key__isnull=True)
        .values_list('site_key', flat=True)
        .distinct()
        .order_by('site_key')
    )
    active_site_key = request.GET.get('site_key', '')

    # --- Base querysets ---
    events_base_qs = Event.objects.all()
    leads_base_qs = Lead.objects.all()
    sessions_base_qs = Session.objects.all()

    if active_site_key:
        events_base_qs = events_base_qs.filter(site_key=active_site_key)
        leads_base_qs = leads_base_qs.filter(event__site_key=active_site_key).distinct()
        sessions_base_qs = sessions_base_qs.filter(site_key=active_site_key)

    # --- Date filter ---
    events_qs = events_base_qs.filter(created_at__date__range=[from_date, to_date])
    leads_qs = leads_base_qs.filter(created_at__date__range=[from_date, to_date])
    sessions_qs = sessions_base_qs.filter(first_seen__date__range=[from_date, to_date])

    # --- CSV export ---
    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="events_export.csv"'
        writer = csv.writer(response)
        writer.writerow([
            'Event UUID', 'Event Type', 'Site Key', 'Session ID', 'Client ID',
            'URL', 'Page Title', 'Referrer', 'UTM Source', 'UTM Campaign', 'Created At'
        ])
        for e in events_qs.select_related('session'):
            ed = e.event_data or {}
            url = e.url or ed.get('meta_url', '')
            page_title = e.page_title or ed.get('meta_page_title', '')
            referrer = e.referrer or ed.get('meta_referrer', '')
            utm_source = e.utm_source or ed.get('utm_source', '')
            utm_campaign = e.utm_campaign or ed.get('utm_campaign', '')
            writer.writerow([
                str(e.event_id), e.event_type, e.site_key,
                e.session.session_id if e.session else '',
                e.session.client_id if e.session else '',
                url, page_title, referrer, utm_source, utm_campaign, e.created_at.isoformat(),
            ])
        return response

    # --- KPIs ---
    total_events = events_qs.count()
    page_loads = events_qs.filter(event_type='page_load').count()
    form_submits = events_qs.filter(event_type='form_submission').count()

    unique_visitors = (
        sessions_qs.exclude(client_id__isnull=True).exclude(client_id='')
        .values('client_id').distinct().count()
    )

    # Use JSON field lookups directly for event_name (no annotation needed)
    new_registrations = events_qs.filter(
        event_type='custom_event',
        event_data__event_name__in=['user_registered', 'user_register']
    ).count()

    logins = events_qs.filter(
        event_type='custom_event',
        event_data__event_name__in=['user_logged_in', 'user_login']
    ).count()

    # --- Time series (daily) ---
    daily_counts = (
        events_qs
        .annotate(date=TruncDate('created_at'))
        .values('date')
        .annotate(count=Count('id'))
        .order_by('date')
    )
    by_day = {row['date']: row['count'] for row in daily_counts}
    days, counts = [], []
    cur = from_date
    while cur <= to_date:
        days.append(cur.isoformat())
        counts.append(by_day.get(cur, 0))
        cur += timedelta(days=1)

    # --- Top pages / marketing / referrers ---
    top_pages = (
        events_qs.annotate(
            page_url=Coalesce('url', jsonb_text(F('event_data'), 'meta_url'), output_field=TextField())
        )
        .exclude(page_url__isnull=True).exclude(page_url='')
        .values('page_url')
        .annotate(events=Count('id'))
        .order_by('-events')[:10]
    )

    top_campaigns = (
        events_qs
        .annotate(source=Coalesce('utm_source', jsonb_text(F('event_data'), 'utm_source'), output_field=TextField()))
        .annotate(campaign=Coalesce('utm_campaign', jsonb_text(F('event_data'), 'utm_campaign'), output_field=TextField()))
        .values('source', 'campaign')
        .exclude(source__isnull=True).exclude(source='')
        .annotate(events=Count('id'))
        .order_by('-events')[:10]
    )

    top_sources = (
        events_qs
        .annotate(source=Coalesce('utm_source', jsonb_text(F('event_data'), 'utm_source'), output_field=TextField()))
        .values('source')
        .exclude(source__isnull=True).exclude(source='')
        .annotate(events=Count('id'))
        .order_by('-events')[:10]
    )

    top_mediums = (
        events_qs
        .annotate(medium=Coalesce('utm_medium', jsonb_text(F('event_data'), 'utm_medium'), output_field=TextField()))
        .values('medium')
        .exclude(medium__isnull=True).exclude(medium='')
        .annotate(events=Count('id'))
        .order_by('-events')[:10]
    )

    top_referrers = (
        events_qs.annotate(
            ref=Coalesce('referrer', jsonb_text(F('event_data'), 'meta_referrer'), output_field=TextField())
        )
        .values('ref').exclude(ref__isnull=True).exclude(ref='')
        .annotate(events=Count('id')).order_by('-events')[:10]
    )

    # --- Input / Search: table of recent searches (non-masked rows included as-is) ---
    recent_searches = (
        events_qs.filter(event_type='custom_event', event_data__event_name='search')
        .filter(event_data__field__isnull=False)
        .values(
            'created_at',
            'event_data__field__label',
            'event_data__field__type',
            'event_data__field__value',
            'event_data__field__masked',
            'event_data__field__reason',
            'event_data__field__name',
            'event_data__field__form_name',
            'event_data__field__form_id',
            'event_data__field__selector',
            'event_data__change_reason',
            'event_data__meta_url',
        )
        .order_by('-created_at')[:20]
    )

    # --- Recent events (denormalized essentials) ---
    recent_events = (
        events_qs.select_related('session')
        .annotate(
            # Page & meta text
            page_url=Coalesce('url', jsonb_text(F('event_data'), 'meta_url'), output_field=TextField()),
            page_title_an=Coalesce('page_title', jsonb_text(F('event_data'), 'meta_page_title'), output_field=TextField()),
            ref=Coalesce('referrer', jsonb_text(F('event_data'), 'meta_referrer'), output_field=TextField()),
            lang=Coalesce('language', jsonb_text(F('event_data'), 'meta_language'), output_field=TextField()),

            # Numeric values: ALWAYS convert JSON to TEXT via jsonb_text, then cast.
            # Prefer the column JSON (viewport/screen) if present, else fallback to event_data meta_*.
            tz=as_int(jsonb_text(F('event_data'), 'meta_tz_offset_min')),

            vw=Coalesce(
                as_int(jsonb_text(F('viewport'), 'w')),
                as_int(jsonb_text(F('event_data'), 'meta_vw')),
                output_field=IntegerField(),
            ),
            vh=Coalesce(
                as_int(jsonb_text(F('viewport'), 'h')),
                as_int(jsonb_text(F('event_data'), 'meta_vh')),
                output_field=IntegerField(),
            ),
            sw=Coalesce(
                as_int(jsonb_text(F('screen'), 'w')),
                as_int(jsonb_text(F('event_data'), 'meta_sw')),
                output_field=IntegerField(),
            ),
            sh=Coalesce(
                as_int(jsonb_text(F('screen'), 'h')),
                as_int(jsonb_text(F('event_data'), 'meta_sh')),
                output_field=IntegerField(),
            ),
            dpr=Coalesce(
                as_float(jsonb_text(F('screen'), 'dpr')),
                as_float(jsonb_text(F('event_data'), 'meta_dpr')),
                output_field=FloatField(),
            ),

            # UTM
            utm_source_an=Coalesce('utm_source', jsonb_text(F('event_data'), 'utm_source'), output_field=TextField()),
            utm_medium_an=Coalesce('utm_medium', jsonb_text(F('event_data'), 'utm_medium'), output_field=TextField()),
            utm_campaign_an=Coalesce('utm_campaign', jsonb_text(F('event_data'), 'utm_campaign'), output_field=TextField()),
            utm_term_an=Coalesce('utm_term', jsonb_text(F('event_data'), 'utm_term'), output_field=TextField()),
            utm_content_an=Coalesce('utm_content', jsonb_text(F('event_data'), 'utm_content'), output_field=TextField()),

            # Event name as text
            evname=Coalesce(jsonb_text(F('event_data'), 'event_name'), Value(''), output_field=CharField()),

            # Field details as text
            fld_label=jsonb_text(F('event_data'), 'field', 'label'),
            fld_type=jsonb_text(F('event_data'), 'field', 'type'),
            fld_value=jsonb_text(F('event_data'), 'field', 'value'),
            fld_masked=jsonb_text(F('event_data'), 'field', 'masked'),

            # Identity fallbacks
            id_user_id=Coalesce(jsonb_text(F('event_data'), 'identity_user_id'), F('session__user_external_id'), output_field=TextField()),
            id_user_email=Coalesce(jsonb_text(F('event_data'), 'identity_user_email'), F('session__user_email'), output_field=TextField()),
            id_user_name=Coalesce(jsonb_text(F('event_data'), 'identity_user_name'), F('session__user_name'), output_field=TextField()),
        )
        .order_by('-created_at')[:10]
        .values(
            'created_at','event_type','evname','page_url','page_title_an','ref',
            'utm_source_an','utm_medium_an','utm_campaign_an','utm_term_an','utm_content_an',
            'lang','tz','vw','vh','sw','sh','dpr',
            'fld_label','fld_type','fld_value','fld_masked',
            'id_user_id','id_user_email','id_user_name'
        )
    )

    # --- Leads & users ---
    recent_leads = leads_qs.order_by('-created_at')[:10]
    recent_users = sessions_qs.filter(
        Q(user_external_id__isnull=False) | Q(user_email__isnull=False)
    ).order_by('-last_seen')[:10]

    context = {
        # filters
        'site_keys': site_keys,
        'active_site_key': active_site_key,
        'from': from_date_str,
        'to': to_date_str,

        # KPIs
        'total_events': total_events,
        'page_loads': page_loads,
        'form_submits': form_submits,
        'unique_visitors': unique_visitors,
        'new_registrations': new_registrations,
        'logins': logins,

        # Main time series
        'days_json': json.dumps(days),
        'counts_json': json.dumps(counts),

        # Tables
        'top_pages': top_pages,
        'top_referrers': top_referrers,
        'top_sources': top_sources,
        'top_mediums': top_mediums,
        'top_campaigns': top_campaigns,

        'recent_searches': list(recent_searches),
        'recent_events': list(recent_events),

        'new_leads': leads_qs.count(),
        'recent_leads': recent_leads,
        'recent_users': recent_users,
    }
    return render(request, 'dashboard.html', context)
