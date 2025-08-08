# -*- coding: utf-8 -*-
import json

from requests.exceptions import HTTPError
from unittest.mock import patch, Mock

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
            'payu_merchant_key': 'test_key',
            'payu_merchant_salt': 'test_salt',
            'company_id': self.env.company.id,
        })

    # 1. Saving credentials
    def test_action_save_payu_credentials_success(self):
        self.provider.action_save_payu_credentials()
        self.assertTrue(self.provider.payu_credentials_saved)

    def test_action_save_payu_credentials_missing_values(self):
        
        self.provider.write({
            'payu_merchant_key': '',
            'payu_merchant_salt': '',
        })
        with self.assertRaises(ValidationError):
            self.provider.action_save_payu_credentials()

    # 2. Resetting account
    def test_action_payu_reset_account(self):
        
        self.provider.action_payu_reset_account()
        self.assertFalse(self.provider.payu_merchant_key)
        self.assertFalse(self.provider.payu_merchant_salt)
        self.assertFalse(self.provider.payu_credentials_saved)
        self.assertEqual(self.provider.state, 'disabled')
        self.assertFalse(self.provider.is_published)

    # 3. Signup redirect
    def test_action_payu_signup_redirect_test_mode(self):

        self.provider.company_id.currency_id.name = 'INR'
        result = self.provider.action_payu_signup_redirect()
        self.assertIn(const.TEST_SIGN_UP_ENDPOINT, result['url'])

    def test_action_payu_signup_redirect_invalid_currency(self):
        self.provider.company_id.currency_id.name = 'USD'  # Assume not supported
        if 'USD' in const.SUPPORTED_CURRENCIES:
            return  # Skip if test config includes USD
        with self.assertRaises(RedirectWarning):
            self.provider.action_payu_signup_redirect()

    # 4. URL selection
    def test_get_payu_urls_test_mode(self):
        
        self.provider.state = 'test'
        urls = self.provider._get_payu_urls()
        self.assertIn('test.payu.in', urls['payu_form_url'])

    def test_get_payu_urls_live_mode(self):
        self.provider.state = 'enabled'
        urls = self.provider._get_payu_urls()
        self.assertIn('secure.payu.in', urls['payu_form_url'])

    # 5. Credential checking
    def test_check_payu_credentials_success(self):

        self.provider._check_payu_credentials()  # Should not raise

    def test_check_payu_credentials_missing(self):

        self.provider.write({
            'payu_merchant_key': False,
            'payu_merchant_salt': False,
        })
        with self.assertRaises(ValidationError):
            self.provider._check_payu_credentials()

    # 6. Supported currencies
    def test_get_supported_currencies_filters(self):

        currency_obj = self.env['res.currency']
        inr = currency_obj.search([('name', '=', 'INR')])
        usd = currency_obj.search([('name', '=', 'USD')])
        supported = self.provider._get_supported_currencies()
        if inr:
            self.assertIn(inr, supported)
        if usd and 'USD' not in const.SUPPORTED_CURRENCIES:
            self.assertNotIn(usd, supported)

    # 7. Payment method codes
    def test_get_default_payment_method_codes(self):

        codes = self.provider._get_default_payment_method_codes()
        self.assertEqual(set(codes), set(const.DEFAULT_PAYMENT_METHOD_CODES))

    # 8. Signature generation
    def test_payu_generate_sign(self):

        values = {
            'key': 'test_key',
            'txnid': '12345',
            'amount': '100.00',
            'productinfo': 'Test Product',
            'firstname': 'John',
            'email': 'john@example.com',
        }
        const_name = 'PAYMENT_HASH_PARAMS'
        hash_val = self.provider._payu_generate_sign(const_name, values)
        self.assertIsInstance(hash_val, str)
        self.assertEqual(len(hash_val), 128)  # sha512 length

    # 9. API request (mocked)
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

