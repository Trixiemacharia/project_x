from django.urls import path

from . import views

urlpatterns = [
    path("mpesa/stk/", views.MpesaStkPushView.as_view(), name="mpesa-stk"),
    path("mpesa/callback/<str:secret>/", views.MpesaCallbackView.as_view(), name="mpesa-callback"),
    path("mpesa/payments/", views.MpesaPaymentListView.as_view(), name="mpesa-payment-list"),
    path("mpesa/payments/<uuid:pk>/", views.MpesaPaymentDetailView.as_view(), name="mpesa-payment-detail"),
]