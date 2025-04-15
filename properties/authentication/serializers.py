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
    # language = serializers.CharField(write_only=True, required=False, default="en")
    phone_number = serializers.CharField(
        required=True,
        validators=[
            RegexValidator(
                regex=r'^\d{1,15}$',
                message='Phone number must be numeric and up to 15 digits.'
            )
        ]
    )

    class Meta:
        model = User
        fields = [
            "email",
            "password",
            "confirm_password",
            "first_name",
            "last_name",
            # "language",
            "phone_number",
        ]

    def validate(self, attrs):
        if attrs["password"] != attrs["confirm_password"]:
            raise serializers.ValidationError({"password": _("Passwords do not match")})
        validate_password(attrs["password"])
        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password")
        validated_data.pop("confirm_password", None)
        user = User.objects.create_user(password=password, **validated_data)
        with switch_language(user, "en"):
            user.first_name = validated_data.get("first_name", "")
            user.last_name = validated_data.get("last_name", "")
            user.save()
        return user

    def send_verification_email(self, user):
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        activation_link = f"{settings.FRONTEND_URL}/verify-email/{uid}/{token}/"
        subject = _("Verify your email address")
        body = _(f"Hi {user.first_name}, please verify your email by clicking the link below:\n{activation_link}")
        Email.send_email(
            subject=subject,
            message=body,   
            recipient_list=[user.email]
        )


class AgentCreateSerializer(serializers.ModelSerializer):
    first_name = serializers.CharField(write_only=True)
    last_name = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ["email", "first_name", "last_name", "phone_number"]

    def create(self, validated_data):
        landlord = self.context["request"].user
        if landlord.role != User.LANDLORD:
            raise serializers.ValidationError(_("Only landlords can create agents."))
        password = get_random_string(length=10)
        agent = User.objects.create_agent(
            landlord=landlord,
            email=validated_data["email"],
            password=password,
            phone_number=validated_data["phone_number"],
            first_name=validated_data["first_name"],
            last_name=validated_data["last_name"],
        )
        subject = _("Your Agent Account Has Been Created")
        body = f"""
            <p>Hi {agent.first_name},</p>
            <p>Your account has been created!</p>
            <p><strong>Email:</strong> {agent.email}</p>
            <p><strong>Password:</strong> {password}</p>
            <p><a href="{settings.FRONTEND_URL}/sign-in/" target="_blank">{_('Login Here')}</a></p>
        """
        Email(subject=subject).to(agent.email, name=agent.first_name).add_html(body).send()
        return agent


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)


class UserSerializer(serializers.ModelSerializer):
    first_name = serializers.CharField(required=False)
    last_name = serializers.CharField(required=False)
    role = serializers.CharField(read_only=True)
    language = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = User
        fields = ("id", "email", "role", "first_name", "last_name", "language", "created_by")

    def to_representation(self, instance):
        data = super().to_representation(instance)
        language = self.context.get("request").query_params.get("lang", "en")
        if language != "en" and instance.has_translation(language):
            data["first_name"] = translator.translate(instance.first_name, dest=language).text if instance.first_name else ""
            data["last_name"] = translator.translate(instance.last_name, dest=language).text if instance.last_name else ""
        return data

    def update(self, instance, validated_data):
        language = validated_data.pop("language", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if language:
            instance.set_current_language(language)
        return instance


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
    # language = serializers.CharField(write_only=True, required=False, default="en")
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
            "role",
            "first_name",
            "last_name",
            # "language",
            "phone_number",
        )
        extra_kwargs = {
            "email": {"required": True},
        }

    def create(self, validated_data):
        password = get_random_string(10)
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
            login_url = f"{settings.FRONTEND_URL}/sign-in"

        body = f"""
        <p>{greeting.format(name=user.first_name)}</p>
        <p>{content}</p>
        <p>{email_line.format(email=user.email)}</p>
        <p>{password_line.format(password=password)}</p>
        <a href="{login_url}"><button style="color:white;background-color:blue">{_('Click here to Login')}</button></a>
        """

        email = Email(subject=subject)
        email.to(user.email, name=user.first_name).add_html(body).send()
