import re
from django.contrib.auth import authenticate
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes
from django.contrib.auth.tokens import default_token_generator
from django.conf import settings
from django.utils.crypto import get_random_string
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from rest_framework import status, generics, permissions, filters
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.permissions import IsAuthenticated, AllowAny
from parler.utils.context import switch_language

from .serializers import (
    RegisterSerializer,
    LoginSerializer,
    UserSerializer,
    PasswordResetSerializer,
    ForgotPasswordSerializer,
    PasswordResetConfirmSerializer,
    AdminRegisterUserSerializer,
    AgentCreateSerializer,
)
from utils.email_services import Email
from utils.translation_services import TranslateService
from .models import LandlordAgentRelationship, User
from .permissions import CanViewUser, CanEditUser, CanDeleteUser, IsSuperAdmin, IsSuperOrAdmin
from authentication import models


translator = TranslateService()


def handle_response(data, status, request=None):

    def is_email(value):
        return isinstance(value, str) and re.match(r"^[^@]+@[^@]+\.[^@]+$", value)

    def should_translate(key, value):
        """Translate only if it's a string, not an email, first name, or last name"""
        excluded_keys = {"first_name", "last_name"}
        return (
            isinstance(value, str) and not is_email(value) and key not in excluded_keys
        )

    def translate_value(key, value, target_language):
        return (
            translator.translate(value, target_language)
            if should_translate(key, value)
            else value
        )

    def process_data(data, target_language):
        """Recursively process for translate dictionaries and lists"""
        if isinstance(data, dict):
            return {
                key: translate_value(key, value, target_language)
                for key, value in data.items()
            }
        elif isinstance(data, list):
            return [process_data(item, target_language) for item in data]
        return data

    target_language = request.data.get("language", "es") if request else "es"
    translated_data = process_data(data, target_language)

    return Response(translated_data, status=status)


class VerifyEmailView(APIView):
    """Handles email verification via a unique token"""

    def get(self, request, uidb64, token):
        try:
            uid = urlsafe_base64_decode(uidb64).decode()
            user = User.objects.get(pk=uid)

            if user.is_active:
                return handle_response(
                    {"message": "Email already verified"},
                    status.HTTP_400_BAD_REQUEST, request
                )

            if default_token_generator.check_token(user, token):
                user.is_active = True
                user.save()
                return Response(
                    {"message": "Email verified successfully"},
                    status.HTTP_200_OK
                )

            return handle_response(
                {"error": "Invalid or expired token"},
                status.HTTP_400_BAD_REQUEST, request
            )

        except (User.DoesNotExist, ValueError, TypeError):
            return handle_response(
                {"error": "Invalid request"}, status.HTTP_400_BAD_REQUEST, request
            )


class RegistrationView(generics.CreateAPIView):
    """Handle User Registration based on Role and account verification after creation."""

    queryset = User.objects.all()
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]

    def get_serializer_context(self):
        """Pass the request object to the serializer."""
        return {"request": self.request}


class LoginView(APIView):
    """
    Handle User Login and authentication using email/password and
    return access token and refresh token after success.
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        try:
            serializer = LoginSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            email = serializer.validated_data["email"]
            password = serializer.validated_data["password"]
            user = authenticate(request, email=email, password=password)
            if not user:
                return handle_response(
                    {"error": "Invalid Credentials"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            if not user.is_active:
                return handle_response(
                    {"error": "Your account is not activated. Please verify your email."},
                    status=status.HTTP_403_FORBIDDEN
                )
            with switch_language(user, 'en'):
                refresh = RefreshToken.for_user(user)
                user_data = UserSerializer(user, context={"request": request}).data
            return Response(
                {
                    "refresh": str(refresh),
                    "access": str(refresh.access_token),
                    "user": user_data,
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            return handle_response(
                {"error": f"Login Invalid. {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )


class LogoutView(APIView):
    """Handle Logout functionality and blacklisted tokens"""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            refresh_token = request.data.get("refresh")
            if not refresh_token:
                return handle_response(
                    {"error": "Refresh token is required"},
                    status.HTTP_400_BAD_REQUEST, request
                )

            token = RefreshToken(refresh_token)
            token.blacklist()
            return handle_response(
                {"message": "Logout successful"}, status.HTTP_205_RESET_CONTENT, request
            )

        except Exception:
            return handle_response(
                {"error": "Invalid or expired token"},
                status.HTTP_400_BAD_REQUEST, request
            )


class ForgotPasswordView(APIView):
    """Handle Forgot passsword and set new password usign link send via Email."""

    def post(self, request):
        serializer = ForgotPasswordSerializer(
            data=request.data, context={"request": request}
        )
        if serializer.is_valid():
            user = serializer.context["user"]
            self.send_reset_email(user)
            return handle_response(
                {"message": "Password reset link sent to your email."},
                status.HTTP_200_OK, request
            )
        return handle_response(serializer.errors, status.HTTP_400_BAD_REQUEST, request)

    def send_reset_email(self, user):
        """Sends password reset email using the custom Email class."""
        token = default_token_generator.make_token(user)
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        reset_link = f"{settings.FRONTEND_URL}/reset-password/?uid={uid}&token={token}"
        if not user.has_translation("en"):
            user.create_translation("en", first_name="User")
            user.save()
        first_name = getattr(user, "first_name", "User")

        email_body = f"""
        <p>Hello {first_name},</p>
        <p>Click the link below to reset your password:</p>
        <a href="{reset_link}">{reset_link}</a>
        <p>If you didn’t request this, please ignore this email.</p>
        """

        email = Email(subject="Password Reset Request")
        email.to(user.email, name=first_name).add_html(email_body).send()


class PasswordResetConfirmView(APIView):
    """Set password using link sent via Email"""

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.context["user"]
            user.set_password(serializer.validated_data["new_password"])
            user.save()
            return handle_response(
                {"message": "Password reset successful."}, status.HTTP_200_OK, request
            )

        return handle_response(serializer.errors, status.HTTP_400_BAD_REQUEST, request)


class PasswordResetView(APIView):
    """
    Handle Password change/Reset using and old password and
    verified old password before set new password.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PasswordResetSerializer(
            data=request.data, context={"request": request}
        )
        if serializer.is_valid():
            user = request.user
            user.set_password(serializer.validated_data["new_password"])
            user.save()
            return handle_response(
                {"message": "Password updated successfully."}, status=status.HTTP_200_OK
            )

        return handle_response(serializer.errors, status.HTTP_400_BAD_REQUEST, request)


class UserListView(generics.ListAPIView):
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = User.objects.all()
    filter_backends = [filters.SearchFilter]
    search_fields = ["email", "first_name", "last_name", "role"]

    def get_queryset(self):
        user = self.request.user
        qs = super().get_queryset()

        if user.role == "SuperAdmin":
            return qs
        elif user.role == "Admin":
            return qs.exclude(Q(role="SuperAdmin") | Q(role="Admin"))
        elif user.role == "Landlord":
            return qs.filter(Q(id=user.id) | Q(role="Agent", created_by=user))
        elif user.role == "Agent":
            return qs.filter(id=user.id)
        return User.objects.none()

class UserDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated, CanViewUser, CanEditUser, CanDeleteUser]
    queryset = User.objects.all()


class AdminRegisterUserView(generics.CreateAPIView):
    serializer_class = AdminRegisterUserSerializer
    permission_classes = [IsAuthenticated, IsSuperOrAdmin]

    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class LandlordAgentTeamView(APIView):
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        if request.user.role != User.LANDLORD:
            return handle_response(
                {"error": "Only landlords can view their agent team"},
                status.HTTP_403_FORBIDDEN,
                request
            )
        relationships = LandlordAgentRelationship.objects.filter(landlord=request.user)
        agents = [rel.agent for rel in relationships]
        serializer = UserSerializer(agents, many=True, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)


class CreateAgentView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        try:
            if request.user.role != User.LANDLORD:
                return Response({"detail": _("Only landlords can create agents.")}, status=status.HTTP_403_FORBIDDEN)

            data = request.data.copy()
            serializer = AgentCreateSerializer(data=data, context={"request": request})
            if serializer.is_valid():
                agent = serializer.save()
                LandlordAgentRelationship.objects.create(landlord=request.user, agent=agent)
                return Response(
                    {"detail": _("Agent created and credentials sent via email.")},
                    status=status.HTTP_201_CREATED,
                )
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        except Exception as e:
            return handle_response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )