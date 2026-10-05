from django.urls import path

from . import views

urlpatterns = [
    path("", views.WalletView.as_view(), name="wallet-me"),
    path("transfer/", views.TransferView.as_view(), name="wallet-transfer"),
    path("transactions/", views.TransactionListView.as_view(), name="wallet-transactions"),
    path("transactions/<uuid:pk>/", views.TransactionDetailView.as_view(), name="wallet-transaction-detail"),
    path("beneficiaries/", views.BeneficiaryListCreateView.as_view(), name="wallet-beneficiaries"),
    path("beneficiaries/<uuid:pk>/", views.BeneficiaryDetailView.as_view(), name="wallet-beneficiary-detail"),
]