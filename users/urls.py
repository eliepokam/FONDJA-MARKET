from django.urls import path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from .views import RegisterView, VerifyOtpView, MeView

urlpatterns = [
    path('register/', RegisterView.as_view()),
    path('otp/verify/', VerifyOtpView.as_view()),
    path('login/', TokenObtainPairView.as_view()),  # fonctionne direct grâce à USERNAME_FIELD='telephone'
    path('login/refresh/', TokenRefreshView.as_view()),
    path('me/', MeView.as_view()),
]