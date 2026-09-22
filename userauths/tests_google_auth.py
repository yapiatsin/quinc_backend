"""Tests de la connexion par compte Google.

`verify_credential` est la seule frontiere avec Google : c'est le seul point
remplace. Tout le reste — resolution du compte, envoi de l'OTP, plafond de
tentatives, expiration, liaison, session — est exerce reellement.

Ces tests couvrent les trois parcours decrits dans userauths/google_auth.py et
les refus qui les protegent. Sans eux, une regression sur l'authentification
passerait inapercue jusqu'en production.
"""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .google_auth import GoogleAuthError, OTP_MAX_ATTEMPTS, OTP_EXPIRY_SECONDS
from .google_views import SESSION_KEY, RESEND_COOLDOWN_SECONDS, RESEND_MAX
from .models import GoogleIdentity, PWD_FORGET

User = get_user_model()

NOUVEAU = "nouveau.client@example.com"
EXISTANT = "employe.existant@example.com"


def profil(email, sub="sub-test-1", **extra):
    base = {
        "sub": sub,
        "email": email,
        "given_name": "Test",
        "family_name": "Google",
        "name": "Test Google",
        "picture": "https://exemple.invalid/photo.jpg",
    }
    base.update(extra)
    return base


class GoogleLoginBase(TestCase):
    url_login = None
    url_otp = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.url_login = reverse("google_login")
        cls.url_otp = reverse("google_otp")

    def poster_jeton(self, email, sub="sub-test-1"):
        """Simule le POST du navigateur avec un jeton deja valide par Google."""
        with patch(
            "userauths.google_auth.verify_credential",
            return_value=profil(email, sub),
        ):
            return self.client.post(
                self.url_login,
                data='{"credential": "jeton-factice"}',
                content_type="application/json",
            )

    def est_connecte(self):
        return "_auth_user_id" in self.client.session


class CompteInconnu(GoogleLoginBase):
    """Adresse inconnue : creation d'un compte client, connexion immediate."""

    def test_cree_un_compte_client_et_connecte(self):
        reponse = self.poster_jeton(NOUVEAU)
        self.assertEqual(reponse.status_code, 200)
        donnees = reponse.json()
        self.assertTrue(donnees["success"])
        self.assertNotIn("otp_required", donnees)
        self.assertTrue(donnees["redirect"])

        utilisateur = User.objects.get(email=NOUVEAU)
        self.assertEqual(utilisateur.role, "client")
        self.assertTrue(utilisateur.is_active)
        self.assertTrue(self.est_connecte())

    def test_le_compte_cree_n_a_pas_de_mot_de_passe_utilisable(self):
        """Sans cela, un mot de passe vide ouvrirait la session par la voie classique."""
        self.poster_jeton(NOUVEAU)
        self.assertFalse(User.objects.get(email=NOUVEAU).has_usable_password())

    def test_l_identite_est_liee(self):
        self.poster_jeton(NOUVEAU, sub="sub-abc")
        identite = GoogleIdentity.objects.get(sub="sub-abc")
        self.assertEqual(identite.user.email, NOUVEAU)
        self.assertIsNotNone(identite.last_login_at)

    def test_aucun_code_n_est_envoye(self):
        self.poster_jeton(NOUVEAU)
        self.assertEqual(len(mail.outbox), 0)


class IdentiteDejaLiee(GoogleLoginBase):
    """Identite connue : connexion directe, sans code ni doublon."""

    def setUp(self):
        self.utilisateur = User.objects.create_user(
            username="deja_lie", email=EXISTANT, password="MotDePasse!42",
            role="client",
        )
        GoogleIdentity.objects.create(
            user=self.utilisateur, sub="sub-lie", email=EXISTANT,
        )

    def test_connexion_directe(self):
        donnees = self.poster_jeton(EXISTANT, sub="sub-lie").json()
        self.assertTrue(donnees["success"])
        self.assertNotIn("otp_required", donnees)
        self.assertTrue(self.est_connecte())

    def test_aucun_compte_supplementaire(self):
        self.poster_jeton(EXISTANT, sub="sub-lie")
        self.assertEqual(User.objects.filter(email=EXISTANT).count(), 1)

    def test_compte_desactive_refuse(self):
        self.utilisateur.is_active = False
        self.utilisateur.save(update_fields=["is_active"])
        reponse = self.poster_jeton(EXISTANT, sub="sub-lie")
        self.assertEqual(reponse.status_code, 403)
        self.assertFalse(self.est_connecte())

    def test_l_identite_suit_un_changement_d_adresse_google(self):
        """La cle est le `sub`, pas l'e-mail : le compte reste retrouve."""
        self.poster_jeton("nouvelle.adresse@example.com", sub="sub-lie")
        self.assertTrue(self.est_connecte())
        self.assertEqual(User.objects.filter(email="nouvelle.adresse@example.com").count(), 0)


class CompteExistantAvecMotDePasse(GoogleLoginBase):
    """Adresse d'un compte existant : code exige avant la liaison."""

    def setUp(self):
        self.utilisateur = User.objects.create_user(
            username="employe", email=EXISTANT, password="MotDePasse!42",
            role="gestionnaire",
        )
        mail.outbox = []

    def demander_liaison(self):
        return self.poster_jeton(EXISTANT, sub="sub-a-lier")

    def code_en_base(self):
        return PWD_FORGET.objects.filter(
            user_id=self.utilisateur,
            purpose=PWD_FORGET.PURPOSE_GOOGLE_LINK,
            status="0",
        ).latest("creat_at")

    def test_un_code_est_exige_et_envoye(self):
        donnees = self.demander_liaison().json()
        self.assertTrue(donnees["otp_required"])
        self.assertFalse(self.est_connecte())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(str(self.code_en_base().otp), mail.outbox[0].body)

    def test_aucune_liaison_avant_validation(self):
        self.demander_liaison()
        self.assertFalse(GoogleIdentity.objects.filter(user=self.utilisateur).exists())

    def test_bon_code_lie_et_connecte(self):
        self.demander_liaison()
        code = self.code_en_base().otp
        reponse = self.client.post(self.url_otp, {"otp": str(code)})
        self.assertEqual(reponse.status_code, 302)
        self.assertTrue(self.est_connecte())
        self.assertTrue(GoogleIdentity.objects.filter(user=self.utilisateur).exists())

    def test_le_code_ne_sert_qu_une_fois(self):
        self.demander_liaison()
        code = self.code_en_base().otp
        self.client.post(self.url_otp, {"otp": str(code)})
        entree = PWD_FORGET.objects.filter(
            user_id=self.utilisateur, purpose=PWD_FORGET.PURPOSE_GOOGLE_LINK,
        ).latest("creat_at")
        self.assertEqual(entree.status, "1")

    def test_mauvais_code_refuse(self):
        self.demander_liaison()
        self.client.post(self.url_otp, {"otp": "000000"})
        self.assertFalse(self.est_connecte())
        self.assertFalse(GoogleIdentity.objects.filter(user=self.utilisateur).exists())

    def test_plafond_de_tentatives(self):
        """Sans plafond, un code a six chiffres se devine par force brute."""
        self.demander_liaison()
        for _ in range(OTP_MAX_ATTEMPTS):
            self.client.post(self.url_otp, {"otp": "000000"})
        reponse = self.client.post(self.url_otp, {"otp": "000000"})
        self.assertEqual(reponse.status_code, 302)
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertFalse(self.est_connecte())

    def test_code_expire_refuse(self):
        self.demander_liaison()
        entree = self.code_en_base()
        PWD_FORGET.objects.filter(pk=entree.pk).update(
            creat_at=timezone.now() - timedelta(seconds=OTP_EXPIRY_SECONDS + 10)
        )
        self.client.post(self.url_otp, {"otp": str(entree.otp)})
        self.assertFalse(self.est_connecte())

    def test_reconnexion_directe_apres_liaison(self):
        self.demander_liaison()
        self.client.post(self.url_otp, {"otp": str(self.code_en_base().otp)})
        self.client.logout()
        donnees = self.poster_jeton(EXISTANT, sub="sub-a-lier").json()
        self.assertNotIn("otp_required", donnees)
        self.assertTrue(self.est_connecte())

    def test_page_otp_inaccessible_sans_demande(self):
        reponse = self.client.get(self.url_otp)
        self.assertEqual(reponse.status_code, 302)

    def test_compte_desactive_ne_recoit_pas_de_code(self):
        self.utilisateur.is_active = False
        self.utilisateur.save(update_fields=["is_active"])
        reponse = self.demander_liaison()
        self.assertEqual(reponse.status_code, 403)
        self.assertEqual(len(mail.outbox), 0)


class VerificationDuJeton(TestCase):
    """Refus au niveau du jeton, avant toute recherche de compte."""

    def test_adresse_non_verifiee_refusee(self):
        """Une adresse non verifiee par Google ne prouve rien."""
        from . import google_auth

        with patch.object(google_auth, "is_configured", return_value=True), \
             patch("google.oauth2.id_token.verify_oauth2_token") as verif:
            verif.return_value = {
                "iss": "https://accounts.google.com",
                "sub": "sub-x",
                "email": "non.verifie@example.com",
                "email_verified": False,
            }
            with self.assertRaises(GoogleAuthError):
                google_auth.verify_credential("jeton")

    def test_emetteur_inattendu_refuse(self):
        from . import google_auth

        with patch.object(google_auth, "is_configured", return_value=True), \
             patch("google.oauth2.id_token.verify_oauth2_token") as verif:
            verif.return_value = {
                "iss": "https://malveillant.example",
                "sub": "sub-x",
                "email": "a@example.com",
                "email_verified": True,
            }
            with self.assertRaises(GoogleAuthError):
                google_auth.verify_credential("jeton")

    def test_jeton_vide_refuse(self):
        from . import google_auth

        with self.assertRaises(GoogleAuthError):
            google_auth.verify_credential("")

    def test_sans_identifiant_client_la_connexion_est_indisponible(self):
        from . import google_auth

        with self.settings(GOOGLE_OAUTH_CLIENT_ID=""):
            self.assertFalse(google_auth.is_configured())
            with self.assertRaises(GoogleAuthError):
                google_auth.verify_credential("jeton")


class RenvoiDuCode(GoogleLoginBase):
    """Renvoi du code : delai minimal, plafond, et pas de remise a zero."""

    def setUp(self):
        self.utilisateur = User.objects.create_user(
            username="a_lier", email=EXISTANT, password="MotDePasse!42",
            role="gestionnaire",
        )
        self.poster_jeton(EXISTANT, sub="sub-renvoi")
        mail.outbox = []
        self.url_resend = reverse("google_otp_resend")

    def vieillir_envoi(self, secondes=RESEND_COOLDOWN_SECONDS + 1):
        """Recule la date du dernier envoi pour sortir du delai d'attente."""
        session = self.client.session
        data = session[SESSION_KEY]
        passe = (timezone.now() - timedelta(seconds=secondes)).isoformat()
        data["last_sent_at"] = passe
        data["started_at"] = passe
        session[SESSION_KEY] = data
        session.save()

    def test_renvoi_trop_rapide_refuse(self):
        self.client.post(self.url_resend)
        self.assertEqual(len(mail.outbox), 0)

    def test_renvoi_apres_delai_envoie_un_nouveau_code(self):
        self.vieillir_envoi()
        self.client.post(self.url_resend)
        self.assertEqual(len(mail.outbox), 1)

    def test_le_nouveau_code_fonctionne(self):
        self.vieillir_envoi()
        self.client.post(self.url_resend)
        code = PWD_FORGET.objects.filter(
            user_id=self.utilisateur,
            purpose=PWD_FORGET.PURPOSE_GOOGLE_LINK,
            status="0",
        ).latest("creat_at").otp
        self.client.post(self.url_otp, {"otp": str(code)})
        self.assertTrue(self.est_connecte())

    def test_l_ancien_code_est_invalide(self):
        """Un seul code vivant a la fois, sinon un ancien e-mail resterait utilisable."""
        ancien = PWD_FORGET.objects.filter(
            user_id=self.utilisateur, purpose=PWD_FORGET.PURPOSE_GOOGLE_LINK,
        ).latest("creat_at").otp
        self.vieillir_envoi()
        self.client.post(self.url_resend)
        self.client.post(self.url_otp, {"otp": str(ancien)})
        self.assertFalse(self.est_connecte())

    def test_plafond_de_renvois(self):
        for _ in range(RESEND_MAX):
            self.vieillir_envoi()
            self.client.post(self.url_resend)
        self.assertEqual(len(mail.outbox), RESEND_MAX)
        self.vieillir_envoi()
        self.client.post(self.url_resend)
        self.assertEqual(len(mail.outbox), RESEND_MAX)

    def test_le_renvoi_ne_remet_pas_les_tentatives_a_zero(self):
        """Sinon il suffirait de redemander un code pour relancer la force brute."""
        for _ in range(OTP_MAX_ATTEMPTS - 1):
            self.client.post(self.url_otp, {"otp": "000000"})
        self.vieillir_envoi()
        self.client.post(self.url_resend)
        self.assertEqual(self.client.session[SESSION_KEY]["attempts"], OTP_MAX_ATTEMPTS - 1)

    def test_renvoi_sans_demande_en_cours_refuse(self):
        self.client.session.flush()
        reponse = self.client.post(self.url_resend)
        self.assertEqual(reponse.status_code, 302)


class BoutonSurLesPages(TestCase):
    """Le bouton apparait sur connexion et inscription, et seulement si configure."""

    def test_present_sur_les_deux_pages_quand_configure(self):
        with self.settings(GOOGLE_OAUTH_CLIENT_ID="test.apps.googleusercontent.com"):
            for nom in ("connexion", "register"):
                html = self.client.get(reverse(nom)).content.decode("utf-8", "replace")
                self.assertIn("google-signin-button", html, nom)
                self.assertIn("accounts.google.com/gsi/client", html, nom)

    def test_absent_quand_non_configure(self):
        with self.settings(GOOGLE_OAUTH_CLIENT_ID=""):
            for nom in ("connexion", "register"):
                html = self.client.get(reverse(nom)).content.decode("utf-8", "replace")
                self.assertNotIn("google-signin-button", html, nom)


class EnTetesDeLaPageDeConnexion(TestCase):
    """La politique d'ouverture croisee doit laisser repondre la fenetre Google."""

    def test_coop_autorise_les_fenetres_surgissantes(self):
        """« same-origin » couperait window.opener et la fenetre Google
        resterait blanche sans jamais renvoyer le jeton."""
        reponse = self.client.get(reverse("connexion"))
        coop = reponse.headers.get("Cross-Origin-Opener-Policy", "")
        self.assertNotEqual(coop, "same-origin")
        if coop:
            self.assertIn("allow-popups", coop)
