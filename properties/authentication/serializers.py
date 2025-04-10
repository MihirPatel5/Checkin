import asyncio
from gettext import translation
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken
from django.utils.http import urlsafe_base64_decode
from django.utils.crypto import get_random_string
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.utils import translation
from django.core.validators import RegexValidator
from django.utils.translation import gettext as _
from django.conf import settings
from parler.utils.context import switch_language
from googletrans import Translator

from utils.email_services import Email
from .models import User

translator = Translator()

class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True)
    first_name = serializers.CharField(required=True)
    last_name = serializers.CharField(required=True)
    language = serializers.CharField(write_only=True, required=False, default="en")
    phone_number = serializers.CharField(
        required=True,
        validators=[
            RegexValidator(
                regex=r'^\d{1,15}$',
                message='Phone number must be numeric and up to 15 digits.',
            )
        ],
        error_messages={"required": "Phone number is required."},
    )

    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "password",
            "confirm_password",
            "role",
            "first_name",
            "last_name",
            "language",
            "phone_number",
        )
        extra_kwargs = {
            "email": {"required": True},
        }

    def translate_text(self, text, dest_language):
        """Handles async translation inside a synchronous function."""
        translator = Translator()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        translated_text = loop.run_until_complete(translator.translate(text, dest=dest_language))
        return translated_text.text

    def create(self, validated_data):
        password = validated_data.pop("password")
        validated_data.pop("confirm_password")
        first_name = validated_data.get("first_name")
        last_name = validated_data.get("last_name")
        language = validated_data.pop("language", "en")
        phone_number = validated_data.pop("phone_number")
        role = validated_data.get("role", "Guest")
        if language != "en":
            first_name_translated = self.translate_text(first_name, language)
            last_name_translated = self.translate_text(last_name, language)
            role_translated = self.translate_text(role, language)
        else:
            first_name_translated = first_name
            last_name_translated = last_name
            role_translated =role

        user = User.objects.create(
            email=validated_data["email"],
            username=validated_data["email"].split("@")[0],
            role=role,
            first_name=first_name_translated,
            last_name=last_name_translated,
            phone_number=phone_number,
            is_active=False,
        )
        user.set_password(password)
        user.save()
        user.set_current_language(language)
        user.first_name = first_name_translated
        user.last_name = last_name_translated
        user.save()
        with translation.override(language):
            user.first_name = first_name_translated
            user.last_name = last_name_translated
            user.save()
        self.context["language"] = language
        self.send_verification_email(user, language)
        return user

    def send_verification_email(self, user, language):
        token = default_token_generator.make_token(user)
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        verification_link = f"{settings.FRONTEND_URL}/verify-email/{uid}/{token}"

        with translation.override(language):
            email_subject = _("Registration Verification")
            greeting = _("Hello {name}")
            click_message = _("Click the link below to verify your email:")
            ignore_message = _("If you didn't request this, please ignore this email.")
            user.set_current_language(language)
            user_name = user.first_name

        email_body = f"""
        <p>{greeting.format(name=user_name)},</p>
        <p>{click_message}</p>
        <a href="{verification_link}">{verification_link}</a>
        <p>{ignore_message}</p>
        """

        email = Email(subject=email_subject)
        email.to(user.email, name=user_name).add_html(email_body).send()


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)


class UserSerializer(serializers.ModelSerializer):
    first_name = serializers.CharField(required=False)
    last_name = serializers.CharField(required=False)
    role = serializers.CharField(required=False)
    language = serializers.CharField(required=False, write_only=True)

    class Meta:
        model = User
        fields = ("id", "email", "role", "first_name", "last_name", "language")

    def to_representation(self, instance):
        language = self.get_language_from_context()
        translated_data = super().to_representation(instance)
        try:
            if language != "en" and instance.has_translation(language):
                translated_data["first_name"] = translator.translate(instance.first_name, dest=language).text if instance.first_name else ""
                translated_data["last_name"] = translator.translate(instance.last_name, dest=language).text if instance.last_name else ""
                translated_data["role"] = translator.translate(instance.role, dest=language).text if instance.role else ""
        except Exception as e:
            print(f"Translation error: {e}")
            pass
        return translated_data

    def get_language_from_context(self):
        request = self.context.get("request")
        language_sources = [
            self.context.get("language"),
            request.query_params.get("language") if request else None,
            request.data.get("language") if request else None,
            (
                request.headers.get("Accept-Language", "").split(",")[0]
                if request
                else None
            ),
            "en",
        ]
        for lang in language_sources:
            if lang and lang in dict(settings.LANGUAGES):
                return lang
        return "en"

    def update(self, instance, validated_data):
        language = validated_data.pop("language", self.get_language_from_context())
        first_name = validated_data.pop("first_name", None)
        last_name = validated_data.pop("last_name", None)
        if first_name is not None or last_name is not None:
            if not instance.has_translation(language):
                translation_data = {
                    "first_name": first_name or instance.first_name,
                    "last_name": last_name or instance.last_name,
                }
                instance.create_translation(language_code=language, **translation_data)
            else:
                with translation.override(language):
                    if first_name is not None:
                        instance.first_name = first_name
                    if last_name is not None:
                        instance.last_name = last_name
                    instance.save()
        return super().update(instance, validated_data)


class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        try:
            user = User.objects.get(email=value)
            self.context["user"] = user
        except User.DoesNotExist:
            raise serializers.ValidationError("No user found with this email.")
        return value


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(
        write_only=True, validators=[validate_password]
    )
    confirm_password = serializers.CharField(
        write_only=True, validators=[validate_password]
    )

    def validate(self, data):
        try:
            uid = urlsafe_base64_decode(data["uid"]).decode()
            user = User.objects.get(pk=uid)
        except (User.DoesNotExist, ValueError):
            raise serializers.ValidationError("Invalid reset link.")

        if not default_token_generator.check_token(user, data["token"]):
            raise serializers.ValidationError("Reset link has expired or is invalid.")
        if data["new_password"] != data["confirm_password"]:
            raise serializers.ValidationError(
                {"password": "Password fields didn't match."}
            )
        self.context["user"] = user
        return data


class PasswordResetSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(
        write_only=True, validators=[validate_password]
    )

    def validate(self, data):
        user = self.context["request"].user
        if not user.check_password(data["old_password"]):
            raise serializers.ValidationError(
                {"old_password": "Incorrect old password."}
            )
        return data

class AdminRegisterUserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, min_length=8)
    first_name = serializers.CharField(required=True)
    last_name = serializers.CharField(required=True)
    language = serializers.CharField(write_only=True, required=False, default="en")
    phone_number = serializers.CharField(
        required=True,
        validators=[
            RegexValidator(
                regex=r'^\d{1,15}$',
                message='Phone number must be numeric and up to 15 digits.',
            )
        ],
        error_messages={"required": "Phone number is required."},
    )

    class Meta:
        model = User
        fields = (
            "email",
            "password",
            "role",
            "first_name",
            "last_name",
            "language",
            "phone_number",
        )
        extra_kwargs = {
            "email": {"required": True},
        }

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        if not password:
            password = get_random_string(10)  # Auto-generate password if not provided

        language = validated_data.pop("language", "en")
        first_name = validated_data.get("first_name")
        last_name = validated_data.get("last_name")
        role = validated_data.get("role", "Guest")
        phone_number = validated_data.pop("phone_number")

        user = User.objects.create(
            email=validated_data["email"],
            username=validated_data["email"].split("@")[0],
            role=role,
            first_name=first_name,
            last_name=last_name,
            phone_number=phone_number,
            is_active=True,
        )
        user.set_password(password)
        user.save()

        self.send_credentials_email(user, password, language)
        return user

    def send_credentials_email(self, user, password, language):
        with translation.override(language):
            subject = _("Your account has been created")
            greeting = _("Hello {name},")
            content = _("Your login credentials are below:")
            email_line = _("Email: {email}")
            password_line = _("Password: {password}")
            login_url = f"{settings.FRONTEND_URL}/login/"

        body = f"""
        <p>{greeting.format(name=user.first_name)}</p>
        <p>{content}</p>
        <p>{email_line.format(email=user.email)}</p>
        <p>{password_line.format(password=password)}</p>
        <p><a href="{login_url}">{_('Login Here')}</a></p>
        """

        email = Email(subject=subject)
        email.to(user.email, name=user.first_name).add_html(body).send()
