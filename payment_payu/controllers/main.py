# payment_payu/controllers/main.py
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


PAYMENT_TRANSACTION_MODEL = 'payment.transaction'

class PayUController(http.Controller):
    _webhook_url = '/payment/payu/webhook'
    _process_url = '/payment/payu/process'
    _cancel_url = '/payment/payu/cancel'

    @http.route(_webhook_url, type='http', auth='public', methods=['POST'], csrf=False)
    def payu_webhook(self, **kwargs):
        _logger.info("PayU Webhook received: %s", kwargs)

        # Retrieve the transaction based on the reference included in the return url.
        tx_sudo = request.env[PAYMENT_TRANSACTION_MODEL].sudo()._get_tx_from_notification_data(
            'payu', kwargs
        )

        tx_sudo._handle_notification_data('payu', kwargs)

        return "Webhook processed"

    @http.route(_process_url, type='http', auth='public', methods=['POST'], csrf=False, save_session=False)
    def payu_process(self, **kwargs):
        _logger.info("PayU redirection response received: %s", kwargs)

        # Retrieve the transaction based on the reference included in the return url.
        tx_sudo = request.env[PAYMENT_TRANSACTION_MODEL].sudo()._get_tx_from_notification_data(
            'payu', kwargs
        )

        tx_sudo._handle_notification_data('payu', kwargs)

        return request.redirect('/payment/status')

    @http.route(_cancel_url, type='http', auth='public', methods=['GET', 'POST'], csrf=False)
    def payu_cancel(self, **kwargs):
        tx_ref = request.session.get('last_txnid')

        if tx_ref:
            tx = request.env[PAYMENT_TRANSACTION_MODEL].sudo().search([('reference', '=', tx_ref)], limit=1)
            if tx and tx.state not in ('done', 'cancel'):
                tx._set_canceled()

        return request.redirect('/payment/status')