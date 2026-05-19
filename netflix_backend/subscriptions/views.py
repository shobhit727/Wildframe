from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated, AllowAny
from subscriptions.models import SubscriptionPlan, Subscription, Payment
from subscriptions.serializers import SubscriptionPlanSerializer, SubscriptionSerializer, PaymentSerializer

class SubscriptionPlanViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = SubscriptionPlan.objects.all()
    serializer_class = SubscriptionPlanSerializer
    permission_classes = [AllowAny]

class SubscriptionViewSet(viewsets.ModelViewSet):
    queryset = Subscription.objects.all()
    serializer_class = SubscriptionSerializer
    permission_classes = [IsAuthenticated]

class PaymentViewSet(viewsets.ModelViewSet):
    queryset = Payment.objects.all()
    serializer_class = PaymentSerializer
    permission_classes = [IsAuthenticated]
