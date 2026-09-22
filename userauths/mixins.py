# userauths/mixins.py
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.urls import resolve
from django.template.loader import render_to_string
from .permissions_utils import get_permission_name_from_url, user_has_permission


class CustomPermissionRequiredMixin:
    """
    Mixin pour vérifier les permissions personnalisées sur les vues basées sur les classes.
    Si permission_url n'est pas défini, il sera automatiquement détecté depuis l'URL.
    Ne refuse que si une CustomPermission existe pour cette URL (comme le middleware).
    """
    permission_url = None
    permission_required_message = "Désolé, accès interdit."

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        if not self.permission_url:
            permission_name = get_permission_name_from_url(request)
            if permission_name:
                self.permission_url = permission_name
            else:
                return super().dispatch(request, *args, **kwargs)

        from django.apps import apps
        CustomPermission = apps.get_model('userauths', 'CustomPermission')
        perm_exists = CustomPermission.objects.filter(url=self.permission_url).exists()

        if perm_exists and not user_has_permission(request.user, self.permission_url):
            return self.handle_no_permission_access_denied(request)

        return super().dispatch(request, *args, **kwargs)

    def handle_no_permission(self):
        """Redirige vers la page de connexion si l'utilisateur n'est pas authentifié"""
        return redirect('connexion')
    
    def handle_no_permission_access_denied(self, request):
        """Gère l'accès interdit avec un modal"""
        # Si c'est une requête AJAX, retourner du JSON avec le HTML du modal
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.GET.get('ajax') == '1':
            html = render_to_string('page/access_denied_modal.html', {
                'message': self.permission_required_message
            }, request=request)
            return JsonResponse({
                'success': False,
                'access_denied': True,
                'html': html,
                'message': self.permission_required_message
            }, status=403)
        
        # Sinon, rendre une page complète avec le modal
        from django.shortcuts import render
        return render(request, 'page/access_denied.html', {
            'message': self.permission_required_message
        }, status=403)
