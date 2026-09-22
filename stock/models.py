import random
import uuid
from django.core.exceptions import ValidationError
from django.db import models
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils.text import slugify
from shortuuid.django_fields import ShortUUIDField
from simple_history.models import HistoricalRecords
from decimal import Decimal
from django.utils import timezone
from userauths.models import LocalEntrepot
#---------------------------------------------------------#
#---------------- MODEL DE LA BASE DE DONNÉES ---------------#
#---------------------------------------------------------#

User = get_user_model()
def generate_numeric_id():
    return ''.join(random.choices('1234567890', k=5))

class Categorie(models.Model):
    cid = ShortUUIDField(unique=True, length=6, alphabet="abcd1234", editable=False)
    categorie = models.CharField(unique=True, max_length=50)
    image = models.ImageField(upload_to="vehicules", blank=True, null=True, default='favicon.ico')
    description = models.TextField(max_length=2000, null=True, blank=True)
    actif = models.BooleanField(default=True)
    history = HistoricalRecords()
    def __str__(self):
        return f"{self.categorie}"

    def as_dict(self, *, with_sous=True):
        data = {
            'id': self.pk,
            'cid': self.cid,
            'categorie': self.categorie,
            'actif': self.actif,
        }
        if with_sous:
            data['sous_categories'] = [
                sc.as_dict()
                for sc in self.sous_categories.all()
                if getattr(sc, 'actif', True)
            ]
        return data


class SousCategorie(models.Model):
    sid = ShortUUIDField(unique=True, length=6, alphabet="abcd1234", editable=False)
    categorie = models.ForeignKey(
        Categorie,
        on_delete=models.CASCADE,
        related_name='sous_categories',
    )
    nom = models.CharField(max_length=80)
    slug = models.SlugField(max_length=100, blank=True)
    image = models.ImageField(upload_to='sous_categories', blank=True, null=True)
    description = models.TextField(max_length=2000, null=True, blank=True)
    ordre = models.PositiveIntegerField(default=0)
    actif = models.BooleanField(default=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ['ordre', 'nom']
        verbose_name = 'Sous-catégorie'
        verbose_name_plural = 'Sous-catégories'
        constraints = [
            models.UniqueConstraint(
                fields=['categorie', 'nom'],
                name='souscategorie_categorie_nom_unique',
            ),
        ]

    def __str__(self):
        parent = self.categorie.categorie if self.categorie_id else ''
        return f"{parent} / {self.nom}" if parent else self.nom

    def as_dict(self):
        return {
            'id': self.pk,
            'sid': self.sid,
            'nom': self.nom,
            'slug': self.slug,
        }

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.nom) or 'sous-categorie'
        super().save(*args, **kwargs)


class Fournisseur(models.Model):
    nom = models.CharField(max_length=255)
    contact = models.CharField(max_length=255, blank=True)
    def __str__(self):
        return self.nom

class Piece(models.Model):
    """Catalogue produit — une référence unique (numero_piece)."""
    categorie = models.ForeignKey(Categorie, on_delete=models.CASCADE)
    sous_categorie = models.ForeignKey(
        SousCategorie,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pieces',
        help_text="Sous-catégorie optionnelle, rattachée à la catégorie.",
    )
    numero_piece = models.CharField(unique=True, max_length=100)
    designation = models.CharField(max_length=255)
    image = models.ImageField(upload_to="pieces", blank=True, null=True)
    prix_achat = models.DecimalField(max_digits=10, decimal_places=2)
    prix_unitaire = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Prix catalogue / référence (gestionnaire). Les localités peuvent définir un prix sur StockLocal.",
    )
    seuil = models.PositiveIntegerField(default=0, help_text="Seuil catalogue (défaut si seuil local non défini)")
    utilisateur = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    date_creation = models.DateField(auto_now_add=True)
    active_sortie = models.BooleanField(default=True, null=True, help_text="Archivage catalogue global (toutes localités).")
    archive_par = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pieces_archivees_catalogue',
    )
    archive_le = models.DateTimeField(null=True, blank=True)
    archive_motif = models.TextField(
        max_length=500,
        blank=True,
        default='',
        help_text="Motif de l'archivage catalogue.",
    )
    history = HistoricalRecords()
    def __str__(self):
        return f"{self.designation} - {self.numero_piece}"

    def clean(self):
        super().clean()
        if self.sous_categorie_id:
            parent_id = self.sous_categorie.categorie_id
            if self.categorie_id and parent_id != self.categorie_id:
                raise ValidationError({
                    'sous_categorie': (
                        "La sous-catégorie doit appartenir à la catégorie de la pièce."
                    ),
                })
            if not self.categorie_id:
                self.categorie_id = parent_id

    def save(self, *args, **kwargs):
        if self.sous_categorie_id:
            parent_id = self.sous_categorie.categorie_id
            if parent_id and self.categorie_id != parent_id:
                self.categorie_id = parent_id
        super().save(*args, **kwargs)

    @property
    def libelle_classification(self):
        if self.sous_categorie_id:
            return f"{self.categorie.categorie} / {self.sous_categorie.nom}"
        return self.categorie.categorie if self.categorie_id else ''

    @property
    def slug(self):
        """Libellé URL-friendly pour la fiche boutique."""
        return slugify(self.designation) or slugify(self.numero_piece) or f'piece-{self.pk}'

    def get_absolute_url(self):
        return reverse('ecommerce_shop_detail', kwargs={'piece_id': self.pk, 'slug': self.slug})

    @property
    def quantite_totale(self):
        return self.stocks.aggregate(t=models.Sum('quantite_disponible'))['t'] or 0
    def quantite_pour(self, local_entrepot):
        from .stock_local_service import get_quantite
        return get_quantite(self, local_entrepot)

    def prix_pour(self, local_entrepot):
        """Prix de vente appliqué dans une localité (local si défini, sinon catalogue)."""
        from .stock_local_service import get_prix_unitaire
        return get_prix_unitaire(self, local_entrepot)

    @property
    def prix_unitaire_catalogue(self):
        """Alias explicite du prix catalogue."""
        return self.prix_unitaire
    @property
    def quantite_en_danger(self):
        qty = getattr(self, 'quantite_disponible', None)
        seuil = getattr(self, 'seuil_affiche', None) or self.seuil
        if qty is None:
            qty = self.quantite_totale
        if seuil > 0:
            return qty < (seuil / 2)
        return False
    @property
    def color_status(self):
        qty = getattr(self, 'quantite_disponible', None)
        seuil = getattr(self, 'seuil_affiche', None) or self.seuil
        if qty is None:
            qty = self.quantite_totale
        from .stock_local_service import piece_color_status
        return piece_color_status(qty, seuil)

    @property
    def gallery_images(self):
        """Images pour la galerie fiche (principale + supplémentaires)."""
        images = []
        if self.image:
            images.append({'url': self.image.url, 'alt': self.designation})
        for extra in self.images_supplementaires.all():
            if extra.image:
                images.append({
                    'url': extra.image.url,
                    'alt': extra.legende or self.designation,
                })
        return images

    def sync_supplementary_images(self, request):
        """Supprime ou ajoute des images supplémentaires depuis le modal magasin."""
        from django.db.models import Max

        delete_ids = []
        for raw in request.POST.getlist('delete_piece_images'):
            try:
                delete_ids.append(int(raw))
            except (TypeError, ValueError):
                continue
        if delete_ids:
            self.images_supplementaires.filter(pk__in=delete_ids).delete()

        new_files = request.FILES.getlist('images_supplementaires')
        if not new_files:
            return

        max_ordre = self.images_supplementaires.aggregate(m=Max('ordre'))['m'] or 0
        for offset, uploaded in enumerate(new_files, start=1):
            if uploaded:
                PieceImage.objects.create(
                    piece=self,
                    image=uploaded,
                    ordre=max_ordre + offset,
                )


class PieceImage(models.Model):
    """Image supplémentaire affichée dans la galerie fiche d'une pièce."""
    piece = models.ForeignKey(
        Piece,
        on_delete=models.CASCADE,
        related_name='images_supplementaires',
    )
    image = models.ImageField(upload_to='pieces/galerie')
    ordre = models.PositiveSmallIntegerField(default=0)
    legende = models.CharField(max_length=255, blank=True, default='')
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['ordre', 'pk']
        verbose_name = 'Image supplémentaire'
        verbose_name_plural = 'Images supplémentaires'

    def __str__(self):
        return f"{self.piece.numero_piece} — image #{self.pk}"


class StockLocal(models.Model):
    """Stock d'une pièce dans une localité."""
    piece = models.ForeignKey(Piece, on_delete=models.CASCADE, related_name='stocks')
    local_entrepot = models.ForeignKey(LocalEntrepot, on_delete=models.CASCADE, related_name='stocks')
    quantite_disponible = models.PositiveIntegerField(default=0)
    seuil_local = models.PositiveIntegerField(default=0)
    prix_unitaire_local = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Prix de vente fixé par la localité. Si vide, utilise le prix catalogue.",
    )
    emplacement = models.CharField(max_length=255, blank=True, default='')
    active_sortie = models.BooleanField(
        default=True,
        help_text="Si False, la pièce est masquée à la vente dans cette localité uniquement.",
    )
    archive_par = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='stocks_archives',
    )
    archive_le = models.DateTimeField(null=True, blank=True)
    archive_motif = models.TextField(
        max_length=500,
        blank=True,
        default='',
        help_text="Motif de l'archivage dans cette localité.",
    )
    date_maj = models.DateTimeField(auto_now=True)
    history = HistoricalRecords()
    class Meta:
        unique_together = ('piece', 'local_entrepot')
        indexes = [models.Index(fields=['piece', 'local_entrepot']),]
        verbose_name = 'Stock local'
        verbose_name_plural = 'Stocks par localité'
    def __str__(self):
        return f"{self.piece.numero_piece} @ {self.local_entrepot.nom} : {self.quantite_disponible}"


STATUT_TRANSFERT_CHOICES = (
    ('en_attente', 'En attente'),
    ('en_transit', 'En transit'),
    ('recu', 'Reçu'),
    ('annule', 'Annulé'),
)

STATUT_DEMANDE_TRANSFERT_CHOICES = (
    ('en_attente', 'En attente'),
    ('validee', 'Validée'),
    ('recue', 'Reçue'),
    ('annulee_demandeur', 'Annulée (demandeur)'),
    ('annulee_donneur', 'Annulée (donneur)'),
)

ORIGINE_ENTREE_CHOICES = (
    ('fournisseur', 'Fournisseur'),
    ('transfert_local', 'Transfert inter-localité'),
)

class TransfertStock(models.Model):
    """Mouvement de stock entre deux localités."""
    numero = models.CharField(max_length=30, unique=True)
    piece = models.ForeignKey(Piece, on_delete=models.PROTECT, related_name='transferts')
    quantite = models.PositiveIntegerField()
    local_source = models.ForeignKey(
        LocalEntrepot, on_delete=models.PROTECT, related_name='transferts_sortants'
    )
    local_destination = models.ForeignKey(
        LocalEntrepot, on_delete=models.PROTECT, related_name='transferts_entrants'
    )
    statut = models.CharField(max_length=15, choices=STATUT_TRANSFERT_CHOICES, default='en_attente')
    demandeur = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name='transferts_demandes'
    )
    receveur = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='transferts_recus'
    )
    motif = models.TextField(blank=True)
    date_demande = models.DateTimeField(auto_now_add=True)
    date_envoi = models.DateTimeField(null=True, blank=True)
    date_reception = models.DateTimeField(null=True, blank=True)
    history = HistoricalRecords()
    class Meta:
        ordering = ['-date_demande']
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(local_source=models.F('local_destination')),
                name='transfert_source_diff_destination',
            ),
        ]
    def __str__(self):
        return f"{self.numero} — {self.piece.numero_piece} ({self.get_statut_display()})"

class DemandeTransfert(models.Model):
    """Demande de transfert multi-lignes (workflow chef d'agence demandeur / donneur)."""
    numero_demande = models.CharField(max_length=30, unique=True)
    local_demandeur = models.ForeignKey(LocalEntrepot, on_delete=models.PROTECT, related_name='demandes_transfert_emises')
    local_donneur = models.ForeignKey(LocalEntrepot, on_delete=models.PROTECT, related_name='demandes_transfert_recues')
    motif = models.TextField(blank=True)
    numero_bon_commande = models.CharField(max_length=100, blank=True, help_text="Numéro saisi par le demandeur à la réception (doit correspondre au bon donneur).",)
    numero_bon_donneur = models.CharField(max_length=100, blank=True, help_text="Numéro de bon saisi par le chef d'agence donneur à la validation.")
    statut = models.CharField(max_length=20, choices=STATUT_DEMANDE_TRANSFERT_CHOICES, default='en_attente')
    demandeur = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='demandes_transfert_creees')
    validateur_donneur = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='demandes_transfert_validees')
    receveur = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='demandes_transfert_recues_user')
    date_demande = models.DateTimeField(auto_now_add=True)
    date_validation = models.DateTimeField(null=True, blank=True)
    date_reception = models.DateTimeField(null=True, blank=True)
    history = HistoricalRecords()
    class Meta:
        ordering = ['-date_demande']
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(local_demandeur=models.F('local_donneur')),
                name='demande_transfert_demandeur_diff_donneur',
            ),
        ]
    def __str__(self):
        return f"{self.numero_demande} ({self.get_statut_display()})"

    def get_statut_affichage(self, user=None):
        """Libellé de statut selon l'agence et l'avancement de la livraison."""
        if self.statut != 'validee':
            return self.get_statut_display()
        bon = getattr(self, 'bon_livraison', None)
        if bon is None:
            try:
                bon = self.bon_livraison
            except Exception:
                bon = None
        if not bon:
            return self.get_statut_display()
        return bon.get_statut_affichage(user)

    def get_statut_affichage_slug(self, user=None):
        """Classe CSS pour le badge de statut."""
        if self.statut != 'validee':
            return self.statut
        bon = getattr(self, 'bon_livraison', None)
        if bon is None:
            try:
                bon = self.bon_livraison
            except Exception:
                bon = None
        if not bon:
            return 'validee'
        return bon.get_statut_affichage_slug(user)

    @property
    def nb_lignes(self):
        return self.lignes.count()

class LigneDemandeTransfert(models.Model):
    demande = models.ForeignKey(
        DemandeTransfert, on_delete=models.CASCADE, related_name='lignes'
    )
    piece = models.ForeignKey(Piece, on_delete=models.PROTECT, related_name='lignes_demande_transfert')
    quantite = models.PositiveIntegerField()
    history = HistoricalRecords()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['demande', 'piece'],
                name='demande_transfert_ligne_piece_unique',
            ),
        ]
    def __str__(self):
        return f"{self.piece.numero_piece} × {self.quantite}"

class EntrePiece(models.Model):
    piece = models.ForeignKey(Piece, on_delete=models.CASCADE)
    local_entrepot = models.ForeignKey(LocalEntrepot, on_delete=models.CASCADE, related_name='entrees_stock', null=True, blank=True)
    quantitajout = models.PositiveIntegerField(default=0)
    prix_achat = models.DecimalField(max_digits=10, decimal_places=2)
    date_creation = models.DateField(auto_now_add=True)
    utilisateur = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    fournisseur = models.ForeignKey(Fournisseur, on_delete=models.SET_NULL, null=True)
    origine_type = models.CharField(max_length=20, choices=ORIGINE_ENTREE_CHOICES, default='fournisseur')
    origine_local = models.ForeignKey(LocalEntrepot, on_delete=models.SET_NULL, null=True, blank=True, related_name='entrees_provenance_transfert')
    demande_transfert = models.ForeignKey(DemandeTransfert, on_delete=models.SET_NULL, null=True, blank=True, related_name='entrees_stock')
    date = models.DateTimeField(auto_now_add=True)
    history = HistoricalRecords()
    def __str__(self):
        return '%s - %s ' % (self.piece.designation, self.quantitajout)
    def save(self, *args, **kwargs):
        from .stock_local_service import ajuster_stock_entree, incrementer_stock
        if not self.local_entrepot_id:
            raise ValueError("local_entrepot est obligatoire pour une entrée en stock.")
        if self.pk:
            old = EntrePiece.objects.get(pk=self.pk)
            ajuster_stock_entree(
                self.piece, self.local_entrepot, old.quantitajout, self.quantitajout
            )
        else:
            incrementer_stock(self.piece, self.local_entrepot, self.quantitajout)
        super().save(*args, **kwargs)
    def delete(self, *args, **kwargs):
        from .stock_local_service import decrementer_stock
        if self.local_entrepot_id and self.quantitajout:
            try:
                decrementer_stock(self.piece, self.local_entrepot, self.quantitajout)
            except Exception:
                pass
        super().delete(*args, **kwargs)

#############################                                ##################################
############################# INSERTION DE MOYEN DE PAIEMENT ##################################
#############################                                ##################################
class Panier(models.Model):
    id = models.CharField(primary_key=True, unique=True, max_length=10, default=generate_numeric_id, editable=False)
    utilisateur = models.ForeignKey(User, on_delete=models.SET_NULL, related_name='paniers', null=True, blank=True)
    local_entrepot = models.ForeignKey(LocalEntrepot, on_delete=models.SET_NULL, related_name='paniers', null=True, blank=True)
    valide = models.BooleanField(default=False)
    panier_paye = models.BooleanField(default=False)
    date_creation = models.DateField(auto_now_add=True)
    ticket = models.CharField(max_length=100, null=True, blank=True)
    panier_livre = models.BooleanField(default=False)
    date_paie_panier = models.DateField(null=True, blank=True)
    date_livr_panier = models.DateField(null=True, blank=True)
    date_save = models.DateTimeField(auto_now_add=True)
    history = HistoricalRecords()
    proforma = models.BooleanField(default=False)
    nom_client = models.CharField(max_length=255, null=True, blank=True)
    commande_en_ligne = models.BooleanField(default=False)
    MODE_RECEPTION_CHOICES = (
        ('livraison', 'Livraison'),
        ('retrait', 'Retrait en magasin'),
    )
    mode_reception = models.CharField(
        max_length=20,
        choices=MODE_RECEPTION_CHOICES,
        default='livraison',
        blank=True,
    )
    livraison_pays = models.ForeignKey(
        'ecommerce.PaysLivraison',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='paniers',
    )
    livraison_ville = models.ForeignKey(
        'ecommerce.VilleLivraison',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='paniers',
    )
    livraison_commune = models.ForeignKey(
        'ecommerce.CommuneLivraison',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='paniers',
    )
    adresse_domicile = models.CharField(max_length=255, null=True, blank=True)
    telephone_livraison = models.CharField(max_length=20, null=True, blank=True)
    frais_livraison = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
    )
    instruction_livraison = models.TextField(null=True, blank=True)

    @property
    def total(self):
        from .stock_local_service import total_panier_items
        return total_panier_items(self.panier_items.all(), self.local_entrepot, panier=self)

class MoyenPaiement(models.Model):
    nom = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=100, unique=True)
    actif = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)
    def __str__(self):
        return self.nom

class PanierItem(models.Model):
    panier = models.ForeignKey(Panier, on_delete=models.CASCADE, related_name='panier_items')
    piece = models.ForeignKey(Piece, on_delete=models.CASCADE)
    new_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.0)
    quantite = models.PositiveIntegerField(default=1)
    date_creation = models.DateField(auto_now_add=True)
    history = HistoricalRecords()
    @property
    def prix_unitaire_applique(self):
        from .stock_local_service import get_prix_unitaire
        loc = self.panier.local_entrepot if self.panier_id else None
        return get_prix_unitaire(self.piece, loc, self.new_price)

    @property
    def prix_total(self):
        return self.prix_unitaire_applique * self.quantite

STATUT_COMMANDE_CHOICES = (
    ('en_attente', 'En attente'),
    ('valider', 'Validée'),
    ('annuler', 'Annulée'),
    ('livrer', 'Livrée'),
)


class BaremeTimbre(models.Model):
    """Barème de timbre fiscal (modifiable via l'admin, sans redéploiement)."""
    montant_min = models.DecimalField(max_digits=12, decimal_places=2)
    montant_max = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Laisser vide pour la tranche « et plus ».",
    )
    montant_timbre = models.DecimalField(max_digits=10, decimal_places=2)
    actif = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ['montant_min']
        verbose_name = 'Barème de timbre'
        verbose_name_plural = 'Barèmes de timbres'

    def __str__(self):
        if self.montant_max:
            return f"[{self.montant_min:,.0f} – {self.montant_max:,.0f}] → {self.montant_timbre:,.0f} F"
        return f"[{self.montant_min:,.0f} et plus] → {self.montant_timbre:,.0f} F"

    def contient(self, montant) -> bool:
        montant = Decimal(str(montant))
        if montant < self.montant_min:
            return False
        if self.montant_max is None:
            return True
        return montant <= self.montant_max


class ParametreTVA(models.Model):
    """Configuration de taux TVA (au plus un actif pour la caisse)."""
    libelle = models.CharField(
        max_length=100,
        blank=True,
        default='',
        help_text="Ex. TVA standard, TVA réduite…",
    )
    active = models.BooleanField(
        default=False,
        help_text="Si activé, la case TVA apparaît en caisse lors du paiement.",
    )
    taux = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('18.00'),
        help_text="Taux de TVA en pourcentage (ex. 18 pour 18 %).",
    )
    date_maj = models.DateTimeField(auto_now=True)
    history = HistoricalRecords()

    class Meta:
        verbose_name = 'Paramètre TVA'
        verbose_name_plural = 'Paramètres TVA'
        ordering = ['-active', 'taux', 'pk']

    def __str__(self):
        etat = 'activée' if self.active else 'désactivée'
        label = self.libelle.strip() or f'TVA {self.taux}%'
        return f"{label} ({etat})"


class Commande(models.Model):
    id = models.CharField(primary_key=True, unique=True, max_length=10, default=generate_numeric_id, editable=False)
    panier = models.ForeignKey(Panier, on_delete=models.CASCADE, related_name='commands')
    numero_commande = models.CharField(max_length=100)
    statut_commande = models.CharField(max_length=20, choices=STATUT_COMMANDE_CHOICES, default='en_attente')
    total_sans_remise = models.DecimalField(max_digits=10, decimal_places=2)
    total = models.DecimalField(max_digits=10, decimal_places=2)
    date_creation = models.DateField(auto_now_add=True)
    paye = models.BooleanField(default=False)
    montant_paye = models.DecimalField(max_digits=10, decimal_places=2, default=0.0)
    montant_reste = models.DecimalField(max_digits=10, decimal_places=2, default=0.0)
    remise = models.DecimalField(max_digits=10, decimal_places=2, default=0.0)
    utilisateur = models.ForeignKey(User, on_delete=models.SET_NULL, related_name='commandes_validees', null=True, blank=True)
    profoma = models.PositiveIntegerField(default=0)
    moyen_paiement = models.ForeignKey(MoyenPaiement, on_delete=models.SET_NULL, null=True, blank=True)
    montant_timbre = models.DecimalField( max_digits=10, decimal_places=2, default=Decimal('0.00'),
        help_text="Timbre fiscal (espèces). Non inclus dans le CA (total).",
    )
    bareme_timbre = models.ForeignKey(
        BaremeTimbre, on_delete=models.SET_NULL, null=True, blank=True, related_name='commandes',
        help_text="Barème appliqué au moment du paiement.",
    )
    montant_tva = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'),
        help_text="Montant TVA appliqué au paiement (optionnel).",
    )
    tva_appliquee = models.BooleanField(default=False,
        help_text="Indique si la TVA a été cochée et appliquée au paiement.",
    )
    date = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    commande_en_ligne = models.BooleanField(default=False)
    livreur = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='commandes_livraison_online',)
    client_confirme_reception = models.BooleanField(default=False)
    date_reception_client = models.DateTimeField(null=True, blank=True)
    history = HistoricalRecords()

    @property
    def total_a_encaisser(self):
        return (
            self.total
            + (self.montant_timbre or Decimal('0'))
            + (self.montant_tva or Decimal('0'))
        )

    @property
    def libelle_paiement(self):
        """Espèce, ou Paiement numérique (Wave / Orange Money / …)."""
        from stock.paiement_labels import libelle_moyen_paiement_commande
        return libelle_moyen_paiement_commande(self)


class Ticket(models.Model):
    id = models.CharField(primary_key=True, unique=True, max_length=10, default=generate_numeric_id, editable=False)
    numero = models.CharField(max_length=100)
    commande = models.OneToOneField(Commande, on_delete=models.CASCADE)
    date_creation = models.DateField(auto_now_add=True)
    utilise = models.BooleanField(default=False)
    client_name = models.CharField(max_length=25, blank=True, null=True)
    utilisateur = models.ForeignKey(User, on_delete=models.SET_NULL, related_name='livraisons_effectuees', null=True, blank=True)
    fichier_pdf = models.FileField(upload_to='tickets/', null=True, blank=True)
    date_save = models.DateTimeField(auto_now_add=True)
    history = HistoricalRecords()


class GeniusPayPaiement(models.Model):
    SOURCE_ecommerce = 'ecommerce'
    SOURCE_CAISSE = 'caisse'
    SOURCE_CHOICES = (
        (SOURCE_ecommerce, 'E-commerce'),
        (SOURCE_CAISSE, 'Caisse'),
    )
    STATUT_CHOICES = (
        ('pending', 'En attente'),
        ('processing', 'En cours'),
        ('completed', 'Complété'),
        ('failed', 'Échoué'),
        ('cancelled', 'Annulé'),
        ('expired', 'Expiré'),
    )
    commande = models.ForeignKey(
        Commande,
        on_delete=models.CASCADE,
        related_name='paiements_geniuspay',
    )
    reference = models.CharField(max_length=64, unique=True, db_index=True)
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES)
    statut = models.CharField(max_length=20, choices=STATUT_CHOICES, default='pending')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    checkout_url = models.TextField(blank=True, default='')
    payment_method = models.CharField(max_length=64, blank=True, default='')
    environment = models.CharField(max_length=20, blank=True, default='')
    appliquer_tva = models.BooleanField(default=False)
    caissier = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='paiements_geniuspay_caisse',
    )
    metadata = models.JSONField(default=dict, blank=True)
    raw_response = models.JSONField(default=dict, blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)
    date_maj = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date_creation']
        verbose_name = 'Paiement GeniusPay'
        verbose_name_plural = 'Paiements GeniusPay'

    def __str__(self):
        return f'{self.reference} ({self.get_statut_display()})'


class BonCommandePaiement(models.Model):
    """Bon de commande émis en caisse lors de la validation du paiement."""
    numero_bon = models.CharField(max_length=30, unique=True)
    commande = models.OneToOneField(
        Commande, on_delete=models.CASCADE, related_name='bon_paiement'
    )
    ticket_numero = models.CharField(max_length=100)
    local_entrepot = models.ForeignKey(
        LocalEntrepot, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='bons_commande_paiement',
    )
    caissier = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name='bons_caisse_emis'
    )
    hote_accueil = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='bons_hote_accueil',
    )
    client_nom = models.CharField(max_length=255, blank=True)
    moyen_paiement_nom = models.CharField(max_length=100, blank=True)
    montant_paye = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    montant_reste = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_commande = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    fichier_pdf = models.FileField(upload_to='bons_vente/', null=True, blank=True)
    date_emission = models.DateTimeField(auto_now_add=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ['-date_emission']
        verbose_name = 'Bon de commande (paiement)'
        verbose_name_plural = 'Bons de commande (paiement)'

    def __str__(self):
        return f"{self.numero_bon} — {self.ticket_numero}"


class Notification(models.Model):
    TYPE_CHOICES = (
        ('stock_alerte', 'Alerte de Stock'),
        ('transfert_demande', 'Demande de transfert'),
        ('commande_en_ligne', 'Commande en ligne'),
        ('chat_client', 'Chat client'),
        ('info', 'Information'),
        ('warning', 'Avertissement'),
        ('success', 'Succès'),
    )
    id = ShortUUIDField(primary_key=True, unique=True, length=8, alphabet="abcd1234", editable=False)
    type_notification = models.CharField(max_length=20, choices=TYPE_CHOICES, default='stock_alerte')
    titre = models.CharField(max_length=255)
    message = models.TextField()
    utilisateur = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications', null=True, blank=True)
    lu = models.BooleanField(default=False)
    date_creation = models.DateTimeField(auto_now_add=True)
    creneau = models.CharField(max_length=32, blank=True, null=True, db_index=True)
    pieces_alerte = models.ManyToManyField(Piece, related_name='notifications_alerte', blank=True)
    class Meta:
        ordering = ['-date_creation']
        verbose_name = 'Notification'
        verbose_name_plural = 'Notifications'
    def __str__(self):
        return f"{self.titre} - {self.date_creation.strftime('%d/%m/%Y %H:%M')}"

# ============================================================================
# LIVRAISON DES TRANSFERTS INTER-LOCALITÉS
# ============================================================================

class TarifLivraison(models.Model):
    """Tarif de livraison inter-localités (un seul actif à la fois)."""
    cout_par_km = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text="Coût par défaut en FCFA / km (si aucun palier ne s'applique)",
    )
    actif = models.BooleanField(default=True)
    date_effet = models.DateTimeField(auto_now_add=True)
    date_fin = models.DateTimeField(null=True, blank=True)
    cree_par = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='tarifs_livraison_crees',
    )
    motif = models.TextField(
        blank=True,
        help_text="Raison du changement de tarif",
    )
    history = HistoricalRecords()

    class Meta:
        ordering = ['-date_effet']
        verbose_name = 'Tarif de livraison'
        verbose_name_plural = 'Tarifs de livraison'

    def __str__(self):
        statut = 'ACTIF' if self.actif else 'Archivé'
        return f'{self.cout_par_km} F/km — {statut} ({self.date_effet:%d/%m/%Y})'

    def save(self, *args, **kwargs):
        if self.actif:
            TarifLivraison.objects.filter(actif=True).exclude(pk=self.pk).update(
                actif=False, date_fin=timezone.now(),
            )
        super().save(*args, **kwargs)

    @classmethod
    def get_actif(cls):
        return cls.objects.filter(actif=True).first()


class PalierLivraison(models.Model):
    """Intervalle de kilomètres avec un tarif spécifique (optionnel)."""
    tarif = models.ForeignKey(
        TarifLivraison, on_delete=models.CASCADE, related_name='paliers',
    )
    km_min = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('0.00'))
    km_max = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True,
        help_text="Laisser vide pour « et plus »",
    )
    cout_par_km = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text="Tarif FCFA/km pour cet intervalle",
    )
    ordre = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['ordre', 'km_min']
        verbose_name = 'Palier de livraison'
        verbose_name_plural = 'Paliers de livraison'

    def __str__(self):
        max_km = f'{self.km_max} km' if self.km_max is not None else '∞'
        return f'{self.km_min}–{max_km} : {self.cout_par_km} F/km'


STATUT_LIVRAISON_CHOICES = (
    ('en_preparation', 'En attente'),
    ('en_route', 'En route'),
    ('livre', 'Livré'),
    ('annule', 'Annulé'),
)


class BonLivraison(models.Model):
    """Bon de livraison rattaché à une demande de transfert validée."""
    numero_bon_livraison = models.CharField(max_length=30, unique=True)
    demande_transfert = models.OneToOneField(
        DemandeTransfert,
        on_delete=models.CASCADE,
        related_name='bon_livraison',
    )
    livreur = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='livraisons_transferts',
        help_text="Livreur de la localité donneuse",
    )
    distance_km = models.DecimalField(
        max_digits=8, decimal_places=2,
        help_text="Distance estimée entre les deux localités",
    )
    tarif_applique = models.ForeignKey(
        TarifLivraison, on_delete=models.PROTECT,
        related_name='bons_livraison',
    )
    cout_base = models.DecimalField(
        max_digits=12, decimal_places=2,
        help_text="Coût calculé automatiquement (figé à la création)",
    )
    cout_ajustement = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
        help_text="Ajustement manuel (+ surcharge, − remise)",
    )
    motif_ajustement = models.TextField(blank=True)
    statut = models.CharField(
        max_length=20, choices=STATUT_LIVRAISON_CHOICES,
        default='en_preparation',
    )
    date_creation = models.DateTimeField(auto_now_add=True)
    date_depart = models.DateTimeField(null=True, blank=True)
    date_livraison_effective = models.DateTimeField(null=True, blank=True)
    cree_par = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='bons_livraison_crees',
    )
    observations = models.TextField(blank=True)
    fichier_pdf = models.FileField(upload_to='bons_livraison/', null=True, blank=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ['-date_creation']
        verbose_name = 'Bon de livraison'
        verbose_name_plural = 'Bons de livraison'

    def __str__(self):
        return f'{self.numero_bon_livraison} — {self.livreur.username}'

    @property
    def cout_total(self):
        return self.cout_base + (self.cout_ajustement or Decimal('0.00'))

    def clean(self):
        from django.core.exceptions import ValidationError
        super().clean()
        if self.livreur_id and self.demande_transfert_id:
            local_donneur = self.demande_transfert.local_donneur
            if self.livreur.local_entrepot_id != local_donneur.pk:
                raise ValidationError({
                    'livreur': (
                        f"Le livreur doit appartenir à la localité « {local_donneur.nom} »."
                    ),
                })
        if self.livreur_id and getattr(self.livreur, 'role', None) != 'livreur':
            raise ValidationError({
                'livreur': "L'utilisateur sélectionné n'a pas le rôle « livreur ».",
            })
        ajust = self.cout_ajustement or Decimal('0.00')
        if ajust != Decimal('0.00') and not (self.motif_ajustement or '').strip():
            raise ValidationError({
                'motif_ajustement': 'Un motif est requis pour tout ajustement de coût.',
            })

    def get_statut_affichage(self, user=None):
        """Libellé selon l'agence : demandeur voit « En cours » quand livraison en route."""
        if self.statut == 'en_route' and user is not None:
            loc_id = getattr(user, 'local_entrepot_id', None)
            if loc_id == self.demande_transfert.local_demandeur_id:
                return 'En cours'
        return self.get_statut_display()

    def get_statut_affichage_slug(self, user=None):
        if self.statut == 'en_preparation':
            return 'en_attente'
        if self.statut == 'en_route':
            loc_id = getattr(user, 'local_entrepot_id', None) if user else None
            if loc_id == self.demande_transfert.local_demandeur_id:
                return 'en_cours'
            return 'en_route'
        if self.statut == 'livre':
            return 'livre'
        if self.statut == 'annule':
            return 'annule'
        return self.statut

    def marquer_en_route(self):
        self.statut = 'en_route'
        self.date_depart = timezone.now()
        self.save(update_fields=['statut', 'date_depart'])

    def marquer_livre(self):
        self.statut = 'livre'
        self.date_livraison_effective = timezone.now()
        self.save(update_fields=['statut', 'date_livraison_effective'])