from django.urls import path
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
    TokenVerifyView,
)

from .views import (
    RegistrationView,
    LoginView,
    LogoutView,
    VerifyEmailView,
    PasswordResetConfirmView,
    PasswordResetView,
    ForgotPasswordView,
    UserDetailsView,
)


urlpatterns = [
    path("register", RegistrationView.as_view(), name="register"),
    path("login", LoginView.as_view(), name="login"),
    path("logout", LogoutView.as_view(), name="logout"),
    path(
        "verify-email/<str:uidb64>/<str:token>",
        VerifyEmailView.as_view(),
        name="verify-email",
    ),
    path("forgot-password", ForgotPasswordView.as_view(), name="forgot_password"),
    path(
        "reset-password/",
        PasswordResetConfirmView.as_view(),
        name="confirm_password",
    ),
    path("reset-password-confirm", PasswordResetView.as_view(), name="reset_password"),
    path("users/<int:id>", UserDetailsView.as_view(), name="user_detail"),
    path("token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("token/verify/", TokenVerifyView.as_view(), name="token_verify"),
]