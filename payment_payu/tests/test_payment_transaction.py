# -*- coding: utf-8 -*-
import json
from unittest.mock import patch, MagicMock, ANY
from odoo.http import request
from odoo.tests import TransactionCase
from odoo.exceptions import ValidationError
from collections import namedtuple

from odoo.addons.website.tools import MockRequest

from odoo.addons.payment_payu.models.payment_transaction import PaymentTransaction

Product = namedtuple('Product', ['default_code', 'name', 'id'])
OrderLine = namedtuple('OrderLine', ['product_id', 'price_total', 'product_uom_qty'])

InvoiceLine = namedtuple('InvoiceLine', ['product_id', 'price_total', 'quantity'])
Invoice = namedtuple('Invoice', ['invoice_line_ids', 'amount_total', 'amount_untaxed'])

class TestPayUPaymentTransaction(TransactionCase):
    
    def setUp(self):
        super().setUp()
        self.env = self.env(context=dict(self.env.context, lang='en_US'))

        self.partner = self.env['res.partner'].create({
            'name': 'Test User',
            'email': 'test@example.com',
            'phone': '9999999999',
        })

        self.provider = self.env.ref('payment_payu.payment_provider_payu')
        self.provider.write({'state': 'test'})
        payment_method = self.provider.payment_method_ids and self.provider.payment_method_ids[0] or None

        self.tx = self.env['payment.transaction'].create({
            'amount': 100.0,
            'partner_id': self.partner.id,
            'provider_id': self.provider.id,
            'provider_code': 'payu',
            'reference': 'TXN_TEST_001',
            'currency_id': self.env.ref('base.INR').id,
            'payment_method_id': payment_method.id if payment_method else False,
        })

        self.credential = MagicMock()
        self.credential.merchant_key = 'fake_key'
        self.credential.merchant_salt = 'fake_salt'
        self.credential.provider_id = MagicMock(state='test', name='PayU', id=1)
        self.credential.currency_id = MagicMock(name='INR')
        self.credential.id = 1


    def test_compute_is_refund(self):
        self.tx.amount = -10.0
        self.tx._compute_is_refund()
        self.assertTrue(self.tx.is_refund)
        self.tx.amount = 10.0
        self.tx._compute_is_refund()
        self.assertFalse(self.tx.is_refund)

    def test_get_productinfo_string(self):
        order_line_mock = MagicMock()
        order_line_mock.product_id.display_name = 'Product A'
        order_mock = MagicMock()
        order_mock.order_line = [order_line_mock]
        result = self.tx.get_productinfo_string(order_mock)
        self.assertEqual(result, 'Product A')

    def test_get_cart_details(self):
        product = MagicMock()
        product.default_code = 'SKU001'
        product.name = 'Test Product'
        product.id = 1

        line = MagicMock()
        line.product_id = product
        line.price_total = 100.0
        line.product_uom_qty = 2

        order = MagicMock()
        order.order_line = [line]
        order.amount_total = 200.0
        order.amount_undiscounted = 210.0

        cart_json = self.tx.get_cart_details(order)
        cart = json.loads(cart_json)
        self.assertEqual(cart['amount'], 200.0)
        self.assertEqual(cart['items'], 2)
        self.assertEqual(cart['sku_details'][0]['sku_id'], 'SKU001')

    def test_get_invoice_cart_details(self):
        product = Product(default_code='SKU001', name='Test Product', id=1)
        line = InvoiceLine(product_id=product, price_total=50.0, quantity=1)
        invoice = Invoice(invoice_line_ids=[line], amount_total=50.0, amount_untaxed=45.0)

        cart_json = self.tx.get_invoice_cart_details(invoice)
        cart = json.loads(cart_json)
        self.assertEqual(cart['amount'], 50.0)
        self.assertEqual(cart['items'], 1)

    def test_capture_and_void_not_supported(self):
        with self.assertRaises(NotImplementedError):
            self.tx.send_capture_request()
        with self.assertRaises(NotImplementedError):
            self.tx._send_void_request()

    def test_get_payment_dns_for_test_and_live(self):
        self.provider.state = 'test'
        self.assertEqual(self.tx._get_payment_dns(self.provider), 'test.payu.in')
        self.provider.state = 'enabled'
        self.assertEqual(self.tx._get_payment_dns(self.provider), 'secure.payu.in')

    @patch("odoo.addons.payment.models.payment_transaction.PaymentTransaction._set_canceled")
    def test_process_notification_data_none(self, mocked):
        self.tx.provider_code = "payu"
        self.tx._process_notification_data(None)
        mocked.assert_called_once_with()

    @patch("odoo.addons.payment_payu.models.payment_transaction.PaymentTransaction._payu_verify_return_sign")
    @patch("odoo.addons.payment_payu.models.payment_transaction.PaymentTransaction._handle_success_status")
    def test_process_notification_data_success(self, mocked_handle, mocked_verify):
        self.tx.provider_code = "payu"
        data = {"mihpayid": "123", "status": "success", "hash": "abc"}
        self.tx._process_notification_data(data)
        mocked_handle.assert_called_once()

    @patch("odoo.addons.payment_payu.models.payment_transaction.PaymentTransaction._handle_failure_status")
    @patch("odoo.addons.payment_payu.models.payment_transaction.PaymentTransaction._payu_verify_return_sign")
    def test_process_notification_data_failure(self, mocked_verify, mocked_handle):
        self.tx.provider_code = "payu"
        data = {"mihpayid": "123", "status": "failure", "hash": "abc"}
        self.tx._process_notification_data(data)
        mocked_handle.assert_called_once()

    @patch("odoo.addons.payment.models.payment_transaction.PaymentTransaction._set_canceled")
    @patch("odoo.addons.payment_payu.models.payment_transaction.PaymentTransaction._payu_verify_return_sign")
    def test_process_notification_data_unknown_status(self, mocked_verify, mocked_canceled):
        self.tx.provider_code = "payu"
        data = {"mihpayid": "123", "status": "other", "hash": "abc"}
        self.tx._process_notification_data(data)
        mocked_canceled.assert_called_once()

    def test_update_amount_if_present(self):
        self.tx.amount = 100.0
        self.tx._update_amount_if_present({"net_amount_debit": "120", "additionalCharges": "20"})
        self.assertEqual(self.tx.amount, 100.0)  # Does not update if net_amount_debit present with additionalCharge

    def test_update_amount_if_present_missing_charges(self):
        self.tx._update_amount_if_present({"net_amount_debit": "150", "additionalCharges": None})
        self.assertEqual(self.tx.amount, 150.0)  # Updates correctly if additionalCharges missing

    def test_paysu_verify_return_sign_raises(self):
        with self.assertRaises(ValidationError):
            self.tx._payu_verify_return_sign({})  # Missing 'hash' in data

    def test_generate_digest_and_signature_and_auth_header(self):
        body = "sample body"
        digest = self.tx.generate_digest(body)
        self.assertIsInstance(digest, str)
        signature = self.tx.generate_signature("Thu, 18 Sep 2025 14:11:00 UTC", digest, "secret")
        self.assertIsInstance(signature, str)
        auth_header = self.tx.generate_authorization_header("fake_key", signature)
        self.assertIn('hmac username=', auth_header)
    
    @patch('odoo.addons.payment_payu.models.payment_transaction.PaymentTransaction.env', create=True)
    def test_get_payu_credentials(self, mock_env):
        mock_pyu_cred_model = mock_env['payu.credential']
        mock_pyu_cred_model.search.return_value = [self.credential]

        credentials = self.tx._get_payu_credentials()

        mock_pyu_cred_model.search.assert_called_once_with([])
        self.assertEqual(credentials, [self.credential])


    @patch.object(PaymentTransaction, 'generate_authorization_header', return_value='auth-header')
    def test_build_request_headers(self, mock_generate_auth_header):
        formatted_date = 'Thu, 18 Sep 2025 04:00:00 UTC'
        digest = 'digeststring'
        signature = 'signaturestring'

        headers = self.tx._build_request_headers(self.credential, formatted_date, digest, signature)

        mock_generate_auth_header.assert_called_once_with(self.credential.merchant_key, signature)
        self.assertEqual(headers, {
            'Date': formatted_date,
            'Digest': digest,
            'Authorization': 'auth-header',
        })

    @patch('requests.get')
    def test_call_payu_api(self, mock_get):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = '{"result": "ok"}'
        mock_response.json.return_value = {"result": "ok"}
        mock_get.return_value = mock_response

        result = self.tx._call_payu_api('http://endpoint', {}, {})
        mock_get.assert_called_once()
        self.assertEqual(result, {"result": "ok"})

        # Test JSON error handling
        mock_response.json.side_effect = ValueError('bad json')
        result = self.tx._call_payu_api('http://endpoint', {}, {})
        self.assertEqual(result, {})

    def test_process_settlement_data(self):
        mock_tx = MagicMock()
        mock_tx.id = 123
        mock_tx.write = MagicMock()

        # Test no data case
        res = self.tx._process_settlement_data({'result': {'size': 0}}, self.credential)
        self.assertFalse(res)

        result_data = {
            'result': {
                'size': 1,
                'data': [{
                    'utrNumber': 'UTR123',
                    'transaction': [{
                        'payuId': 'TX123',
                        'merchantNetAmount': '100.00',
                        'merchantServiceFee': '5.00',
                        'merchantServiceTax': '2.00',
                        'settlementCurrency': 'INR',
                    }]
                }]
            }
        }

        # Patch the search method on payment.transaction model class to return mock_tx
        with patch('odoo.addons.payment.models.payment_transaction.PaymentTransaction.search', return_value=mock_tx):
            res = self.tx._process_settlement_data(result_data, self.credential)

            # Verify the write was called with expected values
            mock_tx.write.assert_called_once_with({
                'settled_amount': 100.0,
                'total_service_fee': 7.0,
                'settlement_currency': 'INR',
                'utr_number': 'UTR123'
            })

            self.assertTrue(res)

    def test_get_settlement_endpoint(self):
        self.assertEqual(self.tx._get_settlement_endpoint('test'),
                         'https://settlement-data.free.beeceptor.com/settlement')
        self.assertEqual(self.tx._get_settlement_endpoint('enabled'),
                         'https://info.payu.in/settlement/range')