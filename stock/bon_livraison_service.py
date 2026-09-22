"""Génération et gestion des bons de livraison inter-localités."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction

from .distance_service import distance_entre_localites
from .models import BonLivraison, Notification, TarifLivraison

User = get_user_model()


def livreurs_disponibles(local_entrepot):
    """Livreurs actifs affectés à une localité."""
    return User.objects.filter(
        role='livreur',
        local_entrepot=local_entrepot,
        is_active=True,
    ).order_by('username')


def taux_km_pour_distance(tarif, distance_km):
    """Retourne le tarif F/km applicable (palier ou tarif de base)."""
    paliers = list(tarif.paliers.order_by('ordre', 'km_min'))
    if paliers:
        for palier in paliers:
            if distance_km >= palier.km_min:
                if palier.km_max is None or distance_km <= palier.km_max:
                    return palier.cout_par_km
    return tarif.cout_par_km


def calculer_cout_base(local_donneur, local_demandeur, tarif=None):
    """Calcule (distance, cout_base, tarif) pour un trajet donneur → demandeur."""
    tarif = tarif or TarifLivraison.get_actif()
    if tarif is None:
        raise ValidationError(
            "Aucun tarif de livraison actif. Définissez-en un dans l'administration."
        )
    distance = distance_entre_localites(local_donneur, local_demandeur)
    if distance is None:
        distance = Decimal('0.00')
    taux = taux_km_pour_distance(tarif, distance)
    cout_base = (distance * taux).quantize(Decimal('0.01'))
    return distance, cout_base, tarif


@transaction.atomic
def creer_bon_livraison(
    demande,
    livreur,
    validateur,
    cout_ajustement=None,
    motif_ajustement='',
    observations='',
):
    """Crée le bon de livraison associé à une demande validée."""
    if BonLivraison.objects.filter(demande_transfert=demande).exists():
        raise ValidationError('Un bon de livraison existe déjà pour cette demande.')
    if livreur.role != 'livreur':
        raise ValidationError("L'utilisateur choisi n'est pas un livreur.")
    if livreur.local_entrepot_id != demande.local_donneur_id:
        raise ValidationError(
            f"Le livreur doit appartenir à la localité « {demande.local_donneur.nom} »."
        )

    distance, cout_base, tarif = calculer_cout_base(
        demande.local_donneur, demande.local_demandeur,
    )
    cout_ajustement = cout_ajustement if cout_ajustement is not None else Decimal('0.00')
    if cout_ajustement != Decimal('0.00') and not (motif_ajustement or '').strip():
        raise ValidationError('Un motif est requis pour tout ajustement de coût.')

    numero = f'BL-{demande.numero_demande}'
    bon = BonLivraison(
        numero_bon_livraison=numero,
        demande_transfert=demande,
        livreur=livreur,
        distance_km=distance,
        tarif_applique=tarif,
        cout_base=cout_base,
        cout_ajustement=cout_ajustement,
        motif_ajustement=motif_ajustement,
        cree_par=validateur,
        observations=observations,
        statut='en_preparation',
    )
    bon.full_clean()
    bon.save()

    Notification.objects.create(
        type_notification='info',
        titre=f'Livraison assignée — {bon.numero_bon_livraison}',
        message=(
            f"Transfert {demande.numero_demande} : "
            f"{demande.local_donneur.nom} → {demande.local_demandeur.nom}. "
            f"Coût livraison : {bon.cout_total:,.0f} F."
        ),
        utilisateur=livreur,
    )
    return bon
