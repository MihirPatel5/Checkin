import json, logging
import os
import random
import string
from django.utils.translation import get_language
from django.conf import settings
from payment.models import Upsell
from rest_framework import serializers
from parler_rest.serializers import TranslatableModelSerializer
from parler_rest.fields import TranslatedFieldsField
from parler.utils.context import switch_language

from .models import Property, PropertyImage, Activity
from utils.ses_validation import generate_ses_xml, send_validation_request
from utils.translation_services import translate_text

logger = logging.getLogger(__name__)

TRANSLATABLE_FIELDS = [
    "name", "address", "description"
]

def generate_unique_code(model, length=6):
        """Generate unique alphanumeric code for models"""
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))
        while model.objects.filter(code=code).exists():
            code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))
        return code
    
class PropertyImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = PropertyImage
        fields = ['id', 'image', 'name','uploaded_at']

class UpsellSerializer(serializers.ModelSerializer):
    class Meta:
        model = Upsell
        fields = ['id', 'name', 'description', 'price', 'currency', 'charge_type', 'image', 'is_active']
        
class ActivitySerializer(serializers.ModelSerializer):
    class Meta:
        model = Activity
        fields = ["id", "title", "description"]
        read_only_fields = ["id"]

class PropertySerializer(TranslatableModelSerializer):
    code = serializers.CharField(read_only=True)
    property_reference = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    cif_nif = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    google_maps_link = serializers.URLField(required=False, allow_null=True, allow_blank=True)
    max_guests = serializers.IntegerField(required=True, min_value=1)
    checkin_url = serializers.SerializerMethodField(read_only=True)

    translations = TranslatedFieldsField(shared_model=Property)
    webservice_username = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    webservice_password = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    establishment_code = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    landlord_code = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    wifi_name = serializers.CharField(required=False, allow_blank=False)
    wifi_pass = serializers.CharField(required=False, allow_blank=True)
    images = PropertyImageSerializer(many=True, read_only=True)
    image = serializers.ListField(
        child=serializers.ImageField(max_length=1000000, allow_empty_file=False, use_url=False),
        write_only=True,
        required=False
    )
    upsell_ids = serializers.ListField(
        child=serializers.IntegerField(), write_only=True, required=False
    )
    upsells = UpsellSerializer(many=True, read_only=True)
    activities = ActivitySerializer(many=True, read_only=True)
    activities_data = serializers.ListField(
        child=serializers.DictField(
            child=serializers.CharField()
        ),
        required=False,
        write_only=True,
        help_text="JSON string representing list of activity objects. Example: [{'title': 'Hiking', 'description': 'Mountain trail'}]"
    )

    class Meta:
        model = Property
        fields = [
            "id", "name","translations", "address", "owner", "created_at",
            "property_type", "available", "rating", "ses_status",
            "country", "state", "city", "postal_code", 'upsells',
            "webservice_username", "webservice_password",
            "establishment_code", "landlord_code", 'upsell_ids',
            "images", "image", "code", "max_guests", "checkin_url",
            "property_reference", "cif_nif", "google_maps_link",
            "wifi_name", "wifi_pass", "activities", "activities_data",
        ]
        read_only_fields = ["id", "owner", "created_at"]
        extra_kwargs = {"activities_data": {"write_only": True}}
    
    def to_internal_value(self, data):
        """
        Handle activities_data in various formats
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
        if 'activities_data' in data_copy:
            if isinstance(data_copy['activities_data'], str):
                try:
                    data_copy['activities_data'] = json.loads(data_copy['activities_data'])
                except json.JSONDecodeError:
                    data_copy['activities_data'] = []
            elif isinstance(data_copy['activities_data'], list):
                pass
            elif isinstance(data_copy['activities_data'], dict):
                activities_list = []
                try:
                    for index in sorted([int(k) for k in data_copy['activities_data'].keys() if k.isdigit()]):
                        activity_data = data_copy['activities_data'][str(index)]
                        if isinstance(activity_data, dict):
                            activities_list.append(activity_data)
                    data_copy['activities_data'] = activities_list
                except (ValueError, KeyError):
                    data_copy['activities_data'] = []
        return super().to_internal_value(data_copy)
    
    def get_checkin_url(self, obj):
        return f"{settings.FRONTEND_URL}/property/{obj.code}"
    
    def create(self, validated_data):
        """
        Create a new property with translations
        """
        activities_data = validated_data.pop("activities_data", [])
        translations = validated_data.pop('translations', {})
        images = validated_data.pop('image', {})
        validated_data['code']= generate_unique_code(Property)
        upsell_ids = validated_data.pop('upsell_ids', [])
        property_instance = Property.objects.create(
            owner=self.context['request'].user,
            **validated_data
        )
        if isinstance(upsell_ids, str):
            try:
                upsell_ids = json.loads(upsell_ids)
            except json.JSONDecodeError:
                upsell_ids = []
        if upsell_ids:
            upsells = Upsell.objects.filter(id__in=upsell_ids)
            property_instance.upsells.set(upsells)
        if translations:
            for lang_code, fields in translations.items():
                property_instance.set_current_language(lang_code)
                for field_name, value in fields.items():
                    setattr(property_instance, field_name, value)
                property_instance.save()
        if activities_data and isinstance(activities_data, list):
            for activity in activities_data:
                if isinstance(activity, dict) and 'title' in activity:
                    Activity.objects.create(
                        property=property_instance,
                        title=activity.get('title', ''),
                        description=activity.get('description', '')
                    )
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
                xml_data = generate_ses_xml(property_instance)
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
        activities_data = validated_data.pop("activities_data", None)
        translations = validated_data.pop('translations', {})
        images = validated_data.pop('image', [])
        request = self.context.get('request')
        upsell_ids = validated_data.pop('upsell_ids', None)
        image_ids_to_keep = request.data.get('image_ids', []) if request else []
        if upsell_ids is not None:
            if isinstance(upsell_ids, str):
                try:
                    upsell_ids = json.loads(upsell_ids)
                except json.JSONDecodeError:
                    upsell_ids = []
            upsells = Upsell.objects.filter(id__in=upsell_ids)
            instance.upsells.set(upsells)
        
        if isinstance(image_ids_to_keep, str):
            try:
                image_ids_to_keep = json.loads(image_ids_to_keep)
            except json.JSONDecodeError:
                image_ids_to_keep = []
        existing_image_ids = list(instance.images.values_list('id', flat=True))
        image_ids_to_delete = [img_id for img_id in existing_image_ids if img_id not in image_ids_to_keep]
        PropertyImage.objects.filter(id__in=image_ids_to_delete, property=instance).delete()
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
        if activities_data is not None:
            instance.activities.all().delete()
            for act in activities_data:
                if isinstance(act, dict) and 'title' in act:
                    Activity.objects.create(
                        property=instance,
                        title=act.get('title', ''),
                        description=act.get('description', '')
                    )
        for img in images:
            filename = os.path.splitext(img.name)[0]
            PropertyImage.objects.create(
                property=instance, 
                image=img, 
                name=filename
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
            "description": instance.description
            }
        data["translations"] = translations
        return data