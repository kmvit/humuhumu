from decimal import Decimal

from rest_framework.test import APITestCase


class DemoFixturesTests(APITestCase):
    """Пример использования демо-фикстур в тестах + проверка целостности данных."""

    fixtures = ["demo_users", "demo_catalog", "demo_orders", "demo_wallet"]

    def auth(self, username, password="demo12345"):
        res = self.client.post(
            "/api/auth/token/",
            {"username": username, "password": password},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")

    def test_client_sees_only_available_products(self):
        self.auth("anna")
        res = self.client.get("/api/products/")
        # 9 товаров в фикстуре, 1 недоступен → клиент видит 8
        self.assertEqual(len(res.data), 8)

    def test_wallet_balance_matches_ledger(self):
        self.auth("anna")
        balance = Decimal(self.client.get("/api/wallet/").data["balance"])
        txns = self.client.get("/api/wallet/transactions/").data
        self.assertEqual(balance, sum(Decimal(t["amount"]) for t in txns))
        self.assertEqual(balance, Decimal("850.00"))

    def test_pay_with_tokens_decreases_balance(self):
        self.auth("anna")
        res = self.client.post(
            "/api/orders/",
            {"pay_method": "tokens", "items": [{"product": 3, "quantity": 1}]},
            format="json",
        )
        self.assertEqual(res.status_code, 201)
        new_balance = Decimal(self.client.get("/api/wallet/").data["balance"])
        self.assertEqual(new_balance, Decimal("700.00"))  # 850 - 150 (Эспрессо)
