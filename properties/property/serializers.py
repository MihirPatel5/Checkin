from django.utils.translation import activate, get_language
from django.conf import settings
from rest_framework import serializers
from .models import Property

class PropertySerializer(serializers.Model):
    translations = serializers.SerializerMethodField()

    class Meta:
        model = Property
        fields = [
            "id",
            "translations",
            "price",
            "owner",
            "created_at",
            "available",
            "rating",
        ]
    
    def get_translations(self, obj):
        """ Return translations based on user's selected languages"""
        request = self.context.get("request")
        language = request.GET.get("language") if request else None

        if language and language in dict(settings.LANGUAGES):
            activate(language)
        else:
            language = get_language()
        return {
            "name": obj.safe_translation_getter("name", language_code=language),
            "description": obj.safe_translation_getter("description", language_code=language),
            "address": obj.safe_translation_getter("address", language_code=language),
            "amenities": obj.safe_translation_getter("amenities", language_code=language),
        }        