import re
from django.contrib.auth import authenticate, get_user_model
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes
from django.contrib.auth.tokens import default_token_generator
from django.conf import settings
from rest_framework import status, generics, permissions
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.permissions import IsAuthenticated, AllowAny

from .serializers import (
    RegisterSerializer,
    LoginSerializer,
    UserSerializer,
    PasswordResetSerializer,
    ForgotPasswordSerializer,
    PasswordResetConfirmSerializer,
    AdminRegisterUserSerializer,
)
from utils.email_services import Email
from utils.translation_services import TranslateService
from .models import User


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
            user_language = request.data.get("language", "en")
            if not user.has_translation(user_language):
                return Response(
                    {
                        "error": "User does not have a translation for the current language."
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            refresh = RefreshToken.for_user(user)
            return Response(
                {
                    "refresh": str(refresh),
                    "access": str(refresh.access_token),
                    "user": UserSerializer(user, context={"request": request}).data,
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            return handle_response(
                {"error": f"Login Invalid. {str(e)}"},
                status.HTTP_400_BAD_REQUEST,
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

        except Exception as e:
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


class UserDetailsView(APIView):
    permission_classes = [IsAuthenticated]
    """Handle retrive, updating and deletion of User by ID"""

    def get(self, request, id=None):
        try:
            if id:
                user = User.objects.get(id=id)
                if request.user.is_superadmin():
                    pass
                elif request.user.role == User.ADMIN:
                    if user.role not in [User.ADMIN, User.SUPERADMIN]:
                        return handle_response(
                            {"error": "You do not have permission to view this user."},
                            status.HTTP_403_FORBIDDEN,
                            request,
                        )
                elif request.user.id != user.id:
                    return handle_response(
                        {"error": "You do not have permission to view this user."},
                        status.HTTP_403_FORBIDDEN,
                        request,
                    )
                language = request.query_params.get("language", "en")
                user.set_current_language(language)
                serializer = UserSerializer(user, context={"request": request, "language": language})
                return Response(serializer.data, status=status.HTTP_200_OK)
 
            else:
                if request.user.is_superadmin():
                    pass
                elif request.user.role == User.ADMIN:
                    if user.role not in [User.ADMIN, User.SUPERADMIN]:
                        return handle_response(
                            {"error": "You do not have permission to view this user."},
                            status.HTTP_403_FORBIDDEN,
                            request,
                        )
                # elif request.user.id != user.id:
                #     return handle_response(
                #         {"error": "You do not have permission to view this user."},
                #         status.HTTP_403_FORBIDDEN,
                #         request,
                #     )
                users = User.objects.all()
                serializer = UserSerializer(users, many=True, context={"request": request})
                return Response(serializer.data, status=status.HTTP_200_OK)
        except User.DoesNotExist:
            return handle_response(
                {"error": f"User not found for ID {id}"},
                status.HTTP_404_NOT_FOUND,
                request,
            )

    def put(self, request, id):
        try:
            user = User.objects.get(id=id)
            if "role" in request.data and request.data["role"] != user.role:
                if not request.user.is_superadmin():
                    return handle_response(
                        {"error": "Only SuperAdmin can change roles."},
                        status.HTTP_403_FORBIDDEN,
                        request,
                    )
            if request.user.id != user.id and not request.user.is_superadmin():
                return handle_response(
                    {"error": "You do not have permission to update this user."},
                    status.HTTP_403_FORBIDDEN,
                    request,
                )
            serializer = UserSerializer(user, data=request.data, partial=True)
            if serializer.is_valid():
                serializer.save()
                return Response(serializer.data, status=status.HTTP_200_OK)
                # return handle_response(serializer.data, status.HTTP_200_OK, request)
            return handle_response(
                serializer.errors, status.HTTP_400_BAD_REQUEST, request
            )
        except User.DoesNotExist:
            return handle_response(
                {"error": f"User not found for {id}"},
                status.HTTP_404_NOT_FOUND,
                request,
            )

    def delete(self, request, id):
        try:
            user = User.objects.get(id=id)
            if request.user.id == user.id:
                pass
            elif request.user.is_superadmin():
                if user.role == User.SUPERADMIN:
                    return handle_response(
                        {"error": "SuperAdmin cannot delete another SuperAdmin."},
                        status.HTTP_403_FORBIDDEN,
                        request,
                    )
                pass
            elif request.user.role == User.ADMIN:
                if user.role in [User.SUPERADMIN, User.ADMIN]:
                    return handle_response(
                        {"error": "Admins cannot delete other Admins or SuperAdmins."},
                        status.HTTP_403_FORBIDDEN,
                        request,
                    )
                pass
            else:
                return handle_response(
                    {"error": "You do not have permission to delete this user."},
                    status.HTTP_403_FORBIDDEN,
                    request,
                )
            user.delete()
            return handle_response(
                {"message": "User deleted successfully!"}, status.HTTP_200_OK, request
            )
        except User.DoesNotExist:
            return handle_response(
                {"error": f"User not found for {id}."},
                status.HTTP_404_NOT_FOUND,
                request,
            )


class AdminRegisterUserView(generics.CreateAPIView):
    serializer_class = AdminRegisterUserSerializer
    permission_classes = [permissions.IsAdminUser]

    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)