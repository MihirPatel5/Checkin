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
    name = serializers.CharField(write_only=True)
    description = serializers.CharField(write_only=True)
    address = serializers.CharField(write_only=True)
    amenities = serializers.CharField(write_only=True)
    property_type = serializers.CharField(write_only=True)
    webservice_username = serializers.CharField(required=False, allow_blank=True)
    webservice_password = serializers.CharField(required=False, allow_blank=True)
    establishment_code = serializers.CharField(required=False, allow_blank=True)
    landlord_code = serializers.CharField(required=False, allow_blank=True)

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
            "name",
            "description",
            "address",
            "amenities",
            "property_type",
            "webservice_username",
            "webservice_password",
            "establishment_code",
            "landlord_code",
            "ses_status"
        ]
        read_only_fields = ["owner", "created_at", "ses_status"]

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
            "property_type": obj.safe_translation_getter("property_type", language_code=language) or "",
            "property_type_display": dict(Property.PROPERTY_TYPES).get(
                obj.safe_translation_getter("property_type", language_code=language), ""
            ),
        }

    def create(self, validated_data):
        """
        Ensure property is created with proper translations
        """
        name = validated_data.pop("name", "")
        description = validated_data.pop("description", "")
        address = validated_data.pop("address", "")
        amenities = validated_data.pop("amenities", "")
        property_type = validated_data.pop("property_type", "")
        webservice_username = validated_data.pop("webservice_username", None)
        webservice_password = validated_data.pop("webservice_password", None)
        establishment_code = validated_data.pop("establishment_code", None)
        landlord_code = validated_data.pop("landlord_code", None)
        request = self.context.get("request")
        language = (request.GET.get("language") if request else None) or (
            request.user.preferred_language
            if request and hasattr(request.user, "preferred_language")
            else get_language()
        )
        property_instance = Property()
        property_instance.price = validated_data.get("price")
        property_instance.available = validated_data.get("available", True)
        property_instance.rating = validated_data.get("rating", 0.0)
        property_instance.owner = validated_data.get("owner")
        if webservice_username:
            property_instance.webservice_username = webservice_username
        if webservice_password:
            property_instance.webservice_password = webservice_password
        if establishment_code:
            property_instance.establishment_code = establishment_code
        if landlord_code:
            property_instance.landlord_code = landlord_code
        property_instance.set_current_language(language)
        property_instance.name = name
        property_instance.description = description
        property_instance.address = address
        property_instance.amenities = amenities
        property_instance.property_type = property_type
        property_instance.save()
        return property_instance

    def update(self, instance, validated_data):
        """
        Update property with proper translations
        """
        name = validated_data.pop("name", None)
        description = validated_data.pop("description", None)
        address = validated_data.pop("address", None)
        amenities = validated_data.pop("amenities", None)
        property_type = validated_data.pop("property_type", None)
        webservice_username = validated_data.pop("webservice_username", None)
        webservice_password = validated_data.pop("webservice_password", None)
        establishment_code = validated_data.pop("establishment_code", None)
        landlord_code = validated_data.pop("landlord_code", None)
        request = self.context.get("request")
        language = (request.GET.get("language") if request else None) or (
            request.user.preferred_language
            if request and hasattr(request.user, "preferred_language")
            else get_language()
        )
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if webservice_username:
            instance.webservice_username = webservice_username
        if webservice_password:
            instance.webservice_password = webservice_password
        if establishment_code:
            instance.establishment_code = establishment_code
        if landlord_code:
            instance.landlord_code = landlord_code
        instance.set_current_language(language)
        if name:
            instance.name = name
        if description:
            instance.description = description
        if address:
            instance.address = address
        if amenities:
            instance.amenities = amenities
        if property_type:
            instance.property_type = property_type
        instance.save()
        return instance