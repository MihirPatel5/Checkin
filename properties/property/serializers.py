from django.utils.translation import activate, get_language
from django.conf import settings
from rest_framework import serializers
from .models import Property, PropertyImage


class PropertyImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = PropertyImage
        fields = ["id", "image"]


class PropertySerializer(serializers.ModelSerializer):
    translations = serializers.SerializerMethodField()
    images = PropertyImageSerializer(many=True, read_only=True)

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
            "images",
        ]
        read_only_fields = ["owner"]

    def get_translations(self, obj):
        """
        Return translations based on user's selected language
        or default language
        """
        request = self.context.get("request")
        language = (request.GET.get("language") if request else None) or (
            request.user.preferred_language
            if request and hasattr(request.user, "preferred_language")
            else get_language()
        )
        if language not in dict(settings.LANGUAGES):
            language = settings.LANGUAGE_CODE
        activate(language)

        return {
            "name": obj.safe_translation_getter("name", language_code=language) or "",
            "description": obj.safe_translation_getter(
                "description", language_code=language
            )
            or "",
            "address": obj.safe_translation_getter("address", language_code=language)
            or "",
            "amenities": obj.safe_translation_getter(
                "amenities", language_code=language
            )
            or "",
            "property_type": dict(Property.PROPERTY_TYPES).get(
                obj.safe_translation_getter("property_type", language_code=language), ""
            ),
        }

    def create(self, validated_data):
        """
        Ensure property is created with proper translations
        """
        request = self.context.get("request")
        language = (request.GET.get("language") if request else None) or (
            request.user.preferred_language
            if request and hasattr(request.user, "preferred_language")
            else get_language()
        )
        property_instance = Property.objects.create(
            owner=request.user,
            price=validated_data.get("price"),
            available=validated_data.get("available", True),
            rating=validated_data.get("rating", 0.0),
        )
        property_instance.set_current_language(language)
        property_instance.name = validated_data.get("name", "")
        property_instance.description = validated_data.get("description", "")
        property_instance.address = validated_data.get("address", "")
        property_instance.amenities = validated_data.get("amenities", "")
        property_instance.property_type = validated_data.get("property_type", "")

        property_instance.save()
        return property_instance
