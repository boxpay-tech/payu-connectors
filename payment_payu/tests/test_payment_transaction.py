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
        
        # Create partner
        self.partner = self.env['res.partner'].create({
            'name': 'Test User',
            'email': 'test@example.com',
            'phone': '9999999999',
        })

        # Load provider and payment method (assumes existing demo or test data for 'payment_payu.payment_provider_payu')
        self.provider = self.env.ref('payment_payu.payment_provider_payu')
        self.provider.write({
            'state': 'test',
        })
        payment_method = self.provider.payment_method_ids and self.provider.payment_method_ids[0] or None

        # Create a payment.transaction record linked to provider etc.
        self.tx = self.env['payment.transaction'].create({
            'amount': 100.0,
            'partner_id': self.partner.id,
            'provider_id': self.provider.id,
            'provider_code': 'payu',
            'reference': 'TXN_TEST_001',
            'currency_id': self.env.ref('base.INR').id,
            'payment_method_id': payment_method.id if payment_method else False,
        })

    def test_compute_is_refund(self):
        self.tx.amount = -50.0
        self.tx._compute_is_refund()
        self.assertTrue(self.tx.is_refund)
        self.tx.amount = 20.0
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

        # To avoid JSON error, ensure attributes accessed return basic types
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
    
    def test_get_specific_rendering_values_missing_credential(self):
        self.tx.currency_id = self.env.ref('base.INR')
        self.tx.provider_code = 'payu'

        payuCredentialClass = self.env.registry['payu.credential']

        with patch.object(payuCredentialClass, 'search', return_value=self.env['payu.credential'].browse([])):
            with self.assertRaises(ValidationError) as cm:
                self.tx._get_specific_rendering_values({'partner_id': self.partner.id})
            self.assertIn('No credentials configured', str(cm.exception))

    def test_get_specific_rendering_values_success(self):
        fake_credential = MagicMock()
        fake_credential.merchant_key = 'FAKE_KEY'

        payuCredentialClass = self.env.registry['payu.credential']
        provider_class = self.env.registry['payment.provider']

        fake_partner = MagicMock()
        fake_partner.name = 'John Doe'
        fake_partner.email = 'john@example.com'
        fake_partner.phone = '9999999999'

        fake_order = MagicMock()
        fake_order.id = 123
        fake_order.order_line = []

        fake_website = MagicMock()
        fake_website.sale_get_order.return_value = fake_order

        with patch.object(payuCredentialClass, 'search', return_value=fake_credential), \
            patch.object(self.env.registry['res.partner'], 'browse', return_value=fake_partner), \
            patch('werkzeug.urls.url_join', side_effect=lambda base, path: base + path), \
            patch.object(provider_class, '_payu_generate_sign', return_value='dummyhash'), \
            MockRequest(self.env):

            # Set the website attribute on the global request proxy
            from odoo.http import request
            request.website = fake_website

            values = self.tx._get_specific_rendering_values({'partner_id': self.partner.id})

            self.assertIn('hash', values)
            self.assertEqual(values['udf3'], 'website')
            self.assertEqual(values['key'], 'FAKE_KEY')
            self.assertEqual(values['firstname'], 'John')
            
    # New Test cases
    def test_get_payment_dns(self):
        # test mode
        self.provider.state = "test"
        self.assertEqual(self.tx._get_payment_dns(self.provider), "test.payu.in")
        # live mode
        self.provider.state = "enabled"
        self.assertEqual(self.tx._get_payment_dns(self.provider), "secure.payu.in")

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
        self.assertEqual(self.tx.amount, 100.0)

    def test_update_amount_if_present_missing_charges(self):
        self.tx._update_amount_if_present({"net_amount_debit": "150", "additionalCharges": None})
        self.assertEqual(self.tx.amount, 150.0)