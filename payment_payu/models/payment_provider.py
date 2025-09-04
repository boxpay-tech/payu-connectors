# -*- coding: utf-8 -*-
import hashlib
import logging
import pprint
import json
import re

import requests
from urllib.parse import parse_qsl

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError, RedirectWarning

from odoo.addons.payment_payu import const

_logger = logging.getLogger(__name__)

class PayUPaymentProvider(models.Model):
    _inherit = 'payment.provider'

    code = fields.Selection(
        selection_add=[('payu', 'PayU')], ondelete={'payu': 'set default'})
    
    
    payu_credential_ids = fields.One2many(
        'payu.credential', 'provider_id', string='PAYU CREDENTIALS'
    )
    
    #=== ACTION METHODS ===#

    def action_payu_signup_redirect(self):
        """ Redirect to the PayU OAuth URL.

        Note: `self.ensure_one()`

        :return: An URL action to redirect to the PayU OAuth URL.
        :rtype: dict
        """
        self.ensure_one()

        _logger.info("Initiating the sign up flow...")

        if self.company_id.currency_id.name not in const.SUPPORTED_CURRENCIES:
            raise RedirectWarning(
                _(
                    "PayU is not available in your country; please use another payment"
                    " provider."
                ),
                self.env.ref('payment.action_payment_provider').id,
                _("Other Payment Providers"),
            )

        signup_url = getattr(const, 'TEST_SIGN_UP_ENDPOINT' if self.state == 'test' else 'SIGN_UP_ENDPOINT')

        authorization_url = f'{signup_url}'
        return {
            'type': 'ir.actions.act_url',
            'url': authorization_url,
            'target': 'self',
        }
    
    #=== COMPUTE METHODS ===#

    def _compute_feature_support_fields(self):
        """ Override of `payment` to enable additional features. """
        super()._compute_feature_support_fields()
        self.filtered(lambda p: p.code == 'payu').update({
            'support_refund': 'partial',
        })

    # === BUSINESS METHODS - PAYMENT FLOW === #

    def _get_supported_currencies(self):
        """ Override of `payment` to return the supported currencies. """
        self.ensure_one()
        supported_currencies = super()._get_supported_currencies()
        
        if self.code == 'payu':
            supported_currencies = supported_currencies.filtered(
                lambda c: c.name in const.SUPPORTED_CURRENCIES
            )
        return supported_currencies

    def _get_default_payment_method_codes(self):
        """ Override of `payment` to return the default payment method codes. """
        default_codes = super()._get_default_payment_method_codes()
        _logger.info("Adding supported payment method for provider: %s, default codes: %s, target codes:%s ...", self.code, default_codes, const.DEFAULT_PAYMENT_METHOD_CODES)
        if self.code != 'payu':
            return default_codes
        return const.DEFAULT_PAYMENT_METHOD_CODES

    def _get_payu_urls(self):
        """ Return the PayU URL based on the provider's state. """
        self.ensure_one()
        if self.state == 'test':
            return {'payu_form_url': 'https://test.payu.in/_payment'}
        else:
            return {'payu_form_url': 'https://secure.payu.in/_payment'}


    def _payu_make_request(self, url, bearer_token = None, query_params = None, data = None, method = "POST"):
        """ Make a request to PayU API at the specified endpoint.

        Note: self.ensure_one()

        :param str endpoint: The endpoint to be reached by the request.
        :param dict data: The payload of the request.
        :param str method: The HTTP method of the request.
        :return The JSON-formatted content of the response.
        :rtype: dict
        :raise ValidationError: If an HTTP error occurs.
        """
        self.ensure_one()

        headers = {
            "Content-Type": "application/x-www-form-urlencoded"
        }

        if bearer_token:
            headers["Authorization"] = f'Bearer {bearer_token}'
        

        try:
            _logger.info("Url: %s, Params: %s, Data: %s", url, query_params, data)
            if method == "GET":
                response = requests.get(
                        url,
                        params=query_params,
                        headers=headers
                    )
            else:
                response = requests.post(
                        url,
                        params=query_params,
                        headers=headers,
                        data=data
                    )

            response.raise_for_status()

        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            _logger.exception("Unable to reach endpoint at %s", url)
            raise ValidationError(
                "PayU: " + _("Could not establish the connection to the API.")
            )

        except requests.exceptions.HTTPError:
            _logger.exception(
                "Invalid API request at %s with data:\n%s", url, pprint.pformat(data),
            )
            raise ValidationError(_(
                "PayU gave us the following information: '%s'",
                response.json()
            ))

        return json.loads(response.text)
    
    def _payu_generate_sign(self, hash_param_const_name, values, currency):
        """
        Generate the PayU signature (hash) based on currency-specific credentials.

        :param str hash_param_const_name: The constant name specifying hash param keys.
        :param dict values: The values used to generate the signature.
        :param res.currency currency: The currency record to select credentials for.
        :return: The generated signature as a hex string.
        :rtype: str
        """
        def safe_str(val):
            return str(val or '').strip()

        # Fetch credentials for the given currency
        credential = self.payu_credential_ids.filtered(lambda cred: cred.currency_id == currency)
        
        if not credential:
            raise ValidationError(_("No PayU credentials found for currency %s.") % currency.name)

        _logger.info(f"Fetched PayU credentials for currency {currency.name}: Merchant Key = {credential.merchant_key}, Merchant Salt = {credential.merchant_salt}");
        
        salt_value = credential.merchant_salt

        hash_param_keys = getattr(const, hash_param_const_name, [])

        hash_string_parts = []
        for hash_param in hash_param_keys:
            if hash_param == '_SALT_':
                hash_string_parts.append(salt_value)
            else:
                hash_string_parts.append(safe_str(values.get(hash_param)))

        hash_string = '|'.join(hash_string_parts)
        hash_string = re.sub(r'^[\s|]+|[\s|]+$', '', hash_string)

        _logger.info('Hash String for currency %s: %s', currency.name, hash_string)

        return hashlib.sha512(hash_string.encode('utf-8')).hexdigest()
    
    def action_save_payu_credentials(self):
        self.ensure_one()
        return True