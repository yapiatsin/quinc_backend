from django.db import models

# Create your models here.
from decimal import Decimal

from django.conf import settings
from django.db import models


class FavoriPiece(models.Model):
    """Pièce mise en favori par un client e-commerce."""
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='favoris_pieces',
    )
    piece = models.ForeignKey(
        'stock.Piece',
        on_delete=models.CASCADE,
        related_name='favoris_utilisateurs',
    )
    date_ajout = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('utilisateur', 'piece')
        verbose_name = 'Favori client'
        verbose_name_plural = 'Favoris clients'
        ordering = ['-date_ajout']

    def __str__(self):
        return f'{self.utilisateur} — {self.piece}'


class VueRecentePiece(models.Model):
    """Historique des pièces consultées par un client e-commerce."""
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='vues_recentes_pieces',
    )
    piece = models.ForeignKey(
        'stock.Piece',
        on_delete=models.CASCADE,
        related_name='vues_recentes_utilisateurs',
    )
    date_vue = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('utilisateur', 'piece')
        verbose_name = 'Vue récente'
        verbose_name_plural = 'Vues récentes'
        ordering = ['-date_vue']

    def __str__(self):
        return f'{self.utilisateur} — {self.piece} ({self.date_vue})'


class NewsletterAbonne(models.Model):
    """Abonnement à la newsletter e-commerce."""
    email = models.EmailField(unique=True)
    accepte_offres = models.BooleanField(default=True)
    actif = models.BooleanField(default=True)
    date_inscription = models.DateTimeField(auto_now_add=True)
    date_mise_a_jour = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Abonné newsletter'
        verbose_name_plural = 'Abonnés newsletter'
        ordering = ['-date_inscription']

    def __str__(self):
        etat = 'actif' if self.actif else 'inactif'
        return f'{self.email} ({etat})'


class RecherchePopulaire(models.Model):
    """Termes de recherche agrégés (tous clients) pour les suggestions populaires."""
    terme = models.CharField(max_length=150, unique=True, db_index=True)
    terme_affiche = models.CharField(max_length=150)
    compteur = models.PositiveIntegerField(default=1)
    derniere_recherche = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Recherche populaire'
        verbose_name_plural = 'Recherches populaires'
        ordering = ['-compteur', '-derniere_recherche']

    def __str__(self):
        return f'{self.terme_affiche} ({self.compteur})'


class RechercheRecente(models.Model):
    """Historique de recherche par client connecté ou session anonyme."""
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='recherches_recentes',
        null=True,
        blank=True,
    )
    session_key = models.CharField(max_length=40, blank=True, default='', db_index=True)
    terme = models.CharField(max_length=150, db_index=True)
    terme_affiche = models.CharField(max_length=150)
    date_recherche = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Recherche récente'
        verbose_name_plural = 'Recherches récentes'
        ordering = ['-date_recherche']
        indexes = [
            models.Index(fields=['utilisateur', '-date_recherche']),
            models.Index(fields=['session_key', '-date_recherche']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['utilisateur', 'terme'],
                condition=models.Q(utilisateur__isnull=False),
                name='uniq_recherche_recente_user_terme',
            ),
            models.UniqueConstraint(
                fields=['session_key', 'terme'],
                condition=models.Q(utilisateur__isnull=True),
                name='uniq_recherche_recente_session_terme',
            ),
        ]

    def __str__(self):
        who = self.utilisateur or self.session_key or 'anonyme'
        return f'{who} — {self.terme_affiche}'


class PaysLivraison(models.Model):
    """Pays desservis pour la livraison e-commerce."""
    nom = models.CharField(max_length=100)
    code = models.CharField(max_length=10, unique=True, help_text='Ex: CI')
    actif = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Pays de livraison'
        verbose_name_plural = 'Pays de livraison'
        ordering = ['nom']

    def __str__(self):
        return self.nom


class VilleLivraison(models.Model):
    """Ville rattachée à un pays, avec coût de livraison."""
    pays = models.ForeignKey(
        PaysLivraison,
        on_delete=models.CASCADE,
        related_name='villes',
    )
    nom = models.CharField(max_length=100)
    frais_livraison = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text='Coût de livraison pour cette ville (FCFA).',
    )
    actif = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Ville de livraison'
        verbose_name_plural = 'Villes de livraison'
        ordering = ['nom']
        unique_together = ('pays', 'nom')

    def __str__(self):
        return f'{self.nom} ({self.pays.nom})'


class CommuneLivraison(models.Model):
    """Commune rattachée à une ville."""
    ville = models.ForeignKey(
        VilleLivraison,
        on_delete=models.CASCADE,
        related_name='communes',
    )
    nom = models.CharField(max_length=100)
    actif = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Commune de livraison'
        verbose_name_plural = 'Communes de livraison'
        ordering = ['nom']
        unique_together = ('ville', 'nom')

    def __str__(self):
        return f'{self.nom} ({self.ville.nom})'


class ContactMessage(models.Model):
    """Message envoyé depuis le formulaire de contact e-commerce."""

    SUJET_CHOICES = (
        ('info', 'Information'),
        ('commande', 'Commande'),
        ('livraison', 'Livraison'),
        ('retour', 'Retour / SAV'),
        ('autre', 'Autre'),
    )

    STATUS_NOUVEAU = 'nouveau'
    STATUS_LU = 'lu'
    STATUS_REPONDU = 'repondu'
    STATUS_ARCHIVE = 'archive'
    STATUS_CHOICES = (
        (STATUS_NOUVEAU, 'Nouveau'),
        (STATUS_LU, 'Lu'),
        (STATUS_REPONDU, 'Répondu'),
        (STATUS_ARCHIVE, 'Archivé'),
    )

    nom = models.CharField(max_length=200)
    email = models.EmailField()
    telephone = models.CharField(max_length=30, blank=True, default='')
    sujet = models.CharField(max_length=100, blank=True, default='')
    services = models.JSONField(default=list, blank=True)
    message = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_NOUVEAU,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Message de contact'
        verbose_name_plural = 'Messages de contact'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.nom} — {self.sujet or "Sans sujet"} ({self.created_at:%d/%m/%Y})'


class ChatConversation(models.Model):
    """Conversation chatbot e-commerce (client ↔ staff, IA plus tard)."""

    STATUS_PENDING = 'pending'
    STATUS_ACTIVE = 'active'
    STATUS_REFUSED = 'refused'
    STATUS_CLOSED = 'closed'
    STATUS_CHOICES = (
        (STATUS_PENDING, 'En attente'),
        (STATUS_ACTIVE, 'Active'),
        (STATUS_REFUSED, 'Refusée'),
        (STATUS_CLOSED, 'Fermée'),
    )

    HANDLER_STAFF = 'staff'
    HANDLER_AI = 'ai'
    HANDLER_CHOICES = (
        (HANDLER_STAFF, 'Conseiller'),
        (HANDLER_AI, 'IA'),
    )

    client = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='chat_conversations_client',
        null=True,
        blank=True,
    )
    session_key = models.CharField(max_length=40, blank=True, default='', db_index=True)
    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='chat_conversations_staff',
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )
    handler_mode = models.CharField(
        max_length=20,
        choices=HANDLER_CHOICES,
        default=HANDLER_STAFF,
    )
    subject = models.CharField(max_length=255, blank=True, default='')
    last_message_at = models.DateTimeField(null=True, blank=True, db_index=True)
    closed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    client_typing_at = models.DateTimeField(null=True, blank=True)
    staff_typing_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Conversation chat'
        verbose_name_plural = 'Conversations chat'
        ordering = ['-last_message_at', '-created_at']
        indexes = [
            models.Index(fields=['status', '-last_message_at']),
            models.Index(fields=['session_key', 'status']),
            models.Index(fields=['client', 'status']),
        ]

    def __str__(self):
        who = self.client or self.session_key or 'anonyme'
        return f'Chat {self.pk} — {who} ({self.status})'


class ChatMessage(models.Model):
    """Message d'une conversation chatbot."""

    SENDER_CLIENT = 'client'
    SENDER_STAFF = 'staff'
    SENDER_SYSTEM = 'system'
    SENDER_BOT = 'bot'
    SENDER_CHOICES = (
        (SENDER_CLIENT, 'Client'),
        (SENDER_STAFF, 'Staff'),
        (SENDER_SYSTEM, 'Système'),
        (SENDER_BOT, 'Bot'),
    )

    conversation = models.ForeignKey(
        ChatConversation,
        on_delete=models.CASCADE,
        related_name='messages',
    )
    sender_type = models.CharField(max_length=20, choices=SENDER_CHOICES)
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='chat_messages',
        null=True,
        blank=True,
    )
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Message chat'
        verbose_name_plural = 'Messages chat'
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['conversation', 'created_at']),
        ]

    def __str__(self):
        return f'{self.sender_type}: {self.body[:40]}'
