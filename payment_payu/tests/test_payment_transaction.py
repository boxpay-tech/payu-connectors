# -*- coding: utf-8 -*-
import json
from unittest.mock import patch, MagicMock, ANY

from odoo.tests import TransactionCase
from odoo.exceptions import ValidationError
from collections import namedtuple
from odoo.addons.payment_payu.models.payment_transaction import PaymentTransaction

Product = namedtuple('Product', ['default_code', 'name', 'id'])
OrderLine = namedtuple('OrderLine', ['product_id', 'price_total', 'product_uom_qty'])

class TestPayUPaymentTransaction(TransactionCase):
    


    def setUp(self):
        super().setUp()
        self.tx_model = self.env['payment.transaction']

        # create partner for testing
        self.partner = self.env['res.partner'].create({
            'name': 'Test User',
            'email': 'test@example.com',
            'phone': '9999999999',
        })

        # load provider from XML
        self.provider = self.env.ref('payment_payu.payment_provider_payu')

        # inject test credentials into existing provider
        self.provider.write({
            'payu_merchant_key': 'merchantKey123',
            'payu_merchant_salt': 'salt123',
            'state': 'test',
        })

        # use the payment method linked in XML
        payment_method = self.provider.payment_method_ids[0]

        # create a transaction linked to the provider and method
        self.transaction = self.tx_model.create({
            'amount': 100.0,
            'partner_id': self.partner.id,
            'provider_id': self.provider.id,
            'provider_code': 'payu',
            'reference': 'TXN_TEST_001',
            'currency_id': self.env.ref('base.INR').id,
            'payment_method_id': payment_method.id,
        })

    # 1. Check if refund is correctly flagged
    def test_01_is_refund_computed_correctly(self):
        self.transaction.amount = -50.0
        self.transaction._compute_is_refund()
        self.assertTrue(self.transaction.is_refund)

    # 2. checking productinfo string from a mock order
    def test_02_get_productinfo_string(self):
        order = MagicMock()
        order.order_line = [MagicMock(product_id=MagicMock(display_name='Product A')),
                            MagicMock(product_id=MagicMock(display_name='Product B'))]
        info = self.transaction.get_productinfo_string(order)
        self.assertEqual(info, 'Product A Product B')

    # 3. testing cart details generation for website order
    def test_03_get_cart_details(self):

        product = Product(default_code='SKU001', name='Product A', id=1)
        order_line = OrderLine(product_id=product, price_total=120.0, product_uom_qty=2)

        order = MagicMock()
        order.order_line = [order_line]
        order.amount_total = 120.0
        order.amount_undiscounted = 130.0

        cart_json = self.transaction.get_cart_details(order)
        cart = json.loads(cart_json)
        self.assertEqual(cart['amount'], 120.0)
        self.assertEqual(cart['sku_details'][0]['sku_id'], 'SKU001')

    # 4. testing invoice cart details generation
    def test_04_get_invoice_cart_details(self):
        
        product = Product(default_code='SKU001', name='Product A', id=1)
        
        invoice_line = MagicMock()
        invoice_line.product_id = product
        invoice_line.price_total = 50.0
        invoice_line.quantity = 1

        invoice = MagicMock()
        invoice.invoice_line_ids = [invoice_line]
        invoice.amount_total = 50.0
        invoice.amount_untaxed = 45.0

        cart_json = self.transaction.get_invoice_cart_details(invoice)
        cart = json.loads(cart_json)
        self.assertEqual(cart['amount'], 50.0)
        self.assertEqual(cart['items'], 1)

    # 5. testing void request
    def test_05_void_not_supported(self):

        with self.assertRaises(NotImplementedError):
            self.transaction._send_void_request()

    # 6. testing capture request
    def test_06_capture_not_supported(self):

        with self.assertRaises(NotImplementedError):
            self.transaction.send_capture_request()

    # 7. testing hash varification
    def test_07_payu_hash_verification_fails(self):

        data = {'hash': 'incorrecthash'}
        with self.assertRaises(ValidationError):
            self.transaction._payu_verify_return_sign(data)

    # 8. Testing handling of successful payment with discount and amount update


    def test_08_handle_successful_payment(self):
        data = {
            'status': 'success',
            'discount': '10',
            'net_amount_debit': '90',
            'udf1': str(self.transaction.id),
            'mihpayid': 'PAYU123',
            'hash': 'fakehash',
            'udf3': 'website',
        }

        # Patch request.env to return a MagicMock for 'sale.order'
        mock_env = MagicMock()
        mock_sale_order = MagicMock()
        mock_env.__getitem__.side_effect = lambda model: mock_sale_order if model == 'sale.order' else self.env[model]

        mock_request = MagicMock()
        mock_request.env = mock_env

        with patch.object(type(self.transaction), '_payu_verify_return_sign', return_value=True), \
            patch('odoo.addons.payment_payu.models.payment_transaction.request', mock_request), \
            patch.object(type(self.transaction), 'apply_global_discount_to_order', return_value=None):

            self.transaction._process_notification_data(data)
            self.assertEqual(self.transaction.state, 'done')
            self.assertEqual(self.transaction.amount, 90.0)

    # 9. Test failed status processing
    def test_09_failed_payment_sets_error(self):

        data = {'status': 'failure', 'error_Message': 'Declined', 'hash': 'fakehash'}

        # Patch on the class, not the instance
        with patch.object(type(self.transaction), '_payu_verify_return_sign', return_value=True):
            self.transaction._process_notification_data(data)
            self.assertEqual(self.transaction.state, 'error')

    # 10. Test refund response handling (success case)
    @patch('odoo.addons.payment_payu.models.payment_provider.PayUPaymentProvider._payu_make_request')
    def test_10_refund_success(self, mock_make_request):
        mock_make_request.return_value = {
            'status': 1,
            'error_code': 102,
            'mihpayid': 'REF123',
            'msg': 'Refund Successful'
        }
        refund_tx = self.transaction._send_refund_request(50.0)
        self.assertEqual(refund_tx.provider_reference, 'REF123')
        self.assertEqual(refund_tx.state, 'done')

    # 11. Test refund response handling (failure case)
    @patch('odoo.addons.payment_payu.models.payment_provider.PayUPaymentProvider._payu_make_request')
    def test_11_refund_failure(self, mock_make_request):
        mock_make_request.return_value = {
            'status': 0,
            'error_code': 101,
            'mihpayid': 'REF456',
            'msg': 'Insufficient balance'
        }
        refund_tx = self.transaction._send_refund_request(100.0)
        self.assertEqual(refund_tx.provider_reference, 'REF456')
        self.assertEqual(refund_tx.state, 'error')
    
    @patch.object(PaymentTransaction, '_get_specific_rendering_values', return_value={'some': 'value'})
    def test_get_specific_rendering_values_non_payu(self, mock_super):
        self.transaction.provider_code = 'other'
        result = self.transaction._get_specific_rendering_values({})
        self.assertEqual(result, {'some': 'value'})
    
    def test_get_payment_dns(self):
        provider = self.provider
        provider.state = 'test'
        self.assertEqual(self.transaction._get_payment_dns(provider), 'test.payu.in')

        # Use the correct production state, usually 'enabled' or 'prod' might not be accepted
        provider.state = 'enabled'  # or 'live' or '' depending on your module
        self.assertEqual(self.transaction._get_payment_dns(provider), 'secure.payu.in')
    
    def test_apply_discount_if_present_no_discount(self):
        data = {'discount': '0', 'udf1': '1', 'udf3': 'website'}
        self.transaction._apply_discount_if_present(data)

    def test_apply_discount_if_present_invoice_path(self):
        data = {'discount': '10', 'udf1': 'INV123', 'udf3': 'invoice'}
        mock_invoice = MagicMock()

        mock_env = MagicMock()
        # Return mock_invoice no matter if search is called on env['account.move'] or on sudo()
        mock_env.__getitem__.return_value.sudo.return_value.search.return_value = mock_invoice

        mock_request = MagicMock()
        mock_request.env = mock_env

        with patch('odoo.addons.payment_payu.models.payment_transaction.request', mock_request), \
            patch.object(type(self.transaction), 'apply_global_discount_to_invoice') as mock_apply:
            self.transaction._apply_discount_if_present(data)
            mock_apply.assert_called_with(ANY, 10.0)
            