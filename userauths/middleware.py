"""
Middleware pour vérifier automatiquement les permissions personnalisées.
Périmètre : /stocks/ + URLs userauths comptes/permissions/localités.
Exempt : auth publique, profil/mdp, ecom, api, admin, static/media.
"""
from django.apps import apps
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string

from .permissions_utils import (
    get_permission_name_from_url,
    is_permission_check_exempt,
    should_enforce_custom_permission,
    user_has_permission,
)


class CustomPermissionMiddleware:
    """
    Magasin /stocks/ et comptes staff : connexion obligatoire,
    puis CustomPermission pour les utilisateurs authentifiés.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        url_name = get_permission_name_from_url(request)

        if not getattr(request, 'user', None) or not request.user.is_authenticated:
            if should_enforce_custom_permission(request, url_name):
                return redirect('connexion')
            return self.get_response(request)

        if request.user.is_superuser:
            return self.get_response(request)

        if is_permission_check_exempt(request, url_name):
            return self.get_response(request)

        # Clients ecom : hors périmètre protégé → laisser passer
        role = getattr(request.user, 'role', None)
        if role == 'client' and not should_enforce_custom_permission(request, url_name):
            return self.get_response(request)

        if not should_enforce_custom_permission(request, url_name):
            return self.get_response(request)

        if not url_name:
            return self.get_response(request)

        try:
            CustomPermission = apps.get_model('userauths', 'CustomPermission')
            perm_exists = CustomPermission.objects.filter(url=url_name).exists()
            if perm_exists and not user_has_permission(request.user, url_name):
                message = 'Désolé, accès interdit.'
                if url_name == 'tbord':
                    message = (
                        "Vous n'avez pas la permission d'accéder au tableau de bord."
                    )
                return self.handle_access_denied(request, message=message)
        except Exception:
            pass

        return self.get_response(request)

    def handle_access_denied(self, request, message=None):
        """Gère l'accès interdit (AJAX → JSON modal, sinon page 403)."""
        msg = message or 'Désolé, accès interdit.'
        if (
            request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            or request.GET.get('ajax') == '1'
        ):
            html = render_to_string(
                'page/access_denied_modal.html',
                {'message': msg},
                request=request,
            )
            return JsonResponse(
                {
                    'success': False,
                    'access_denied': True,
                    'html': html,
                    'message': msg,
                },
                status=403,
            )

        return render(
            request,
            'page/access_denied.html',
            {'message': msg},
            status=403,
        )
