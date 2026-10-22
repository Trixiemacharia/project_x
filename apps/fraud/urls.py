from django.urls import path

from . import views

urlpatterns = [
    path("cases/", views.CaseListView.as_view()),
    path("cases/<str:reference>/", views.CaseDetailView.as_view()),
    path("cases/<str:reference>/review/", views.CaseReviewView.as_view()),
    path("alerts/", views.AlertsView.as_view()),
    path("analytics/", views.AnalyticsView.as_view()),
    path("notifications/", views.NotificationsView.as_view()),
    path("ws-ticket/", views.WsTicketView.as_view()),
]
