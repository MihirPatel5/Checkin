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
    "name", "address", "description", "amenities"
]

class PropertyImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = PropertyImage
        fields = ['id', 'image', 'name','uploaded_at']

class PropertySerializer(TranslatableModelSerializer):
    translations = TranslatedFieldsField(shared_model=Property)
    webservice_username = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    webservice_password = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    establishment_code = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    landlord_code = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    
    images = PropertyImageSerializer(many=True, read_only=True)
    image = serializers.ListField(
        child=serializers.ImageField(max_length=1000000, allow_empty_file=False, use_url=False),
        write_only=True,
        required=False
    )

    class Meta:
        model = Property
        fields = [
            "id", "name","translations", "address", "price", "owner", "created_at",
            "property_type", "available", "rating", "ses_status",
            "country", "state", "city", "postal_code",
            "webservice_username", "webservice_password",
            "establishment_code", "landlord_code",
            "images", "image"
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
            filename = os.path.splitext(img.name)[0]
            PropertyImage.objects.create(property=property_instance, image=img, name=filename)
        ws_user = validated_data.get("webservice_username")
        ws_password = validated_data.get("webservice_password")
        est_code = validated_data.get("establishment_code")
        landlord_code = validated_data.get("landlord_code")

        property_instance.ses_status = False
        
        if all([ws_user, ws_password, est_code, landlord_code]):
            try:
                xml_data = generate_ses_xml(est_code)
                success, ses_response = send_validation_request(xml_data, ws_user, ws_password, landlord_code)
                property_instance.ses_status = success
                if not success:
                    logger.warning(f"SES validation failed with message: {ses_response}")
            except Exception as e:
                error_msg = str(e)
                logger.error(f"SES validation error for property {property_instance.id}: {error_msg}")
                property_instance.ses_status = False

        property_instance.save()
        return property_instance
 
    def update(self, instance, validated_data):
        """
        Update a property with translations
        """
        translations = validated_data.pop('translations', {})
        images = validated_data.pop('image', {})
        sensitive_fields = [
        "webservice_username",
        "webservice_password",
        "establishment_code",
        "landlord_code"
    ]
        for field in sensitive_fields:
            if field in validated_data:
                setattr(instance, field, validated_data[field])
                validated_data.pop(field)
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
                print('ses_response: ', ses_response)
            except Exception as e:
                logger.error(f"SES validation error: {e}")
                property_instance.ses_status = False

            property_instance.save()

    def to_representation(self, instance):
        """
        Customize the output representation
        """
        data = super().to_representation(instance)
        data.pop("webservice_username", None)
        data.pop("webservice_password", None)
        data.pop("establishment_code", None)
        data.pop("landlord_code", None)
        data["name"] = instance.name
        data["address"] = instance.address
        # data["location"] = instance.location
        translations = {}
        for lang_code in instance.get_available_languages():
            instance.set_current_language(lang_code)
            translations[lang_code] = {
            "description": instance.description,
            "amenities": instance.amenities,
            }
        data["translations"] = translations
        return data