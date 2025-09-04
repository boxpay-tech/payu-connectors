# -*- coding: utf-8 -*-
import json

from requests.exceptions import HTTPError
from unittest.mock import patch, Mock, PropertyMock

from odoo.tests.common import TransactionCase
from odoo.exceptions import ValidationError, RedirectWarning
from odoo.addons.payment_payu import const

class TestPayUPaymentProvider(TransactionCase):

    def setUp(self):
        super().setUp()
        self.provider = self.env['payment.provider'].create({
            'name': 'PayU Test Provider',
            'code': 'payu',
            'state': 'test',
            'company_id': self.env.company.id,
        })
        # Add a credential for INR (and any supported currency used in tests)
        self.currency = self.env['res.currency'].search([('name','=','INR')], limit=1)
        self.credential = self.env['payu.credential'].create({
            'provider_id': self.provider.id,
            'currency_id': self.currency.id,
            'merchant_key': 'test_key',
            'merchant_salt': 'test_salt',
        })
    
    def test_action_save_payu_credentials_success(self):
        result = self.provider.action_save_payu_credentials()
        self.assertTrue(result)

    def test_action_payu_signup_redirect_test_mode(self):
        self.provider.company_id.currency_id = self.currency
        result = self.provider.action_payu_signup_redirect()
        self.assertIn(const.TEST_SIGN_UP_ENDPOINT, result['url'])

    def test_action_payu_signup_redirect_invalid_currency(self):
        with patch.object(type(self.provider.company_id.currency_id), "name", new_callable=PropertyMock) as mock_name:
            mock_name.return_value = "XXX"  # Not in SUPPORTED_CURRENCIES
            with self.assertRaises(RedirectWarning):
                self.provider.action_payu_signup_redirect()

    def test_get_payu_urls_test_mode(self):
        self.provider.state = 'test'
        urls = self.provider._get_payu_urls()
        self.assertIn('test.payu.in', urls['payu_form_url'])        

    def test_get_payu_urls_live_mode(self):
        self.provider.state = 'enabled'
        urls = self.provider._get_payu_urls()
        self.assertIn('secure.payu.in', urls['payu_form_url'])

    def test_get_supported_currencies_filters(self):
        supported = self.provider._get_supported_currencies()
        if self.currency:
            self.assertIn(self.currency, supported)
        usd = self.env['res.currency'].search([('name','=','USD')], limit=1)
        if usd and usd.name not in const.SUPPORTED_CURRENCIES:
            self.assertNotIn(usd, supported)

    def test_get_default_payment_method_codes(self):
        codes = self.provider._get_default_payment_method_codes()
        self.assertEqual(set(codes), set(const.DEFAULT_PAYMENT_METHOD_CODES))

    def test_payu_generate_sign(self):
        values = {
            'key': self.credential.merchant_key,
            'txnid': '12345',
            'amount': '100.00',
            'productinfo': 'Test Product',
            'firstname': 'John',
            'email': 'john@example.com',
        }
        const_name = 'PAYMENT_HASH_PARAMS'
        # Pass currency record
        hash_val = self.provider._payu_generate_sign(const_name, values, self.currency)
        self.assertIsInstance(hash_val, str)
        self.assertEqual(len(hash_val), 128)  # sha512 length

    @patch('odoo.addons.payment_payu.models.payment_provider.requests.post')
    def test_payu_make_post_request(self, mock_post):
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.text = json.dumps({'status': 'success'})
        mock_resp.raise_for_status = Mock()
        mock_post.return_value = mock_resp

        result = self.provider._payu_make_request(
            url='https://test.payu.in/_payment',
            data={'key': 'value'}
        )
        self.assertEqual(result['status'], 'success')

    @patch('odoo.addons.payment_payu.models.payment_provider.requests.get')
    def test_payu_make_get_request_with_token(self, mock_get):
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.text = json.dumps({'result': 'ok'})
        mock_resp.raise_for_status = Mock()
        mock_get.return_value = mock_resp

        result = self.provider._payu_make_request(
            url='https://test.payu.in/_payment',
            method='GET',
            query_params={'foo': 'bar'},
            bearer_token='dummy_token'
        )
        self.assertEqual(result['result'], 'ok')

    @patch('odoo.addons.payment_payu.models.payment_provider.requests.post')
    def test_payu_make_request_http_error(self, mock_post):
        mock_resp = Mock()
        mock_resp.raise_for_status.side_effect = HTTPError("HTTP Error")
        mock_resp.text = json.dumps({'status': 'fail'})
        mock_resp.json = lambda: {'status': 'fail'}
        mock_post.return_value = mock_resp

        with self.assertRaises(ValidationError):
            self.provider._payu_make_request(url='https://fail.url', data={})
