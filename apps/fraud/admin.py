from django.contrib import admin

from .models import AuditLog, FraudCase, FraudReview, RiskAssessment


class ReadOnly(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


for model in (RiskAssessment, FraudCase, FraudReview, AuditLog):
    admin.site.register(model, ReadOnly)