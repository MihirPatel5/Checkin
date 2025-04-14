from django.urls import path
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
    TokenVerifyView,
)

from .views import (
    LandlordAgentTeamView,
    RegistrationView,
    LoginView,
    LogoutView,
    VerifyEmailView,
    PasswordResetConfirmView,
    PasswordResetView,
    ForgotPasswordView,
    UserListView,
    UserDetailView,
    CreateAgentView,
    AdminRegisterUserView,
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
    path("users", UserListView.as_view(), name="user-list"),
    path("users/<int:pk>", UserDetailView.as_view(), name="user-detail"),
    path("users/create-agent/", CreateAgentView.as_view(), name="create-agent"),
    path("user/add/", AdminRegisterUserView.as_view(), name="add_user"),
    # path("user/agent-team", LandlordAgentTeamView.as_view(), name="agent_team"),
    path("token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("token/verify/", TokenVerifyView.as_view(), name="token_verify"),
]
