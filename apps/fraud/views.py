import secrets
from datetime import timedelta

from django.core.cache import cache
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import generics
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import FraudCase, OutboxEvent, RiskAssessment
from .permissions import IsFraudAnalyst
from .serializers import CaseDetailSerializer, CaseListSerializer, ReviewSerializer
from .services import ReviewError, review_case

TICKET_TTL = 30


class Pagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class CaseListView(generics.ListAPIView):
    """GET /fraud/cases/?status=&kind=&priority=&category=&type=&from=&to=&search=<case or txn reference>"""

    permission_classes = [IsFraudAnalyst]
    pagination_class = Pagination
    serializer_class = CaseListSerializer

    def get_queryset(self):
        qs = FraudCase.objects.select_related("transaction", "assessment")
        p = self.request.query_params
        filters = {"status": "status", "kind": "kind", "priority": "priority",
                   "category": "assessment__category", "type": "transaction__type"}
        for param, lookup in filters.items():
            if p.get(param):
                qs = qs.filter(**{lookup: p[param]})
        for param, lookup in (("from", "created_at__date__gte"), ("to", "created_at__date__lte")):
            if p.get(param):
                try:
                    day = parse_date(p[param])
                except ValueError:
                    day = None
                if day is None:
                    raise ValidationError({param: "Invalid date. Use YYYY-MM-DD."})
                qs = qs.filter(**{lookup: day})
        if p.get("search"):
            s = p["search"].strip()
            qs = qs.filter(Q(reference__iexact=s) | Q(transaction__reference__iexact=s))
        return qs


class CaseDetailView(generics.RetrieveAPIView):
    permission_classes = [IsFraudAnalyst]
    serializer_class = CaseDetailSerializer
    lookup_field = "reference"
    queryset = FraudCase.objects.select_related("transaction", "assessment")


class CaseReviewView(APIView):
    permission_classes = [IsFraudAnalyst]

    def post(self, request, reference):
        serializer = ReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        case = get_object_or_404(FraudCase, reference=reference)
        try:
            case = review_case(case_id=case.pk, analyst=request.user, **serializer.validated_data)
        except ReviewError as exc:
            return Response({"detail": exc.detail, "code": exc.code}, status=exc.status_code)
        case = FraudCase.objects.select_related("transaction", "assessment").get(pk=case.pk)
        return Response(CaseDetailSerializer(case).data)


def _recover(request, qs):
    try:
        after = int(request.query_params.get("after", 0))
    except ValueError:
        raise ValidationError({"after": "Must be an integer event id."})
    return Response({"events": [e.as_message() for e in qs.filter(id__gt=after).order_by("id")[:100]]})


class AlertsView(APIView):
    """Missed-alert recovery for analysts. Clients dedupe on event id."""

    permission_classes = [IsFraudAnalyst]

    def get(self, request):
        return _recover(request, OutboxEvent.objects.filter(audience=OutboxEvent.Audience.ANALYST))


class NotificationsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return _recover(request, OutboxEvent.objects.filter(audience=OutboxEvent.Audience.CUSTOMER, user=request.user))


class AnalyticsView(APIView):
    permission_classes = [IsFraudAnalyst]

    def get(self, request):
        try:
            days = min(max(int(request.query_params.get("days", 30)), 1), 90)
        except ValueError:
            raise ValidationError({"days": "Must be an integer."})
        since = timezone.now() - timedelta(days=days)
        by_status = FraudCase.objects.filter(created_at__gte=since).values("status").annotate(n=Count("id"))
        by_category = RiskAssessment.objects.filter(created_at__gte=since).values("category").annotate(n=Count("id"))
        return Response({"days": days,
                         "cases_by_status": {r["status"]: r["n"] for r in by_status},
                         "assessments_by_category": {r["category"]: r["n"] for r in by_category}})


class WsTicketView(APIView):
    """Short-lived one-time ticket so the JWT never appears in a WebSocket URL."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        ticket = secrets.token_urlsafe(24)
        cache.set(f"wsticket:{ticket}", request.user.pk, TICKET_TTL)
        return Response({"ticket": ticket, "expires_in": TICKET_TTL})