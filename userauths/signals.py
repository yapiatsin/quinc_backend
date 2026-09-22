# signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import CustomUser, ProfilUser

@receiver(post_save, sender=CustomUser)
def creer_ou_mettre_a_jour_profil(sender, instance, created, raw=False, **kwargs):
    # raw=True signifie que l'objet vient d'un chargement de fixture (loaddata).
    # Le fichier contient deja les ProfilUser : en creer un ici ferait echouer
    # l'import sur la contrainte d'unicite de ProfilUser.user.
    if raw:
        return
    if created:
        ProfilUser.objects.create(user=instance)
    else:
        # Utilisateurs créés avant ProfilUser peuvent ne pas avoir de profil
        profil, _ = ProfilUser.objects.get_or_create(user=instance)
        profil.save()
