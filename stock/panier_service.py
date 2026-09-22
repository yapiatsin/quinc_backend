"""
Panier accueil (vente) : un panier actif par utilisateur et par localité.
"""
from __future__ import annotations
from django.db import transaction
from .models import Panier
def get_user_localite_panier(user):
    """
    Localité pour le panier accueil / proforma en cours.
    Utilise toujours l'affectation sur le profil (pas le mode « voir tout » admin).
    """
    return getattr(user, 'local_entrepot', None)

def queryset_panier_accueil_actif(user, *, for_update=False):
    """Paniers accueil non validés, isolés par utilisateur + localité."""
    localite = get_user_localite_panier(user)
    if not localite:
        return Panier.objects.none()
    qs = Panier.objects.filter(
        utilisateur=user,
        local_entrepot=localite,
        valide=False,
        proforma=False,
        panier_paye=False,
    )
    if for_update:
        qs = qs.select_for_update()
    return qs.order_by('-date_save')


def get_panier_accueil_actif(user, *, for_update=False):
    return queryset_panier_accueil_actif(user, for_update=for_update).first()


def utilisateur_peut_modifier_panier_accueil(user, panier) -> bool:
    if panier is None:
        return False
    if panier.valide or panier.proforma or panier.panier_paye:
        return False
    localite = get_user_localite_panier(user)
    if not localite:
        return False
    return (
        panier.utilisateur_id == user.pk
        and panier.local_entrepot_id == localite.pk
    )


@transaction.atomic
def get_or_create_panier_accueil(user):
    """Retourne le panier accueil actif ou le crée pour (user, localité)."""
    localite = get_user_localite_panier(user)
    if not localite:
        return None, False
    panier = get_panier_accueil_actif(user, for_update=True)
    if panier:
        return panier, False
    panier = Panier.objects.create(
        utilisateur=user,
        local_entrepot=localite,
        valide=False,
        proforma=False,
    )
    return panier, True
