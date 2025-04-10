import json, logging
import os
from django.utils.translation import get_language
from rest_framework import serializers
from parler_rest.serializers import TranslatableModelSerializer
from parler_rest.fields import TranslatedFieldsField
from parler.utils.context import switch_language

from .models import Property, PropertyImage
from utils.ses_validation import generate_ses_xml, send_validation_request
from utils.translation_services import translate_text

logger = logging.getLogger(__name__)

TRANSLATABLE_FIELDS = [
    "name", "address", "location", "description", "amenities"
]

class PropertyImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = PropertyImage
        fields = ["id", "image", "name", "uploaded_at"]

class PropertySerializer(TranslatableModelSerializer):
    translations = TranslatedFieldsField(shared_model=Property)
    webservice_username = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    webservice_password = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    establishment_code = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    landlord_code = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    
    images = PropertyImageSerializer(many=True, read_only=True)
    new_images = serializers.ListField(
        child=serializers.ImageField(max_length=1000000, allow_empty_file=False, use_url=False),
        write_only=True,
        required=False
    )

    class Meta:
        model = Property
        fields = [
            "id", "translations", "price", "owner", "created_at","property_type",
            "available", "rating", "ses_status",
            "country", "state", "city", "postal_code",
            "webservice_username", "webservice_password",
            "establishment_code", "landlord_code",
            "images", "new_images"
        ]
        read_only_fields = ["id", "owner", "created_at"]
    
    def to_internal_value(self, data):
        """
        Handle translations in various formats including string dictionary and form data
        """
        data_copy = data.copy()
        if 'translations' in data_copy and isinstance(data_copy['translations'], str):
            try:
                translations_value = data_copy['translations']
                if (translations_value.startswith('{') and translations_value.endswith('}')) or \
                   (translations_value.startswith('[') and translations_value.endswith(']')):
                    data_copy['translations'] = json.loads(translations_value)
            except json.JSONDecodeError:
                pass
                
        return super().to_internal_value(data_copy)
 
    def create(self, validated_data):
        """
        Create a new property with translations
        """
        translations = validated_data.pop('translations', {})
        images = validated_data.pop('image', {})
        property_instance = Property.objects.create(
            owner=self.context['request'].user,
            **validated_data
        )
        if translations:
            for lang_code, fields in translations.items():
                property_instance.set_current_language(lang_code)
                for field_name, value in fields.items():
                    setattr(property_instance, field_name, value)
                property_instance.save()
        for img in images:
            filename_without_ext = os.path.splitext(img.name)[0]
            PropertyImage.objects.create(
                property=property_instance, image=img, name=filename_without_ext
            )
        self._validate_ses(property_instance, validated_data)
        return property_instance
 
    def update(self, instance, validated_data):
        """
        Update a property with translations
        """
        translations = validated_data.pop('translations', {})
        images = validated_data.pop('image', {})
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if translations:
            for lang_code, fields in translations.items():
                instance.set_current_language(lang_code)
                for field_name, value in fields.items():
                    setattr(instance, field_name, value)
                
        instance.save()
        for img in images:
            filename_without_ext = os.path.splitext(img.name)[0]
            PropertyImage.objects.create(
                property=instance, image=img, name=filename_without_ext
            )
        self._validate_ses(instance, validated_data)

        return instance

    def _validate_ses(self, property_instance, validated_data):
        """Helper method to validate SES credentials"""
        ws_user = validated_data.get("webservice_username")
        ws_password = validated_data.get("webservice_password")
        est_code = validated_data.get("establishment_code")
        landlord_code = validated_data.get("landlord_code")

        if all([ws_user, ws_password, est_code, landlord_code]):
            try:
                xml_data = generate_ses_xml(
                    ws_user, ws_password, est_code, landlord_code
                )
                success, ses_response = send_validation_request(
                    xml_data, ws_user, ws_password, verify_ssl=False
                )
                property_instance.ses_status = success
                logger.info(f"SES Validation Result: {ses_response}")
            except Exception as e:
                logger.error(f"SES validation error: {e}")
                property_instance.ses_status = False

            property_instance.save()

    def to_representation(self, instance):
        """
        Customize the output representation
        """
        data = super().to_representation(instance)

        request = self.context.get("request")
        requested_lang = request.query_params.get("lang") or request.headers.get(
            "Accept-Language", "en"
        )
        base_lang = "en"
        if requested_lang in instance.get_available_languages():
            with switch_language(instance, requested_lang):
                return super().to_representation(instance)

        with switch_language(instance, base_lang):
            base_data = super().to_representation(instance)
        for field in TRANSLATABLE_FIELDS:
            value = base_data.get(field)
            if isinstance(value, str) and value.strip():
                try:
                    data[field] = translate_text(value, requested_lang)
                except Exception as e:
                    logger.warning(
                        f"Translation failed for field '{field}' to '{requested_lang}': {e}"
                    )
                    data[field] = value
        return data