from django import forms
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import ValidationError

from .models import (
    CustomUser,
    CustomPermission,
    TypeCustomPermission,
    LocalEntrepot,
    CreneauDisponibilite,
    ProfilUser,
)

AUTH_INPUT = {'class': 'auth-input'}
PROFILE_INPUT = {'class': 'profile-input'}
PROFILE_TEXTAREA = {'class': 'profile-input profile-input--textarea', 'rows': 3}


class ClientRegistrationForm(forms.ModelForm):
    password1 = forms.CharField(
        label='Mot de passe',
        widget=forms.PasswordInput(attrs={**AUTH_INPUT, 'placeholder': 'Mot de passe', 'autocomplete': 'new-password'}),
    )
    password2 = forms.CharField(
        label='Confirmation',
        widget=forms.PasswordInput(attrs={**AUTH_INPUT, 'placeholder': 'Confirmer le mot de passe', 'autocomplete': 'new-password'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.instance.role = 'client'

    class Meta:
        model = CustomUser
        fields = ('username', 'email', 'contact', 'genre')
        widgets = {
            'username': forms.TextInput(attrs={**AUTH_INPUT, 'placeholder': 'Nom utilisateur', 'autocomplete': 'username'}),
            'email': forms.EmailInput(attrs={**AUTH_INPUT, 'placeholder': 'Email', 'autocomplete': 'email'}),
            'contact': forms.TextInput(attrs={**AUTH_INPUT, 'placeholder': 'Téléphone', 'autocomplete': 'tel'}),
            'genre': forms.RadioSelect(),
        }

    def clean_username(self):
        username = (self.cleaned_data.get('username') or '').strip()
        if not username:
            raise ValidationError("Le nom d'utilisateur est obligatoire.")
        if CustomUser.objects.filter(username__iexact=username).exists():
            raise ValidationError("Ce nom d'utilisateur est déjà pris.")
        return username

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        if CustomUser.objects.filter(email__iexact=email).exists():
            raise ValidationError("Un compte existe déjà avec cet email.")
        return email

    def clean_password2(self):
        password1 = self.cleaned_data.get('password1')
        password2 = self.cleaned_data.get('password2')
        if password1 and password2 and password1 != password2:
            raise ValidationError("Les mots de passe ne correspondent pas.")
        if password2 and len(password2) < 8:
            raise ValidationError("Le mot de passe doit contenir au moins 8 caractères.")
        return password2

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = user.email.strip().lower()
        user.username = user.username.strip()
        user.role = 'client'
        user.is_active = False
        user.set_password(self.cleaned_data['password1'])
        if commit:
            user.save()
        return user


class PasswordResetRequestForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={**AUTH_INPUT, 'placeholder': 'Email', 'autocomplete': 'email'}),
    )

    def clean_email(self):
        return (self.cleaned_data.get('email') or '').strip().lower()


class PasswordResetConfirmForm(forms.Form):
    new_password = forms.CharField(
        min_length=8,
        widget=forms.PasswordInput(attrs={**AUTH_INPUT, 'placeholder': 'Nouveau mot de passe', 'autocomplete': 'new-password'}),
    )
    confirm_password = forms.CharField(
        widget=forms.PasswordInput(attrs={**AUTH_INPUT, 'placeholder': 'Confirmer le mot de passe', 'autocomplete': 'new-password'}),
    )

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get('new_password')
        password2 = cleaned_data.get('confirm_password')
        if password1 and password2 and password1 != password2:
            raise ValidationError("Les mots de passe ne correspondent pas.")
        return cleaned_data


class ResendActivationForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={**AUTH_INPUT, 'placeholder': 'Votre adresse email', 'autocomplete': 'email'}),
    )

    def clean_email(self):
        return (self.cleaned_data.get('email') or '').strip().lower()


class CustomUserForm(forms.ModelForm):
    username = forms.CharField(widget=forms.TextInput(attrs={"placeholder":"username...",'class':'form-control'}))
    email = forms.EmailField(widget=forms.EmailInput(attrs={"placeholder":"email@...",'class':'form-control'}))

    class Meta:
        model = CustomUser
        fields = ('username', 'email', 'contact', 'role', 'genre', 'local_entrepot')
        widgets = {
            'contact':        forms.TextInput(attrs={'class': 'form-control'}),
            'genre':          forms.Select(attrs={'class': 'form-control'}),
            'role':           forms.Select(attrs={'class': 'form-control'}),
            'local_entrepot': forms.Select(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Les comptes clients sont gérés séparément (page compte_client)
        self.fields['role'].choices = [
            (value, label) for value, label in self.fields['role'].choices
            if value != 'client'
        ]

    def clean(self):
        cleaned_data = super().clean()
        role = cleaned_data.get('role')
        local_entrepot = cleaned_data.get('local_entrepot')
        from .models import ROLES_AVEC_LOCAL
        if role in ROLES_AVEC_LOCAL and not local_entrepot:
            self.add_error(
                'local_entrepot',
                f"L'affectation à un entrepôt est obligatoire pour le rôle sélectionné."
            )
        return cleaned_data


class ClientCompteForm(forms.ModelForm):
    """Formulaire d'édition d'un compte client (rôle figé)."""

    class Meta:
        model = CustomUser
        fields = ('username', 'first_name', 'last_name', 'email', 'contact', 'genre')
        widgets = {
            'username': forms.TextInput(attrs={
                'class': 'form-control', 'placeholder': 'Nom utilisateur…',
            }),
            'first_name': forms.TextInput(attrs={
                'class': 'form-control', 'placeholder': 'Prénom…',
            }),
            'last_name': forms.TextInput(attrs={
                'class': 'form-control', 'placeholder': 'Nom…',
            }),
            'email': forms.EmailInput(attrs={
                'class': 'form-control', 'placeholder': 'email@…',
            }),
            'contact': forms.TextInput(attrs={
                'class': 'form-control', 'placeholder': 'Téléphone…',
            }),
            'genre': forms.Select(attrs={'class': 'form-control'}),
        }

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        qs = CustomUser.objects.filter(email__iexact=email)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError('Cette adresse e-mail est déjà utilisée.')
        return email

class CustomPermissionForm(forms.ModelForm):
    name = forms.CharField(
        widget=forms.TextInput(attrs={
            'placeholder': 'Nom de la permission...',
            'class': 'form-control'
        })
    )
    url = forms.CharField(
        widget=forms.TextInput(attrs={
            'placeholder': 'Nom de l\'URL (ex: add_panier)...',
            'class': 'form-control'
        })
    )
    categorie = forms.ModelChoiceField(
        queryset=TypeCustomPermission.objects.all(),
        widget=forms.Select(attrs={'class': 'form-control'}),
        empty_label='Sélectionner une catégorie'
    )
    
    class Meta:
        model = CustomPermission
        fields = ['name', 'categorie', 'url']

class TypeCustomPermissionForm(forms.ModelForm):
    categorie = forms.CharField(
        widget=forms.TextInput(attrs={
            'placeholder': 'Nom de la catégorie...',
            'class': 'form-control'
        })
    )
    
    class Meta:
        model = TypeCustomPermission
        fields = ['categorie']

class ImportPermissionsForm(forms.Form):
    excel_file = forms.FileField(
        label='Fichier Excel',
        widget=forms.FileInput(attrs={
            'class': 'form-control',
            'accept': '.xlsx,.xls'
        })
    )
    update_existing = forms.BooleanField(
        required=False,
        initial=False,
        label='Mettre à jour les permissions existantes',
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )

class UserPermissionForm(forms.Form):
    permissions = forms.ModelMultipleChoiceField(
        queryset=CustomPermission.objects.all(),
        widget=forms.CheckboxSelectMultiple,
        required=False
    )

#forms pour changer le mot de passe
class ChangePasswordForm(PasswordChangeForm):
    old_password = forms.CharField(
        label='Ancien mot de passe',
        widget=forms.PasswordInput(attrs={**PROFILE_INPUT, 'placeholder': 'Ancien mot de passe', 'autocomplete': 'current-password'}),
    )
    new_password1 = forms.CharField(
        label='Nouveau mot de passe',
        widget=forms.PasswordInput(attrs={**PROFILE_INPUT, 'placeholder': 'Nouveau mot de passe', 'autocomplete': 'new-password'}),
    )
    new_password2 = forms.CharField(
        label='Confirmer le mot de passe',
        widget=forms.PasswordInput(attrs={**PROFILE_INPUT, 'placeholder': 'Confirmez le nouveau mot de passe', 'autocomplete': 'new-password'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = field.widget.attrs.get('class', '')
            if 'profile-input' not in css:
                field.widget.attrs['class'] = f'{css} profile-input'.strip()
            field.widget.attrs.setdefault('autocomplete', 'off')


class ProfileSelfUserForm(forms.ModelForm):
    """Champs utilisateur modifiables par le titulaire du compte."""

    class Meta:
        model = CustomUser
        fields = ('first_name', 'last_name', 'email', 'contact', 'genre')
        labels = {
            'first_name': 'Prénom',
            'last_name': 'Nom',
            'email': 'E-mail',
            'contact': 'Téléphone',
            'genre': 'Genre',
        }
        widgets = {
            'first_name': forms.TextInput(attrs={**PROFILE_INPUT, 'placeholder': 'Prénom'}),
            'last_name': forms.TextInput(attrs={**PROFILE_INPUT, 'placeholder': 'Nom'}),
            'email': forms.EmailInput(attrs={**PROFILE_INPUT, 'placeholder': 'email@exemple.com', 'autocomplete': 'email'}),
            'contact': forms.TextInput(attrs={**PROFILE_INPUT, 'placeholder': 'Ex: 0700000000'}),
            'genre': forms.Select(attrs={**PROFILE_INPUT}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = field.widget.attrs.get('class', '')
            if 'profile-input' not in css:
                field.widget.attrs['class'] = f'{css} profile-input'.strip()

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        qs = CustomUser.objects.filter(email__iexact=email)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError('Cette adresse e-mail est déjà utilisée.')
        return email


class ProfileSelfProfilForm(forms.ModelForm):
    """Champs ProfilUser modifiables par le titulaire du compte."""

    class Meta:
        model = ProfilUser
        fields = (
            'photo',
            'date_naissance',
            'adresse',
            'ville',
            'code_postal',
            'pays',
            'poste',
            'bio',
        )
        labels = {
            'photo': 'Photo de profil',
            'date_naissance': 'Date de naissance',
            'adresse': 'Adresse',
            'ville': 'Ville',
            'code_postal': 'Code postal',
            'pays': 'Pays',
            'poste': 'Poste',
            'bio': 'Bio',
        }
        widgets = {
            'photo': forms.FileInput(attrs={
                'class': 'profile-input',
                'accept': 'image/jpeg,image/png,image/webp,image/gif',
            }),
            'date_naissance': forms.DateInput(
                format='%Y-%m-%d',
                attrs={**PROFILE_INPUT, 'type': 'date'},
            ),
            'adresse': forms.TextInput(attrs={**PROFILE_INPUT, 'placeholder': 'Adresse'}),
            'ville': forms.TextInput(attrs={**PROFILE_INPUT, 'placeholder': 'Ville'}),
            'code_postal': forms.TextInput(attrs={**PROFILE_INPUT, 'placeholder': 'Code postal'}),
            'pays': forms.TextInput(attrs={**PROFILE_INPUT, 'placeholder': "Côte d'Ivoire"}),
            'poste': forms.TextInput(attrs={**PROFILE_INPUT, 'placeholder': 'Poste'}),
            'bio': forms.Textarea(attrs={**PROFILE_TEXTAREA, 'placeholder': 'Présentez-vous brièvement…'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['date_naissance'].input_formats = ['%Y-%m-%d']
        for name, field in self.fields.items():
            css = field.widget.attrs.get('class', '')
            if 'profile-input' not in css:
                field.widget.attrs['class'] = f'{css} profile-input'.strip()


class LocalEntrepotForm(forms.ModelForm):
    class Meta:
        model = LocalEntrepot
        fields = ('nom', 'contact', 'latitude', 'longitude', 'statut')
        widgets = {
            'nom': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Nom de la localité',
                'autocomplete': 'off',
            }),
            'contact': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Ex: +225 07 00 00 00 00',
                'autocomplete': 'tel',
            }),
            'latitude': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': 'any',
                'placeholder': 'Latitude (ex: 5.345317)',
                'id': 'id_latitude',
            }),
            'longitude': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': 'any',
                'placeholder': 'Longitude (ex: -4.024429)',
                'id': 'id_longitude',
            }),
            'statut': forms.CheckboxInput(attrs={
                'class': 'form-check-input',
                'id': 'id_statut',
            }),
        }
        labels = {
            'contact': 'Contact',
            'statut': 'Ouvert',
        }


class CreneauDisponibiliteForm(forms.ModelForm):
    class Meta:
        model = CreneauDisponibilite
        fields = ('jour', 'heure_ouverture', 'heure_fermeture')
        widgets = {
            'jour': forms.Select(attrs={'class': 'form-control ecom-creneau-jour'}),
            'heure_ouverture': forms.TimeInput(
                format='%H:%M',
                attrs={'class': 'form-control', 'type': 'time'},
            ),
            'heure_fermeture': forms.TimeInput(
                format='%H:%M',
                attrs={'class': 'form-control', 'type': 'time'},
            ),
        }

    def clean(self):
        cleaned = super().clean()
        if self.cleaned_data.get('DELETE'):
            return cleaned
        ouverture = cleaned.get('heure_ouverture')
        fermeture = cleaned.get('heure_fermeture')
        if ouverture and fermeture and fermeture <= ouverture:
            self.add_error(
                'heure_fermeture',
                "L'heure de fermeture doit être après l'heure d'ouverture.",
            )
        return cleaned


CreneauDisponibiliteFormSet = forms.inlineformset_factory(
    LocalEntrepot,
    CreneauDisponibilite,
    form=CreneauDisponibiliteForm,
    extra=1,
    can_delete=True,
    min_num=0,
    validate_min=False,
)

