from django.conf import settings

class TranslationMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        lang = request.headers.get('Accept-Language', settings.LANGUAGE_CODE)
        if request.user.is_authenticated:
            lang = request.user.preferred_language or lang
        request.LANGUAGE_CODE = lang[:2]
        response = self.get_response(request)
        response['Content-Language'] = request.LANGUAGE_CODE
        return response