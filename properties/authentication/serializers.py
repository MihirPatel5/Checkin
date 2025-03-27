from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken
from django.utils.http import urlsafe_base64_decode
from django.contrib.auth import get_user_model
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.conf import settings

from utils.email_services import Email


User = get_user_model()


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True)

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
        )
        extra_kwargs = {
            "email": {"required": True},
            "first_name": {"required": True},
            "last_name": {"required": True},
        }

    def validate(self, attrs):
        if attrs["password"] != attrs["confirm_password"]:
            raise serializers.ValidationError(
                {"password": "Password fields didn't match."}
            )

        return attrs

    def create(self, validated_data):
        validated_data.pop("confirm_password")
        user = User.objects.create(
            username=validated_data["email"].split("@")[0],
            email=validated_data["email"],
            first_name=validated_data["first_name"],
            last_name=validated_data["last_name"],
            role=validated_data.get("role", "Guest"),
        )
        user.set_password(validated_data["password"])
        user.is_active = False
        user.save()
        self.send_verification_email(user)
        return user

    def send_verification_email(self, user):
        token = default_token_generator.make_token(user)
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        verification_link = (
            f"{settings.FRONTEND_URL}/verify-email/{uid}/{token}"  # frontend URL
        )

        email_body = f"""
        <p>Hello {user.first_name},</p>
        <p>Click the link below to verify your email:</p>
        <a href="{verification_link}">{verification_link}</a>
        <p>If you didn’t request this, please ignore this email.</p>
        """

        email = Email(subject="Registration Verification")
        email.to(user.email, name=user.first_name).add_html(email_body).send()


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "email", "role", "first_name", "last_name")


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

    def validate(self, data):
        try:
            uid = urlsafe_base64_decode(data["uid"]).decode()
            user = User.objects.get(pk=uid)
        except (User.DoesNotExist, ValueError):
            raise serializers.ValidationError("Invalid reset link.")

        if not default_token_generator.check_token(user, data["token"]):
            raise serializers.ValidationError("Reset link has expired or is invalid.")

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