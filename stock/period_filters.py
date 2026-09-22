"""Filtres de période et localité partagés (tableau de bord, stock, ventes, etc.)."""
from calendar import monthrange
from datetime import date, timedelta
import calendar

from Userauths.models import LocalEntrepot

from .forms import DateForm

PERIODE_LABELS = {
    'aujourdhui': "Aujourd'hui",
    'jour': "Aujourd'hui",
    'semaine': 'Cette semaine',
    'mois': 'Ce mois',
    'annee': 'Cette année',
    'personnalise': 'Personnalisé',
}

MOIS_FR = (
    '',
    'Janvier', 'Février', 'Mars', 'Avril', 'Mai', 'Juin',
    'Juillet', 'Août', 'Septembre', 'Octobre', 'Novembre', 'Décembre',
)


def dashboard_can_choose_localite(user):
    """Admin et gestionnaire peuvent choisir la localité dans le filtre."""
    if user.is_superuser:
        return True
    return getattr(user, 'role', None) in ('gestionnaire', 'admin')


def days_in_range(date_debut, date_fin):
    return (date_fin - date_debut).days + 1


def chart_granularity(date_debut, date_fin):
    """Plus d'un mois → agrégation mensuelle pour les graphiques."""
    return 'monthly' if days_in_range(date_debut, date_fin) > 31 else 'daily'


def get_periode_date_range(request, form=None, default='mois'):
    """Retourne (date_debut, date_fin, periode_active)."""
    today = date.today()
    periode = request.GET.get('periode', default)
    if form is None:
        form = DateForm(request.GET)

    if periode in ('aujourdhui', 'jour'):
        return today, today, 'aujourdhui'
    if periode == 'semaine':
        debut = today - timedelta(days=today.weekday())
        return debut, today, 'semaine'
    if periode == 'mois':
        debut = date(today.year, today.month, 1)
        fin = date(today.year, today.month, monthrange(today.year, today.month)[1])
        return debut, fin, 'mois'
    if periode == 'annee':
        return date(today.year, 1, 1), date(today.year, 12, 31), 'annee'
    if periode == 'personnalise' and form.is_valid():
        debut = form.cleaned_data.get('date_debut')
        fin = form.cleaned_data.get('date_fin')
        if debut and fin:
            if debut > fin:
                debut, fin = fin, debut
            return debut, fin, 'personnalise'
    if form.is_valid():
        debut = form.cleaned_data.get('date_debut')
        fin = form.cleaned_data.get('date_fin')
        if debut and fin:
            if debut > fin:
                debut, fin = fin, debut
            return debut, fin, 'personnalise'

    debut = date(today.year, today.month, 1)
    fin = date(today.year, today.month, monthrange(today.year, today.month)[1])
    return debut, fin, default if default in PERIODE_LABELS else 'mois'


def resolve_filter_localite(request, user, form, can_choose_localite):
    if can_choose_localite and form.is_valid():
        return form.cleaned_data.get('localite')
    if user.is_superuser or getattr(user, 'role', None) == 'admin':
        return None
    return getattr(user, 'local_entrepot', None)


def build_filtre_resume(date_debut, date_fin, periode_active, localite=None):
    """Libellé court de la période active (sans dates ni localité)."""
    if periode_active in ('aujourdhui', 'jour'):
        return PERIODE_LABELS['aujourdhui']
    if periode_active == 'semaine':
        return PERIODE_LABELS['semaine']
    if periode_active == 'mois':
        return MOIS_FR[date_debut.month]
    if periode_active == 'annee':
        return str(date_debut.year)
    if periode_active == 'personnalise':
        if date_debut.month == date_fin.month and date_debut.year == date_fin.year:
            return MOIS_FR[date_debut.month]
        return PERIODE_LABELS['personnalise']
    return PERIODE_LABELS.get(periode_active, 'Période')


def resolve_nav_localite_label(request, user):
    """Localité affichée dans la navbar (filtre actif ou entrepôt utilisateur)."""
    if not user.is_authenticated:
        return None
    form = DateForm(request.GET)
    can_choose = dashboard_can_choose_localite(user)
    localite = resolve_filter_localite(request, user, form, can_choose)
    if localite:
        return localite.nom
    entrepot = getattr(user, 'local_entrepot', None)
    return entrepot.nom if entrepot else None


def build_table_period_columns(date_debut, date_fin, periode_active=None):
    """
    Colonnes pour table_vente : jours si ≤ 31 jours, sinon mois.
    Retourne (columns, mode) avec columns = [{'label', 'start', 'end'}, ...]
    """
    if periode_active == 'annee' or days_in_range(date_debut, date_fin) > 31:
        columns = []
        month_cursor = date(date_debut.year, date_debut.month, 1)
        while month_cursor <= date_fin:
            last_day = monthrange(month_cursor.year, month_cursor.month)[1]
            m_start = month_cursor
            m_end = date(month_cursor.year, month_cursor.month, last_day)
            if m_end > date_fin:
                m_end = date_fin
            if m_start < date_debut:
                m_start = date_debut
            columns.append({
                'label': calendar.month_abbr[month_cursor.month],
                'start': m_start,
                'end': m_end,
            })
            if month_cursor.month == 12:
                month_cursor = date(month_cursor.year + 1, 1, 1)
            else:
                month_cursor = date(month_cursor.year, month_cursor.month + 1, 1)
        return columns, 'monthly'

    columns = []
    d = date_debut
    while d <= date_fin:
        columns.append({'label': str(d.day), 'start': d, 'end': d})
        d += timedelta(days=1)
    return columns, 'daily'


def periode_filter_context(
    request, user, *, reset_url_name, reset_url_kwargs=None, default='mois', include_localites=True,
):
    """Contexte commun pour le modal de filtre dans les templates."""
    form = DateForm(request.GET)
    date_debut, date_fin, periode_active = get_periode_date_range(request, form, default=default)
    can_choose = dashboard_can_choose_localite(user)
    localite = resolve_filter_localite(request, user, form, can_choose)
    from django.urls import reverse
    ctx = {
        'form': form,
        'date_debut': date_debut,
        'date_fin': date_fin,
        'periode_active': periode_active,
        'can_choose_localite': can_choose,
        'localite_active': localite,
        'filtre_resume': build_filtre_resume(date_debut, date_fin, periode_active, localite),
        'chart_mode': chart_granularity(date_debut, date_fin),
        'filter_reset_url': reverse(reset_url_name, kwargs=reset_url_kwargs or {}),
    }
    if include_localites and can_choose:
        ctx['localites'] = LocalEntrepot.objects.all()
    else:
        ctx['localites'] = None
    return ctx


def append_query_string(url, request):
    q = request.GET.urlencode()
    if q:
        sep = '&' if '?' in url else '?'
        return f"{url}{sep}{q}"
    return url
