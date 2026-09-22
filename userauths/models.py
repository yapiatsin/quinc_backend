from django.db import models
# Create your models here.
from datetime import datetime, timedelta
import secrets
from django.contrib.auth.models import AbstractUser, Permission
from shortuuid.django_fields import ShortUUIDField
from django.utils import timezone
from simple_history.models import HistoricalRecords
import uuid
# Create your models here.
#---------------------------------------------------------#
#---------------- UTILISATEUR PERSONNALISÉ ---------------#
#---------------------------------------------------------#
class LocalEntrepot(models.Model):
    code = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    nom = models.CharField(max_length=100)
    contact = models.CharField(
        max_length=30,
        null=True,
        blank=True,
        verbose_name='Contact',
        help_text='Contact de l’agence (optionnel)',
    )
    latitude = models.DecimalField(
        max_digits=9, decimal_places=7, null=True, blank=True,
        help_text="Latitude GPS (ex: 5.345317 pour Abidjan-Plateau)",
    )
    longitude = models.DecimalField(
        max_digits=9, decimal_places=7, null=True, blank=True,
        help_text="Longitude GPS (ex: -4.024429 pour Abidjan-Plateau)",
    )
    statut = models.BooleanField(
        default=True,
        verbose_name='Statut',
        help_text='Coché = Ouvert, décoché = Fermé',
    )
    def __str__(self):
        return self.nom
    @property
    def a_coordonnees(self):
        return self.latitude is not None and self.longitude is not None
    def creneau_du_jour(self, jour=None):
        """Retourne le créneau du jour donné (0=lundi … 6=dimanche), ou None."""
        if jour is None:
            jour = timezone.localtime().weekday()
        return self.creneaux.filter(jour=jour).order_by('heure_ouverture').first()
    def est_ouvert_maintenant(self):
        """
        True si le statut est Ouvert et qu'un créneau du jour couvre l'heure actuelle.
        Sans créneau configuré → suit uniquement le booléen statut.
        """
        if not self.statut:
            return False
        now = timezone.localtime()
        creneau = self.creneau_du_jour(now.weekday())
        if creneau is None:
            return True
        return creneau.est_ouvert_a(now.time())
    @property
    def statut_ouverture(self):
        return 'Ouvert' if self.statut else 'Fermé'

JOURS_SEMAINE = (
    (0, 'Lundi'),
    (1, 'Mardi'),
    (2, 'Mercredi'),
    (3, 'Jeudi'),
    (4, 'Vendredi'),
    (5, 'Samedi'),
    (6, 'Dimanche'),
)

class CreneauDisponibilite(models.Model):
    """Horaires d'ouverture optionnels d'un LocalEntrepot (un créneau par jour)."""
    local_entrepot = models.ForeignKey(
        LocalEntrepot,
        on_delete=models.CASCADE,
        related_name='creneaux',
    )
    jour = models.PositiveSmallIntegerField(choices=JOURS_SEMAINE)
    heure_ouverture = models.TimeField()
    heure_fermeture = models.TimeField()
    class Meta:
        verbose_name = 'Créneau de disponibilité'
        verbose_name_plural = 'Créneaux de disponibilité'
        ordering = ['jour', 'heure_ouverture']
        constraints = [
            models.UniqueConstraint(
                fields=['local_entrepot', 'jour'],
                name='uniq_creneau_local_jour',
            ),
        ]
    def __str__(self):
        return (
            f'{self.local_entrepot.nom} — {self.get_jour_display()} '
            f'{self.heure_ouverture.strftime("%H:%M")}–{self.heure_fermeture.strftime("%H:%M")}'
        )

    def clean(self):
        from django.core.exceptions import ValidationError
        super().clean()
        if (
            self.heure_ouverture
            and self.heure_fermeture
            and self.heure_fermeture <= self.heure_ouverture
        ):
            raise ValidationError(
                {'heure_fermeture': "L'heure de fermeture doit être après l'heure d'ouverture."}
            )

    def est_ouvert_a(self, heure):
        """True si `heure` est dans l'intervalle [ouverture, fermeture]."""
        if heure is None:
            return False
        return self.heure_ouverture <= heure <= self.heure_fermeture

    @property
    def statut(self):
        """Statut Ouvert/Fermé pour ce créneau aujourd'hui à l'heure courante."""
        now = timezone.localtime()
        if now.weekday() != self.jour:
            return 'Fermé'
        return 'Ouvert' if self.est_ouvert_a(now.time()) else 'Fermé'

GENRE_CHOICES = (
        ('Homme', 'Homme'),
        ('Femme', 'Femme'), 
)
ROLE_CHOICES = (
        ('accueil',      'Service Accueil'),
        ('caissier',     'Caissier(ère)'),
        ('livreur',      'Livreur(euse)'),
        ('chefagence',   'Chef agence'),
        ('gestionnaire', 'Gestionnaire'),
        ('admin',        'Administrateur'),
        ('client',  'Client'),
    )
# Rôles pour lesquels l'affectation à un LocalEntrepot est obligatoire
ROLES_AVEC_LOCAL = {'accueil', 'caissier', 'livreur', 'chefagence'}

class CustomUser(AbstractUser):
    email = models.EmailField(unique=True, null=False)
    username = models.CharField(unique=True, max_length=100)
    contact = models.CharField(max_length=15, null=False, blank=True)
    role = models.CharField(default="caissier",max_length=20, choices=ROLE_CHOICES)
    genre = models.CharField(default="Homme",max_length=20, choices=GENRE_CHOICES)
    local_entrepot = models.ForeignKey(LocalEntrepot, on_delete=models.SET_NULL, related_name='local_entrepot_users', null=True, blank=True)
    is_active = models.BooleanField(default=True)
    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = ["email"]
    user_permissions = models.ManyToManyField(Permission,related_name='customuser_permissions', blank=True)
    history = HistoricalRecords()

    def clean(self):
        from django.core.exceptions import ValidationError
        super().clean()
        if self.role in ROLES_AVEC_LOCAL and not self.local_entrepot_id:
            raise ValidationError(
                {'local_entrepot': f"Un entrepôt est obligatoire pour le rôle « {self.get_role_display()} »."}
            )
    def __str__(self):
        return '%s - %s ' %(self.username, self.email,)

def profil_image_upload_path(instance, filename):
    return f'profils/{instance.user.username}/{filename}'

STATUT_CHOICES = (
    ('actif', 'Actif'),
    ('inactif', 'Inactif'),
    ('suspendu', 'Suspendu'),
)
class ProfilUser(models.Model):
    pid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='profil'
    )
    # --- Informations personnelles ---
    photo = models.ImageField(
        upload_to=profil_image_upload_path,
        null=True,
        blank=True,
        default='profils/default/avatar.png'
    )
    date_naissance = models.DateField(null=True, blank=True)
    adresse = models.CharField(max_length=255, null=True, blank=True)
    ville = models.CharField(max_length=100, null=True, blank=True)
    pays = models.CharField(max_length=100, default='Côte d\'Ivoire')
    code_postal = models.CharField(max_length=20, null=True, blank=True)

    # --- Informations professionnelles ---
    poste = models.CharField(max_length=100, null=True, blank=True)
    numero_employe = ShortUUIDField(
        length=8,
        max_length=20,
        unique=True,
        null=True,
        blank=True,
        prefix='EMP&B-'
    )
    date_embauche = models.DateField(null=True, blank=True)
    statut = models.CharField(
        max_length=20,
        choices=STATUT_CHOICES,
        default='actif'
    )
    # --- Préférences & activité ---
    bio = models.TextField(max_length=500, null=True, blank=True)
    derniere_connexion = models.DateTimeField(null=True, blank=True)
    est_verifie = models.BooleanField(default=False)
    # --- Métadonnées ---
    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)
    history = HistoricalRecords()
    class Meta:
        verbose_name = "Profil Utilisateur"
        verbose_name_plural = "Profils Utilisateurs"
        ordering = ['-date_creation']
    def __str__(self):
        return f'Profil de {self.user.username} ({self.user.get_full_name()})'
    @property
    def nom_complet(self):
        return self.user.get_full_name() or self.user.username
    @property
    def est_actif(self):
        return self.statut == 'actif' and self.user.is_active
    def save(self, *args, **kwargs):
        # Supprimer l'ancienne photo si elle est remplacée
        try:
            ancien_profil = ProfilUser.objects.get(pk=self.pk)
            if ancien_profil.photo and ancien_profil.photo != self.photo:
                if 'default' not in ancien_profil.photo.name:
                    ancien_profil.photo.delete(save=False)
        except ProfilUser.DoesNotExist:
            pass
        super().save(*args, **kwargs)

class TypeCustomPermission(models.Model):
    cid = ShortUUIDField(unique=True, length=6, alphabet="abcd1234", editable=False)
    categorie = models.CharField(max_length=100)
    def __str__(self):
        return self.categorie

class CustomPermission(models.Model):
    name = models.CharField(max_length=100)
    categorie = models.ForeignKey(TypeCustomPermission, on_delete=models.CASCADE,related_name='cat_permis')
    url = models.CharField(max_length=255)
    users = models.ManyToManyField(CustomUser, related_name='custom_permissions', blank=True)
    def __str__(self):
        return self.name
    
class PWD_FORGET(models.Model):
    PURPOSE_PASSWORD_RESET = 'password_reset'
    PURPOSE_ACTIVATION = 'activation'
    PURPOSE_GOOGLE_LINK = 'google_link'
    PURPOSE_CHOICES = (
        (PURPOSE_PASSWORD_RESET, 'Réinitialisation mot de passe'),
        (PURPOSE_ACTIVATION, 'Activation compte'),
        (PURPOSE_GOOGLE_LINK, 'Liaison compte Google'),
    )

    otp = models.IntegerField()
    status = models.CharField(max_length=1, default="0")
    purpose = models.CharField(
        max_length=32,
        choices=PURPOSE_CHOICES,
        default=PURPOSE_PASSWORD_RESET,
        db_index=True,
    )
    user_id = models.ForeignKey(CustomUser, on_delete=models.CASCADE)
    creat_at = models.DateTimeField(auto_now_add=True)

class EmailVerificationToken(models.Model):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='verification_tokens',)
    token = models.CharField(max_length=64, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    used = models.BooleanField(default=False)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Activation {self.user.email} ({'utilisé' if self.used else 'actif'})"

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)

    def is_valid(self):
        if self.used:
            return False
        expiry = self.created_at + timedelta(hours=24)
        return timezone.now() < expiry

class GoogleIdentity(models.Model):
    """Lien entre un compte Auto-Piece et un compte Google.

    La cle est `sub`, l'identifiant stable attribue par Google : contrairement a
    l'e-mail, il ne change jamais, meme si l'utilisateur renomme son adresse.
    L'e-mail n'est conserve qu'a titre indicatif, pour l'affichage.
    """

    user = models.OneToOneField(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='google_identity',
    )
    sub = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        verbose_name='Identifiant Google',
    )
    email = models.EmailField()
    picture = models.URLField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_login_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Identite Google'
        verbose_name_plural = 'Identites Google'
        ordering = ['-created_at']

    def __str__(self):
        return f"Google {self.email} → {self.user.username}"
