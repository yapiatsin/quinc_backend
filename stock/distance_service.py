"""Calcul de distance géographique entre localités (Haversine, hors-ligne)."""
from decimal import Decimal
from math import radians, sin, cos, sqrt, atan2

FACTEUR_ROUTE_DEFAUT = Decimal('1.3')


def haversine_km(lat1, lon1, lat2, lon2):
    """Distance vol d'oiseau en km entre 2 points GPS."""
    if None in (lat1, lon1, lat2, lon2):
        return None
    r_earth = 6371.0
    lat1, lon1, lat2, lon2 = map(
        lambda x: radians(float(x)), [lat1, lon1, lat2, lon2],
    )
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return Decimal(str(round(r_earth * c, 2)))


def distance_km(lat1, lon1, lat2, lon2, facteur_route=FACTEUR_ROUTE_DEFAUT):
    """Alias compatible avec la formule Haversine demandée (+ facteur route)."""
    d = haversine_km(lat1, lon1, lat2, lon2)
    if d is None:
        return None
    return (d * facteur_route).quantize(Decimal('0.01'))


def distance_entre_localites(local_source, local_destination, facteur_route=FACTEUR_ROUTE_DEFAUT):
    """Distance estimée (km) entre deux LocalEntrepot."""
    if not (local_source.a_coordonnees and local_destination.a_coordonnees):
        return None
    return distance_km(
        local_source.latitude, local_source.longitude,
        local_destination.latitude, local_destination.longitude,
        facteur_route=facteur_route,
    )
