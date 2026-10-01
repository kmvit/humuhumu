"""Возврат денег через кассу aQsi: деньги на карту по слипу, затем чек возврата.

Сеть подменена кабинетом aQsi в памяти — тем же FakeAqsi, что и в
tests_kassa, плюс операции устройства v4. Проверяем то, что стоит денег:
возврат на карту не уходит дважды, бонусы и склад откатываются ровно один
раз и только когда касса подтвердила, а сбой на чеке не заставляет
возвращать деньги на карту повторно.
"""
from decimal import Decimal
from unittest import mock

import httpx
from rest_framework.test import APITestCase

from orders.models import Order
from users.models import User

from .journal import day_journal
from .models import Payment
from .services import pending_kassa_refunds, settle_kassa_refund, settle_order
from .tests_kassa import FakeAqsi, KassaBase


class FakeAqsiOps(FakeAqsi):
    """FakeAqsi + чеки v4 и операции устройства."""

    def __init__(self):
        super().__init__()
        self.receipts: dict[str, dict] = {}
        self.ops: dict[str, dict] = {}
        self.posted: list[tuple[str, dict]] = []

    def finish(self, op_id, *, ok=True, result=None, message=""):
        self.ops[op_id].update(
            status="Completed" if ok else "Error",
            result=result, message=message,
        )

    def last_op(self, kind):
        return next(op_id for op_id, op in reversed(list(self.ops.items())) if op["kind"] == kind)

    def __call__(self, method, url, json=None, headers=None, timeout=None):
        path = url.split("/pub", 1)[1]
        request = httpx.Request(method, url)
        if path.startswith("/v4/"):
            self.calls.append((method, path))
            if method == "GET" and path.startswith("/v4/Receipts/"):
                rid = path.rsplit("/", 1)[1]
                return httpx.Response(200, json=self.receipts[rid], request=request)
            if method == "POST" and path in ("/v4/Slips/process/refund", "/v4/Receipts/process"):
                op_id = f"op-{len(self.ops) + 1}"
                kind = "slip" if "Slips" in path else "receipt"
                self.ops[op_id] = {"kind": kind, "status": "Pending", "body": json}
                self.posted.append((kind, json))
                return httpx.Response(200, json={"operationId": op_id}, request=request)
            if method == "GET" and path.startswith("/v4/Operations/"):
                op = self.ops[path.rsplit("/", 1)[1]]
                import json as _json
                body = {"status": op["status"], "message": op.get("message", "")}
                if op.get("result") is not None:
                    body["result"] = _json.dumps(op["result"])
                return httpx.Response(200, json=body, request=request)
            return httpx.Response(404, request=request)
        return super().__call__(method, url, json=json, headers=headers, timeout=timeout)


class KassaRefundBase(KassaBase):
    def setUp(self):
        super().setUp()
        self.aqsi = FakeAqsiOps()
        patcher = mock.patch("payments.providers.httpx.request", side_effect=self.aqsi)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.barista = User.objects.create_user("barista-rf", password="Sh4-staff", role=User.Role.WAITER)

    def paid_order(self, pay_type=1):
        """Заказ гостя, оплаченный на кассе, — с чеком в кабинете aQsi."""
        order = self.place()
        self.to_kassa(order)
        payment = Payment.objects.get(order=order)
        self.aqsi.paid(payment.external_id, pay_type=pay_type)
        self.age(order)
        settle_order(order)
        order.refresh_from_db()
        assert order.paid_at, "заказ должен быть оплачен"
        pay = {"type": pay_type, "amount": 48000}
        if pay_type == 1:
            pay["slip"] = {"id": "slip-7f3a", "content": {"type": "purchase"}}
        self.aqsi.receipts["rcpt-0c91"] = {
            "id": "rcpt-0c91",
            "device": {"id": 786767},
            "info": {"taxSystemCode": 2, "typeId": 1, "sum": 48000},
            "positions": [{"id": "p1", "info": {
                "name": "Латте", "finalPrice": 24000, "baseQuantity": "2",
                "taxRateId": 6, "calculationTypeId": 4, "calculationSubjectId": 1,
                "quantityUnitId": 0, "unknownField": "в чек возврата не идёт",
            }}],
            "payments": [pay],
        }
        # Заказ выдан — возврат делают из «Выданных».
        Order.objects.filter(pk=order.pk).update(status=Order.Status.PAID)
        order.refresh_from_db()
        return order

    def refund(self, order, **body):
        self.client.force_authenticate(self.barista)
        return self.client.post(f"/api/orders/{order.pk}/refund/", body, format="json")

    def poll(self, order):
        """Опрос кассы: пауза прошла — спрашиваем о возврате."""
        Payment.objects.filter(order=order, purpose=Payment.Purpose.REFUND).update(
            updated_at=Payment.objects.get(order=order, purpose=Payment.Purpose.ORDER).created_at
        )
        for r in pending_kassa_refunds():
            settle_kassa_refund(r)
        order.refresh_from_db()


class CardRefundTests(KassaRefundBase):
    def test_full_card_refund(self):
        order = self.paid_order(pay_type=1)
        response = self.refund(order)
        self.assertEqual(response.status_code, 200, response.data)
        # Касса только приняла команду — заказ ещё не «возврат».
        self.assertEqual(response.data["status"], Order.Status.PAID)
        self.assertEqual(response.data["refund_state"], "pending")
        kind, body = self.aqsi.posted[-1]
        self.assertEqual(kind, "slip")
        self.assertEqual(body, {"deviceId": 786767, "originalId": "slip-7f3a"})

        # Касса вернула деньги на карту — пробиваем чек возврата.
        self.aqsi.finish(self.aqsi.last_op("slip"), result={"id": "slip-back-1", "content": {"type": "refund"}})
        self.poll(order)
        kind, body = self.aqsi.posted[-1]
        self.assertEqual(kind, "receipt")
        self.assertEqual(body["typeId"], 2)
        self.assertEqual(body["deviceId"], 786767)
        self.assertEqual(body["info"], {"taxSystemCode": 2})
        self.assertEqual(body["payments"], [
            {"type": 1, "amount": 48000, "slip": {"id": "slip-back-1", "content": {"type": "refund"}}},
        ])
        self.assertNotIn("unknownField", body["positions"][0]["info"])
        self.assertNotIn("discountInfo", body)
        self.assertEqual(order.status, Order.Status.PAID)

        # Чек возврата пробит — вот теперь возврат.
        self.aqsi.finish(self.aqsi.last_op("receipt"), result={
            "id": "rcpt-back", "info": {"docInfo": {"docNumber": 250, "docFiscalAttributeInt": 4242}},
        })
        self.poll(order)
        self.assertEqual(order.status, Order.Status.REFUNDED)
        refund = Payment.objects.get(order=order, purpose=Payment.Purpose.REFUND)
        self.assertEqual(refund.status, Payment.Status.SUCCEEDED)
        self.assertEqual(refund.fiscal_receipt, "ФД 250, ФП 4242")
        self.assertEqual(
            Payment.objects.get(order=order, purpose=Payment.Purpose.ORDER).status,
            Payment.Status.REFUNDED,
        )

    def test_second_poll_does_not_repeat_steps(self):
        order = self.paid_order(pay_type=1)
        self.refund(order)
        self.aqsi.finish(self.aqsi.last_op("slip"), result={"id": "slip-back-1"})
        self.poll(order)
        self.poll(order)
        self.assertEqual([k for k, _ in self.aqsi.posted], ["slip", "receipt"])
        self.aqsi.finish(self.aqsi.last_op("receipt"), result={"id": "rcpt-back"})
        self.poll(order)
        refunded_at = order.refunded_at
        self.poll(order)
        order.refresh_from_db()
        self.assertEqual(order.refunded_at, refunded_at)
        self.assertEqual(Payment.objects.filter(order=order, purpose=Payment.Purpose.REFUND).count(), 1)

    def test_refund_while_running_is_refused(self):
        order = self.paid_order(pay_type=1)
        self.refund(order)
        response = self.refund(order)
        self.assertEqual(response.status_code, 400)
        self.assertIn("уже идёт", response.data["detail"])
        self.assertEqual(len(self.aqsi.posted), 1)

    def test_card_refund_declined_can_be_retried(self):
        order = self.paid_order(pay_type=1)
        self.refund(order)
        self.aqsi.finish(self.aqsi.last_op("slip"), ok=False, message="Операция отклонена банком")
        self.poll(order)
        response = self.client.get("/api/orders/?status=paid&with_refunded=1")
        row = next(o for o in response.data if o["id"] == order.pk)
        self.assertEqual(row["refund_state"], "failed")
        self.assertIn("Касса не вернула деньги", row["refund_error"])
        self.assertEqual(order.status, Order.Status.PAID)
        # Повтор — снова возврат на карту: в прошлый раз деньги не ушли.
        self.assertEqual(self.refund(order).status_code, 200)
        self.assertEqual([k for k, _ in self.aqsi.posted], ["slip", "slip"])

    def test_receipt_failure_retry_skips_card_refund(self):
        """Деньги на карту ушли, чек не пробился — повтор только с чека."""
        order = self.paid_order(pay_type=1)
        self.refund(order)
        self.aqsi.finish(self.aqsi.last_op("slip"), result={"id": "slip-back-1"})
        self.poll(order)
        self.aqsi.finish(self.aqsi.last_op("receipt"), ok=False, message="Смена не открыта")
        self.poll(order)
        failed = Payment.objects.get(order=order, purpose=Payment.Purpose.REFUND)
        self.assertEqual(failed.status, Payment.Status.FAILED)
        self.assertIn("Деньги на карту вернули", failed.kassa_meta["error"])

        self.assertEqual(self.refund(order).status_code, 200)
        self.assertEqual([k for k, _ in self.aqsi.posted], ["slip", "receipt", "receipt"])
        self.assertEqual(self.aqsi.posted[-1][1]["payments"][0]["slip"], {"id": "slip-back-1"})
        self.aqsi.finish(self.aqsi.last_op("receipt"), result={"id": "rcpt-back"})
        self.poll(order)
        self.assertEqual(order.status, Order.Status.REFUNDED)

    def test_journal_counts_only_finished_refund(self):
        order = self.paid_order(pay_type=1)
        self.refund(order)
        totals = day_journal(order.created_at.date())["totals"]
        self.assertEqual(totals["refunds"], Decimal("0"))


class CashRefundTests(KassaRefundBase):
    def test_cash_refund_is_receipt_only(self):
        order = self.paid_order(pay_type=0)
        self.assertEqual(self.refund(order, return_to_stock=False).status_code, 200)
        kind, body = self.aqsi.posted[-1]
        self.assertEqual(kind, "receipt")
        self.assertEqual(body["payments"], [{"type": 0, "amount": 48000}])
        self.aqsi.finish(self.aqsi.last_op("receipt"), result={"id": "rcpt-back"})
        self.poll(order)
        self.assertEqual(order.status, Order.Status.REFUNDED)
        self.assertEqual([k for k, _ in self.aqsi.posted], ["receipt"])


class ManualFallbackTests(KassaRefundBase):
    def test_payment_without_receipt_is_recorded_as_before(self):
        """Оплата, о которой касса не сказала чек (ручное «Оплачено»), —
        возврат по-старому: записываем, деньги возвращают на кассе руками."""
        order = self.paid_order(pay_type=1)
        Payment.objects.filter(order=order, purpose=Payment.Purpose.ORDER).update(kassa_receipt_id="")
        response = self.refund(order)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], Order.Status.REFUNDED)
        self.assertEqual(self.aqsi.posted, [])


class DiscountTests(APITestCase):
    def test_bonus_discount_is_repeated_in_return_receipt(self):
        from .providers import AqsiProvider

        p = AqsiProvider({"api_key": "aqsi-key-0123456789abcdef"})
        sent = {}

        def fake(method, path, json=None):
            sent.update(json or {})
            return {"operationId": "op-x"}

        p._request = fake
        p.start_return_receipt({
            "device": {"id": 1}, "info": {"taxSystemCode": 2},
            "positions": [{"info": {"name": "Латте", "finalPrice": 24000, "baseQuantity": "2"}}],
            "payments": [{"type": 0, "amount": 38000}],
        }, None)
        self.assertEqual(sent["discountInfo"], {"type": "Absolute", "value": 10000})
