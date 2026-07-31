from django.shortcuts import get_object_or_404
from rest_framework import generics, status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from users.permissions import IsCashierOrAdmin

from .models import TokenPackage, Wallet
from .serializers import (
    TokenPackageSerializer,
    TokenTransactionSerializer,
    TopupSerializer,
    WalletSerializer,
)
from .services import topup_by_package


class MyWalletView(generics.RetrieveAPIView):
    """GET /api/wallet/ — кошелёк текущего пользователя."""

    serializer_class = WalletSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        wallet, _ = Wallet.objects.get_or_create(user=self.request.user)
        return wallet


class MyTransactionsView(generics.ListAPIView):
    """GET /api/wallet/transactions/ — журнал движений текущего пользователя."""

    serializer_class = TokenTransactionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return self.request.user.wallet.transactions.all()


class TokenPackageViewSet(viewsets.ReadOnlyModelViewSet):
    """GET /api/token-packages/ — активные пакеты токенов."""

    queryset = TokenPackage.objects.filter(is_active=True)
    serializer_class = TokenPackageSerializer
    permission_classes = [IsAuthenticated]


class TopupView(APIView):
    """POST /api/wallet/topup/ — кассир/админ пополняет кошелёк клиента по пакету."""

    permission_classes = [IsCashierOrAdmin]

    def post(self, request):
        serializer = TopupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        wallet = get_object_or_404(Wallet, user_id=serializer.validated_data["client"])
        package = get_object_or_404(
            TokenPackage, pk=serializer.validated_data["package"], is_active=True
        )
        topup_by_package(wallet.id, package)
        wallet.refresh_from_db()
        return Response(
            {"balance": wallet.balance}, status=status.HTTP_201_CREATED
        )
