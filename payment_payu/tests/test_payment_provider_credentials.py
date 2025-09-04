# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase
from odoo.exceptions import ValidationError
from odoo import fields

class TestPayUPaymentProviderCredential(TransactionCase):

    def setUp(self):
        super().setUp()
        self.provider = self.env['payment.provider'].create({
            'name': 'PayU Unit Test Provider',
            'code': 'payu',
            'state': 'test',
            'company_id': self.env.company.id,
        })
        self.currency = self.env['res.currency'].search([('name', '=', 'INR')], limit=1)
        if not self.currency:
            self.currency = self.env['res.currency'].create({
                'name': 'INR',
                'symbol': '₹',
                'currency_unit_label': '₹',
                'rounding': 1,
                'decimal_places': 2,
            })

    def test_create_credential_success(self):
        """Should create a credential when all required fields are present."""
        cred = self.env['payu.credential'].create({
            'provider_id': self.provider.id,
            'currency_id': self.currency.id,
            'merchant_key': 'abc',
            'merchant_salt': 'def',
        })
        self.assertEqual(cred.provider_id, self.provider)
        self.assertEqual(cred.currency_id, self.currency)

    def test_create_credential_missing_currency(self):
        """Should raise IntegrityError if currency is missing (required=True at database level)."""
        with self.assertRaises(Exception):  # IntegrityError parent is Exception in Odoo unittest
            self.env['payu.credential'].create({
                'provider_id': self.provider.id,
                'merchant_key': 'abc',
                'merchant_salt': 'def',
            })

    def test_create_credential_missing_merchant_key(self):
        """Should raise ValidationError if merchant_key is missing."""
        with self.assertRaises(ValidationError):
            self.env['payu.credential'].create({
                'provider_id': self.provider.id,
                'currency_id': self.currency.id,
                'merchant_salt': 'def',
            })
    
    def test_create_credential_missing_merchant_salt(self):
        """Should raise ValidationError if merchant_salt is missing."""
        with self.assertRaises(ValidationError):
            self.env['payu.credential'].create({
                'provider_id': self.provider.id,
                'currency_id': self.currency.id,
                'merchant_key': 'abc',
            })

    def test_unique_provider_currency_constraint(self):
        """Should raise IntegrityError if duplicating provider/currency pair."""
        self.env['payu.credential'].create({
            'provider_id': self.provider.id,
            'currency_id': self.currency.id,
            'merchant_key': 'abc',
            'merchant_salt': 'def',
        })
        with self.assertRaises(Exception):  # Odoo wraps psycopg2.IntegrityError
            self.env['payu.credential'].create({
                'provider_id': self.provider.id,
                'currency_id': self.currency.id,
                'merchant_key': 'xyz',
                'merchant_salt': 'uvw',
            })
