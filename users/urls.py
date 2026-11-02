from django.urls import path
from .views import (
    ChangePasswordView, CreateStaffAccountView, DeleteUserView, ForgotPasswordRequestView, RegisterView, ResendOtpView, ResetPasswordView, StaffSelfRegisterView, ToggleUserStatusView, VerifyOtpView, MeView,
    CookieTokenObtainPairView, CookieTokenRefreshView, LogoutView
)

urlpatterns = [
    path('register/', RegisterView.as_view()),
    path('otp/verify/', VerifyOtpView.as_view()),
    path('login/', CookieTokenObtainPairView.as_view()),
    path('login/refresh/', CookieTokenRefreshView.as_view()),
    path('me/', MeView.as_view()),
    path('logout/', LogoutView.as_view()),
    path('change-password/', ChangePasswordView.as_view()),
    path('forgot-password/', ForgotPasswordRequestView.as_view()),
    path('reset-password/', ResetPasswordView.as_view()),
    path('otp/resend/', ResendOtpView.as_view()),
    path('staff-register/', StaffSelfRegisterView.as_view()),
]