"""
Demandes de transfert inter-localités (workflow chef d'agence).
"""
from __future__ import annotations

from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from Userauths.models import LocalEntrepot
from .models import (
    DemandeTransfert,
    LigneDemandeTransfert,
    EntrePiece,
    Notification,
    Piece,
)
from .stock_local_service import decrementer_stock, incrementer_stock, get_quantite
from .mqtt_service import publish_transfert_demande


def _generer_numero_demande() -> str:
    year = timezone.now().year
    count = DemandeTransfert.objects.filter(
        numero_demande__startswith=f'DTR-{year}-'
    ).count() + 1
    return f'DTR-{year}-{count:04d}'


def _chefs_agence_pour_local(local: LocalEntrepot):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    return User.objects.filter(role='chefagence', local_entrepot=local, is_active=True)


def _iter_utilisateurs_uniques(users):
    """QuerySet ou liste d'utilisateurs → itération sans doublon."""
    if hasattr(users, 'distinct'):
        yield from users.distinct()
        return
    seen = set()
    for user in users:
        if user is None or user.pk in seen:
            continue
        seen.add(user.pk)
        yield user


def _notifier_utilisateurs(users, *, titre, message, type_notif='transfert_demande'):
    for user in _iter_utilisateurs_uniques(users):
        Notification.objects.create(
            type_notification=type_notif,
            titre=titre,
            message=message,
            utilisateur=user,
        )


def _notifier_chefs_local(local: LocalEntrepot, *, titre, message, type_notif='transfert_demande'):
    chefs = _chefs_agence_pour_local(local)
    _notifier_utilisateurs(chefs, titre=titre, message=message, type_notif=type_notif)


def _publish_mqtt_demande(demande: DemandeTransfert, event: str):
    publish_transfert_demande(
        demande_id=demande.pk,
        numero_demande=demande.numero_demande,
        event=event,
        local_donneur_id=demande.local_donneur_id,
        local_demandeur_id=demande.local_demandeur_id,
        statut=demande.statut,
    )


def _parse_lignes_post(post_data) -> list[tuple[Piece, int]]:
    numeros = post_data.getlist('numero_piece[]') or post_data.getlist('numero_piece')
    quantites = post_data.getlist('quantite[]') or post_data.getlist('quantite')
    if not numeros:
        numero = (post_data.get('numero_piece') or '').strip()
        qte = post_data.get('quantite')
        if numero:
            numeros = [numero]
            quantites = [qte]
    if not numeros:
        raise ValidationError('Ajoutez au moins une ligne de pièce.')
    lignes = []
    seen = set()
    for numero, qte_raw in zip(numeros, quantites):
        numero = (numero or '').strip()
        if not numero:
            continue
        try:
            qte = int(qte_raw)
        except (TypeError, ValueError):
            raise ValidationError(f'Quantité invalide pour {numero}.')
        if qte <= 0:
            raise ValidationError(f'La quantité doit être positive ({numero}).')
        if numero in seen:
            raise ValidationError(f'La pièce {numero} est en double.')
        seen.add(numero)
        try:
            piece = Piece.objects.get(numero_piece=numero)
        except Piece.DoesNotExist:
            raise ValidationError(f'Pièce introuvable : {numero}.')
        lignes.append((piece, qte))
    if not lignes:
        raise ValidationError('Ajoutez au moins une ligne de pièce valide.')
    return lignes


@transaction.atomic
def creer_demande_transfert(demandeur, local_demandeur, local_donneur, motif, lignes):
    if local_demandeur.pk == local_donneur.pk:
        raise ValidationError('La localité destination doit être différente de la vôtre.')
    demande = DemandeTransfert.objects.create(
        numero_demande=_generer_numero_demande(),
        local_demandeur=local_demandeur,
        local_donneur=local_donneur,
        motif=motif or '',
        demandeur=demandeur,
        statut='en_attente',
    )
    for piece, qte in lignes:
        LigneDemandeTransfert.objects.create(demande=demande, piece=piece, quantite=qte)

    titre = f'Nouvelle demande {demande.numero_demande}'
    msg = (
        f"{demande.local_demandeur.nom} demande un transfert vers votre agence "
        f"({demande.local_donneur.nom}). {demande.nb_lignes} ligne(s)."
    )
    _notifier_chefs_local(local_donneur, titre=titre, message=msg)
    _publish_mqtt_demande(demande, 'nouvelle')
    return demande


@transaction.atomic
def annuler_demande_par_demandeur(demande: DemandeTransfert, user):
    if demande.statut != 'en_attente':
        raise ValidationError('Seules les demandes en attente peuvent être annulées.')
    if demande.demandeur_id != user.pk and getattr(user, 'local_entrepot_id', None) != demande.local_demandeur_id:
        raise ValidationError('Action non autorisée.')
    demande.statut = 'annulee_demandeur'
    demande.save(update_fields=['statut'])
    _notifier_chefs_local(
        demande.local_donneur,
        titre=f'Demande {demande.numero_demande} annulée',
        message=f"Le demandeur a annulé la demande.",
        type_notif='warning',
    )
    _publish_mqtt_demande(demande, 'annulee')
    return demande


@transaction.atomic
def annuler_demande_par_donneur(demande: DemandeTransfert, user):
    if demande.statut != 'en_attente':
        raise ValidationError('Seules les demandes en attente peuvent être annulées.')
    if getattr(user, 'local_entrepot_id', None) != demande.local_donneur_id:
        raise ValidationError('Action réservée au chef d\'agence donneur.')
    demande.statut = 'annulee_donneur'
    demande.save(update_fields=['statut'])
    if demande.demandeur_id:
        _notifier_utilisateurs(
            [demande.demandeur],
            titre=f'Demande {demande.numero_demande} refusée',
            message=f"La demande a été annulée par {demande.local_donneur.nom}.",
            type_notif='warning',
        )
    _publish_mqtt_demande(demande, 'annulee')
    return demande


@transaction.atomic
def valider_demande_par_donneur(
    demande: DemandeTransfert,
    user,
    numero_bon_donneur: str,
    livreur_id=None,
    cout_ajustement=None,
    motif_ajustement='',
    observations_livraison='',
):
    from django.contrib.auth import get_user_model
    from .bon_livraison_service import creer_bon_livraison

    User = get_user_model()

    if demande.statut != 'en_attente':
        raise ValidationError('Cette demande n\'est plus en attente.')
    if getattr(user, 'local_entrepot_id', None) != demande.local_donneur_id:
        raise ValidationError('Action réservée au chef d\'agence donneur.')
    numero_bon = (numero_bon_donneur or '').strip()
    if not numero_bon:
        raise ValidationError('Le numéro de bon de commande est obligatoire.')
    if not livreur_id:
        raise ValidationError('Vous devez sélectionner un livreur.')

    try:
        livreur = User.objects.get(pk=livreur_id)
    except User.DoesNotExist:
        raise ValidationError('Livreur introuvable.')

    for ligne in demande.lignes.select_related('piece'):
        dispo = get_quantite(ligne.piece, demande.local_donneur)
        if dispo < ligne.quantite:
            raise ValidationError(
                f"Stock insuffisant chez {demande.local_donneur.nom} pour "
                f"{ligne.piece.numero_piece} (dispo: {dispo}, demandé: {ligne.quantite})."
            )

    demande.numero_bon_donneur = numero_bon
    demande.validateur_donneur = user
    demande.statut = 'validee'
    demande.date_validation = timezone.now()
    demande.save(update_fields=['numero_bon_donneur', 'validateur_donneur', 'statut', 'date_validation'])

    bon = creer_bon_livraison(
        demande=demande,
        livreur=livreur,
        validateur=user,
        cout_ajustement=cout_ajustement,
        motif_ajustement=motif_ajustement,
        observations=observations_livraison,
    )

    if demande.demandeur_id:
        _notifier_utilisateurs(
            [demande.demandeur],
            titre=f'Demande {demande.numero_demande} validée',
            message=(
                f"Votre demande a été validée. Bon n° {numero_bon}. "
                f"Bon de livraison {bon.numero_bon_livraison} "
                f"(coût livraison : {bon.cout_total:,.0f} F). "
                f"Statut : En attente d'expédition."
            ),
            type_notif='success',
        )
    _publish_mqtt_demande(demande, 'validee')
    return demande, bon


@transaction.atomic
def confirmer_reception_demande(demande: DemandeTransfert, user, numero_bon_saisi: str):
    if demande.statut != 'validee':
        raise ValidationError('La demande doit être validée avant réception.')
    if not utilisateur_est_chef_demandeur(user, demande):
        raise ValidationError('Action réservée au chef d\'agence demandeur.')
    bon = getattr(demande, 'bon_livraison', None)
    if bon is None:
        try:
            bon = demande.bon_livraison
        except Exception:
            bon = None
    if not bon or bon.statut != 'livre':
        raise ValidationError(
            'La réception n\'est possible qu\'après que l\'agence donneuse a marqué la livraison comme « Livrée ».'
        )
    saisi = (numero_bon_saisi or '').strip()
    if not saisi:
        raise ValidationError('Saisissez le numéro de bon de commande.')
    if saisi != (demande.numero_bon_donneur or '').strip():
        raise ValidationError(
            'Le numéro saisi ne correspond pas au bon émis par l\'agence donneuse.'
        )

    for ligne in demande.lignes.select_related('piece'):
        decrementer_stock(ligne.piece, demande.local_donneur, ligne.quantite)
        incrementer_stock(ligne.piece, demande.local_demandeur, ligne.quantite)
        EntrePiece.objects.create(
            piece=ligne.piece,
            local_entrepot=demande.local_demandeur,
            quantitajout=ligne.quantite,
            prix_achat=ligne.piece.prix_achat,
            utilisateur=user,
            origine_type='transfert_local',
            origine_local=demande.local_donneur,
            demande_transfert=demande,
        )

    demande.numero_bon_commande = saisi
    demande.receveur = user
    demande.statut = 'recue'
    demande.date_reception = timezone.now()
    demande.save(update_fields=['numero_bon_commande', 'receveur', 'statut', 'date_reception'])

    _notifier_chefs_local(
        demande.local_donneur,
        titre=f'Réception confirmée — {demande.numero_demande}',
        message=f"Stock déduit pour la demande {demande.numero_demande}.",
        type_notif='info',
    )
    _publish_mqtt_demande(demande, 'recue')
    return demande


def utilisateur_est_chef_donneur(user, demande: DemandeTransfert) -> bool:
    role = getattr(user, 'role', None)
    if not (user.is_superuser or role in ('chefagence', 'admin')):
        return False
    return getattr(user, 'local_entrepot_id', None) == demande.local_donneur_id


def utilisateur_est_chef_demandeur(user, demande: DemandeTransfert) -> bool:
    role = getattr(user, 'role', None)
    if not (user.is_superuser or role in ('chefagence', 'admin')):
        return False
    return getattr(user, 'local_entrepot_id', None) == demande.local_demandeur_id


def utilisateur_peut_gerer_livraison_donneur(user, demande: DemandeTransfert) -> bool:
    """Marquer en route / livré : réservé au chef d'agence donneur."""
    return utilisateur_est_chef_donneur(user, demande)


def utilisateur_peut_voir_demande(user, demande: DemandeTransfert) -> bool:
    if user.is_superuser or getattr(user, 'role', None) == 'admin':
        return True
    loc_id = getattr(user, 'local_entrepot_id', None)
    if not loc_id:
        return False
    return loc_id in (demande.local_demandeur_id, demande.local_donneur_id)
