from django.urls import path
from . import views

urlpatterns = [
    path('login/', views.user_login, name='login'),
    path('logout/', views.user_logout, name='logout'),
    path('register/', views.register, name='register'),
    path('profile/', views.profile, name='profile'),
    path('dashboard/', views.profile, name='dashboard'),
    path('recharge/', views.recharge_wallet, name='recharge'),
    path('forgot_password/', views.forgot_password, name='forgot_password'),
    path('plans/', views.list_credit_plans, name='list_credit_plans'),
    path('orders/create/', views.create_payment_order, name='create_payment_order'),
    path('orders/verify/', views.verify_payment_order, name='verify_payment_order'),
    path('orders/<str:order_id>/status/', views.check_order_status, name='check_order_status'),
    path('orders/<str:order_id>/reconcile/', views.reconcile_order, name='reconcile_order'),
    path('webhooks/razorpay/', views.razorpay_webhook, name='razorpay_webhook'),
]
