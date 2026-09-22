"""
Décorateurs pour la gestion des permissions personnalisées
"""
from functools import wraps
from django.http import JsonResponse, HttpResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import resolve
from .permissions_utils import get_permission_name_from_url, user_has_permission


def superuser_required(view_func):
    """Accès réservé aux superutilisateurs Django."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('connexion')
        if not request.user.is_superuser:
            message = 'Accès réservé aux administrateurs.'
            is_ajax = (
                request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                or request.GET.get('ajax') == '1'
            )
            if is_ajax:
                html = render_to_string(
                    'page/access_denied_modal.html',
                    {'message': message},
                    request=request,
                )
                return JsonResponse({
                    'success': False,
                    'access_denied': True,
                    'html': html,
                    'message': message,
                }, status=403)
            return render(
                request,
                'page/access_denied.html',
                {'message': message},
                status=403,
            )
        return view_func(request, *args, **kwargs)
    return wrapper


def custom_permission_required(permission_url=None):
    """
    Décorateur pour vérifier les permissions personnalisées sur les vues basées sur les fonctions.
    
    Usage:
        @custom_permission_required('nom_de_l_url')
        def ma_vue(request):
            ...
    
    Si permission_url n'est pas fourni, il sera automatiquement détecté depuis l'URL.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            # Vérifier l'authentification
            if not request.user.is_authenticated:
                from django.shortcuts import redirect
                return redirect('connexion')
            
            # Détecter le nom de permission depuis l'URL si non fourni
            perm_url = permission_url
            if not perm_url:
                perm_url = get_permission_name_from_url(request)
            
            # Si on a un nom de permission, vérifier
            if perm_url:
                from django.apps import apps
                CustomPermission = apps.get_model('userauths', 'CustomPermission')
                perm_exists = CustomPermission.objects.filter(url=perm_url).exists()
                if perm_exists and not user_has_permission(request.user, perm_url):
                    message = 'Désolé, accès interdit.'
                    if perm_url == 'tbord':
                        message = (
                            "Vous n'avez pas la permission d'accéder au tableau de bord."
                        )
                    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.GET.get('ajax') == '1':
                        html = render_to_string('page/access_denied_modal.html', {
                            'message': message
                        }, request=request)
                        return JsonResponse({
                            'success': False,
                            'access_denied': True,
                            'html': html,
                            'message': message
                        }, status=403)

                    return render(request, 'page/access_denied.html', {
                        'message': message
                    }, status=403)
            
            # Si tout est OK, exécuter la vue
            return view_func(request, *args, **kwargs)
        
        return wrapper
    return decorator

