from rest_framework import serializers
from subscriptions.models import SubscriptionPlan, Subscription, Payment


class SubscriptionPlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionPlan
        fields = ['id', 'name', 'price', 'max_streams', 'max_resolution', 'description']


class SubscriptionSerializer(serializers.ModelSerializer):
    plan = SubscriptionPlanSerializer(read_only=True)
    user_email = serializers.CharField(source='user.email', read_only=True)

    class Meta:
        model = Subscription
        fields = ['id', 'user', 'user_email', 'plan', 'status', 'start_date', 'renewal_date', 'created_at', 'updated_at']
        read_only_fields = ['user', 'created_at', 'updated_at']


class PaymentSerializer(serializers.ModelSerializer):
    user_email = serializers.CharField(source='user.email', read_only=True)

    class Meta:
        model = Payment
        fields = ['id', 'user', 'user_email', 'subscription', 'amount', 'payment_method', 'status', 'transaction_id', 'created_at']
        read_only_fields = ['user', 'created_at', 'status']
