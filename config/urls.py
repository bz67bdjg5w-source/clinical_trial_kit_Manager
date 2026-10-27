from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views
from inventory import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("accounts/signup/", views.signup, name="signup"),
    path("accounts/signup/resend-confirmation/", views.resend_signup_confirmation, name="resend_signup_confirmation"),
    path("accounts/signup/confirm/<uidb64>/<token>/", views.verify_signup, name="verify_signup"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", views.dashboard, name="dashboard"),
    path("kits/", views.kit_list, name="kit_list"),
    path("kits/quick-update/", views.quick_update_stock, name="quick_update_stock"),
    path("kits/add/", views.kit_create, name="kit_create"),
    path("kits/<int:pk>/edit/", views.edit_kit, name="edit_kit"),
    path("kits/<int:pk>/delete/", views.delete_kit, name="delete_kit"),
    path("kits/test-email/", views.send_test_email, name="send_test_email"),
    path("stock-history/", views.stock_history, name="stock_history"),
    path("profile/", views.profile, name="profile"),
]
