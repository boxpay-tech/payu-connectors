# -*- coding: utf-8 -*-
import logging
import pprint
import json
import uuid
from werkzeug.urls import url_join

import requests

from odoo import _, api, fields, models
from odoo.http import request
from odoo.exceptions import ValidationError
from odoo.tools.float_utils import float_round

from odoo.addons.payment_payu import const

_logger = logging.getLogger(__name__)

class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    refund_bank_reference = fields.Char(
        string="Refund Bank Reference",
        help="Bank reference number for the refund transaction"
    )

    is_refund = fields.Boolean(string="Is Refund", compute="_compute_is_refund")

    @api.depends('amount')
    def _compute_is_refund(self):
        for tx in self:
            tx.is_refund = tx.amount < 0

    def get_productinfo_string(self, order):
        product_names = [line.product_id.display_name for line in order.order_line]
        return ' '.join(product_names)

    def get_cart_details(self, order):
        sku_details = []

        for line in order.order_line:
            product = line.product_id
            sku_details.append({
                "sku_id": product.default_code or str(product.id),
                "sku_name": product.name,
                "amount_per_sku": f"{line.price_total:.2f}",
                "quantity": int(line.product_uom_qty),
                # You can attach specific offers per SKU here
                "offer_key": [],
                "offer_auto_apply": True 
            })

        cart_details = {
            "amount": float(order.amount_total),
            "items": int(sum(line.product_uom_qty for line in order.order_line)),
            "surcharges": 0,  # Fill as needed
            "pre_discount": float(order.amount_undiscounted),  # Fill as needed (e.g., coupon applied before PayU)
            "sku_details": sku_details
        }
        return json.dumps(cart_details)
    
    def get_invoice_cart_details(self, invoice):
        sku_details = []

        for line in invoice.invoice_line_ids:
            product = line.product_id
            sku_details.append({
                "sku_id": product.default_code or str(product.id),
                "sku_name": product.name,
                "amount_per_sku": f"{line.price_total:.2f}",
                "quantity": int(line.quantity),
                "offer_key": [],
                "offer_auto_apply": True
            })

        cart_details = {
            "amount": float(invoice.amount_total),
            "items": int(sum(line.quantity for line in invoice.invoice_line_ids)),
            "surcharges": 0,
            "pre_discount": float(invoice.amount_untaxed),
            "sku_details": sku_details
        }

        return json.dumps(cart_details)

    def _get_specific_rendering_values(self, processing_values):
        """ Override of payment to return a dict of payu-specific values used to render the redirect form.

        :param dict processing_values: The processing values of the transaction.
        :return: The dict of payu-specific rendering values.
        :rtype: dict
        """

        res = super()._get_specific_rendering_values(processing_values)
        if self.provider_code != 'payu':
            return res

        provider = self.provider_id
        base_url = provider.get_base_url()

        currency = self.currency_id

        # Fetch the PayU credential record for this provider and currency
        credential = self.env['payu.credential'].search([
            ('provider_id', '=', provider.id),
            ('currency_id', '=', currency.id)
        ], limit=1)
        

        if not credential:
            raise ValidationError(_("PayU: No credentials configured for currency %s.") % currency.name)

        payu_key = credential.merchant_key

        partner_id = processing_values.get('partner_id')
        if not partner_id:
            raise ValidationError("PayU: " + _("A customer is required to proceed with the payment."))

        billing_partner = self.env['res.partner'].browse(partner_id)

        required_fields = {
            'name': billing_partner.name,
            'email': billing_partner.email,
            'phone': billing_partner.phone,
        }
        missing_fields = [key for key, value in required_fields.items() if not value]
        if missing_fields:
            raise ValidationError(
                "PayU: " + _(
                    "The following details are missing from your contact information, but are required for this payment: %s",
                    ', '.join(missing_fields).title()
                )
            )

        if hasattr(request, 'website') and request.website:
            order = request.website.sale_get_order()
            trn_ref_id = order.id
            cart_details = self.get_cart_details(order)
            udf3 = 'website'
        else:
            invoice = self.invoice_ids and self.invoice_ids[0]
            trn_ref_id = invoice.name
            cart_details = self.get_invoice_cart_details(invoice)
            udf3 = 'invoice'

        curl = f'/payment/payu/cancel?txn_ref={self.reference}'

        payu_values = {
            'api_version': 14,
            'key': payu_key,
            'txnid': str(uuid.uuid4()),
            'amount': f"{self.amount:.2f}",
            'productinfo': 'Odoo product',
            'cart_details': cart_details,
            'firstname': billing_partner.name.split(' ')[0],
            'email': billing_partner.email,
            'user_token': billing_partner.email,
            'phone': billing_partner.phone,
            'surl': url_join(base_url, '/payment/payu/process'),
            'furl': url_join(base_url, '/payment/payu/process'),
            'curl': url_join(base_url, curl),
            'udf1': trn_ref_id, 'udf2': self.reference, 'udf3': udf3, 'udf4': '', 'udf5': 'odoo',
        }

        payu_values['hash'] = provider._payu_generate_sign('PAYMENT_HASH_PARAMS', payu_values, currency)

        payment_dns = self._get_payment_dns(provider)

        payu_values['action_url'] = f'https://{payment_dns}/_payment'

        _logger.debug(f"Prepared PayU payment values: {payu_values}")

        return payu_values

    def _get_payment_dns(self, provider):
        payment_dns = 'test.payu.in' if provider.state == 'test' else 'secure.payu.in'
        return payment_dns    

    def _get_tx_from_notification_data(self, provider_code, notification_data):
        """ Override of payment to find the transaction based on custom data.

        :param str provider_code: The code of the provider that handled the transaction
        :param dict notification_data: The notification feedback data
        :return: The transaction if found
        :rtype: recordset of `payment.transaction`
        :raise: ValidationError if the data match no transaction
        """
        tx = super()._get_tx_from_notification_data(provider_code, notification_data)
        if provider_code != 'payu' or len(tx) == 1:
            return tx

        reference = notification_data.get('udf2')
        if not reference: 
            raise ValidationError("PayU: " + _("Received data with a missing transaction identifier (udf2)."))

        tx = self.search([('reference', '=', reference), ('provider_code', '=', 'payu')])
        if not tx:
            raise ValidationError(
                "PayU: " + _("No transaction found matching reference %s.", reference)
            )
        return tx
    
    def apply_global_discount_to_invoice(self, invoice, discount_amount):
        """
        Apply a global discount to the invoice as a negative invoice line with zero tax.
        :param invoice: record of account.move (invoice)
        :param discount_amount: discount amount (float, positive)
        """
        # Find or create the discount product (make sure it's configured in Odoo)
        discount_product = self.env['product.product'].search([('name', '=', 'PG Discount')], limit=1)
        if not discount_product:
            discount_product = self.env['product.product'].create({
                'name': 'PG Discount',
                'type': 'service',
                'sale_ok': True,
                'list_price': 0.0,
            })

        # Remove previous discount lines to avoid duplicates
        previous_discount_lines = invoice.invoice_line_ids.filtered(lambda l: l.product_id == discount_product)
        previous_discount_lines.unlink()

        # Prepare zero taxes
        zero_taxes = self.env['account.tax']

        # Add negative invoice line for discount with no taxes
        self.env['account.move.line'].create({
            'move_id': invoice.id,
            'product_id': discount_product.id,
            'name': 'Global Discount',
            'quantity': 1,
            'price_unit': -abs(discount_amount),  # Negative value
            'tax_ids': [(6, 0, zero_taxes.ids)],  # No taxes applied
        })

    def apply_global_discount_to_order(self, sale_order, discount_amount):
        """
        Apply a global discount to the sale order as a negative order line with zero tax.
        :param sale_order: record of sale.order
        :param discount_amount: discount amount (float, positive)
        """
        # Find or create the discount product (make sure it's configured in Odoo)
        discount_product = self.env['product.product'].search([('name', '=', 'PG Discount')], limit=1)
        if not discount_product:
            discount_product = self.env['product.product'].create({
                'name': 'PG Discount',
                'type': 'service',
                'sale_ok': True,
                'list_price': 0.0,
            })
        
        # Remove previous discount lines to avoid duplicates
        previous_discount_lines = sale_order.order_line.filtered(lambda l: l.product_id == discount_product)
        previous_discount_lines.unlink()
        
        # Prepare zero taxes
        zero_taxes = self.env['account.tax']
        
        # Add negative order line for discount with no taxes
        sale_order.order_line.create({
            'order_id': sale_order.id,
            'product_id': discount_product.id,
            'name': 'Global Discount',
            'product_uom_qty': 1,
            'price_unit': -abs(discount_amount),  # Negative value
            'tax_id': [(6, 0, zero_taxes.ids)],    # No taxes applied
        })

        # Recompute order totals
        sale_order._compute_amounts()

    def send_capture_request(self, amount_to_capture=None):
        """
        Override of payment to capture the transaction.

        Since PayU does not support the capture 
        operation separately, we explicitly raise an error to indicate 
        that capture is not implemented or supported.
        """

        raise NotImplementedError("PayU does not support capture operations.")


    def _send_void_request(self, amount_to_void=None):
        """
        Override of payment to void the transaction.

        Since PayU does not support the void 
        operation separately, we explicitly raise an error to indicate 
        that void is not implemented or supported.
        """

        raise NotImplementedError("PayU does not support void operations.")



    def _send_refund_request(self, amount_to_refund=None):
        """ Override of `payment` to send a refund request to PayU.

        Note: self.ensure_one()

        :param float amount_to_refund: The amount to refund.
        :return: The refund transaction created to process the refund request.
        :rtype: recordset of `payment.transaction`
        """
        refund_tx = super()._send_refund_request(amount_to_refund=amount_to_refund)
        if self.provider_code != 'payu':
            return refund_tx

        provider = self.provider_id
        currency = self.currency_id
        
        # Fetch the PayU credential record for this provider and currency
        credential = self.env['payu.credential'].search([
            ('provider_id', '=', provider.id),
            ('currency_id', '=', currency.id)
        ], limit=1)
        
        if not credential:
            raise ValidationError(_("PayU: No credentials configured for currency %s.") % currency.name)

        values = {
            "key": credential.merchant_key,
            "command": "cancel_refund_transaction",
            "var1": self.provider_reference,
            "var2": refund_tx.reference,
            "var3": amount_to_refund,
        }

        hash = provider._payu_generate_sign("REFUND_HASH_PARAMS", values, currency)
        data = {**values, 'hash': hash}

        url_host = "test.payu.in" if provider.state == 'test' else "info.payu.in";
        url = f'https://{url_host}/merchant/postservice.php'

        query_params = {
            "form": "2"
        }

        refund_response = provider._payu_make_request(url, query_params=query_params, data=data)
        _logger.info('Refund Response: %s', json.dumps(refund_response, indent=2))

        if refund_response and refund_response['status'] == 1 and refund_response['error_code'] == 102:
            refund_tx._set_done()
            refund_tx.provider_reference = refund_response['mihpayid']        
            refund_tx.env.ref('payment.cron_post_process_payment_tx')._trigger()
        else:
            refund_tx.provider_reference = refund_response['mihpayid']
            refund_tx._set_error(_("Your refund failed. Reason: %s", refund_response['msg']))


        return refund_tx


    def _process_notification_data(self, data):
        """Override of payment to process the transaction based on custom data."""
        if self.provider_code != 'payu':
            return

        if data is None:
            self._set_canceled()
            return

        self._payu_verify_return_sign(data)
        self.provider_reference = data.get('mihpayid')

        status = data.get('status')
        if status == 'success':
            self._handle_success_status(data)
        elif status == 'failure':
            self._handle_failure_status(data)
        else:
            self._set_canceled()


    def _handle_success_status(self, data):
        if self.state in ('done',):
            return

        self._apply_discount_if_present(data)
        self._update_amount_if_present(data)
        self._set_done()

        # Call helper to generate invoice and post to PayU
        sale_order_id = data.get('udf1')
        if sale_order_id:
            self.generate_sales_order_pdf_and_post_to_payu(data)
        else:
            _logger.warning("Sale Order ID not found in payment data; cannot generate or post invoice.")
        
    def generate_sales_order_pdf_and_post_to_payu(self, data):
        sale_order_id = data.get('udf1')

        sale_order = self.env['sale.order'].browse(int(sale_order_id))
        if not sale_order.exists():
            _logger.warning(f"Sale order with ID {sale_order_id} not found.")
            return

        self.update_udf_invoice_id(data, sale_order)
        self.upload_invoice(data, sale_order)


    def update_udf_invoice_id(self, data, sale_order):
        txnid = data.get('txnid')
        invoiceid = sale_order.name

        provider = self.provider_id
        currency = self.currency_id

        # Fetch the PayU credential record for this provider and currency
        credential = self.env['payu.credential'].search([
            ('provider_id', '=', provider.id),
            ('currency_id', '=', currency.id)
        ], limit=1)

        values = {
            'key' : credential.merchant_key,
            'command' : 'udf_update',
            'var1' : txnid,
            'var6' : invoiceid
        }
        hash_ = provider._payu_generate_sign("UPDATE_INVOICE_ID_HASH_PARAMS", values, currency)
        data = {**values, 'hash' : hash_}

        url_host = "test.payu.in" if provider.state == 'test' else "info.payu.in"
        url = f'https://{url_host}/merchant/postservice.php'

        query_params = {
            "form" : "2"
        }

        invoice_update_response = provider._payu_make_request(url, query_params=query_params, data=data)
        _logger.info('Invoice id Update Response: %s', invoice_update_response)

    def upload_invoice(self, data, sale_order):
        provider = self.provider_id
        currency = self.currency_id

        # Fetch the PayU credential record for this provider and currency
        credential = self.env['payu.credential'].search([
            ('provider_id', '=', provider.id),
            ('currency_id', '=', currency.id)
        ], limit=1)

        # Base form values
        values = {
            'key': credential.merchant_key,
            'command': 'opgsp_upload_invoice_awb',
            'var1': data.get('mihpayid'),
            'var2': sale_order.name,
            'var3': 'Invoice',
            'invoice_id': sale_order.name,
        }

        # Render the PDF report content for the sale order
        report = self.env.ref('sale.action_report_saleorder')
        pdf_content, _ = report._render_qweb_pdf(report.id, res_ids=[sale_order.id])

        # Generate the signed hash for security
        hash_ = provider._payu_generate_sign("UPLOAD_INVOICE_HASH_PARAMS", values, currency)

        # Append the hash to the form values
        values['hash'] = hash_

        # Prepare the file dictionary with the invoice PDF
        files = {
            'file': (f'{sale_order.name}.pdf', pdf_content, 'application/pdf'),
        }

        url_host = "test.payu.in" if provider.state == 'test' else "info.payu.in"
        url = f'https://{url_host}/merchant/postservice.php'

        
        try:
            # Send form data and file as multipart/form-data
            response = requests.post(url, data=values, files=files, timeout=30)
            response.raise_for_status()
            _logger.info(f"Successfully posted sales order PDF {sale_order.name} to test endpoint.")
            _logger.info(f"Response status: {response.status_code}, body: {response.text}")

            message = response.get('responseMsg')

            sale_order.message_post(
                body = message,
                message_type="notification",
                subtype_xmlid="mail.mt_note"
            ) 
        except Exception as e:
            _logger.error(f"Error posting sales order PDF {sale_order.name} to test endpoint: {str(e)}")

    def _apply_discount_if_present(self, data):
        discount = float(data.get('discount', 0))
        if discount <= 0:
            return

        sale_order_id = data.get('udf1')
        if not sale_order_id:
            _logger.warning("Sale Order id not found in request session")
            return

        udf3 = data.get('udf3')

        if udf3 == 'website':
            sale_order = request.env['sale.order'].sudo().browse(int(sale_order_id))
            self.apply_global_discount_to_order(sale_order, discount)
        else:
            # Assuming invoice is linked by name/reference stored in udf1
            invoice = request.env['account.move'].sudo().search([('name', '=', sale_order_id)], limit=1)
            if not invoice:
                _logger.warning(f"Invoice {sale_order_id} not found in request session")
                return
            self.apply_global_discount_to_invoice(invoice, discount)


    def _update_amount_if_present(self, data):
        additional_charges = data.get('additionalCharges')
        net_amount_debit = data.get('net_amount_debit')

        # Consider additionalCharges as zero if missing or None or empty string
        additional_charges_value = float(additional_charges) if additional_charges not in (None, '', 'null') else 0.0

        if net_amount_debit:
            self.write({'amount': float(net_amount_debit) - additional_charges_value})


    def _handle_failure_status(self, data):
        error_message = data.get('error_Message', _("The payment was declined or failed."))
        self._set_error(_("Your payment failed. Reason: %s", error_message))


    def _payu_verify_return_sign(self, data):
        """ Verifies the hash value received in PayU response data

        Note: self.ensure_one()

        :param dict data: The custom data
        :return: None
        """

        returned_hash = data.get('hash')
        if not returned_hash: raise ValidationError(_("PayU: Received a response with no hash."))

        provider = self.provider_id
        currency = self.currency_id
        
        credential = self.env['payu.credential'].search([
            ('provider_id', '=', provider.id),
            ('currency_id', '=', currency.id)
        ], limit=1)
        
        _logger.error(f"Fetched PayU credentials for currency {currency.name}")
        
        sign_values = {**data, 'key': credential.merchant_key}

        calculated_hash = provider._payu_generate_sign("PAYMENT_REVERSE_HASH_PARAMS", sign_values, currency)

        if calculated_hash.lower() != returned_hash.lower():
            _logger.warning("PayU: Tampered payment notification for tx %s. Hash mismatch.", self.reference)
            raise ValidationError(_("PayU: The response hash does not match the expected hash. The data may have been tampered with."))