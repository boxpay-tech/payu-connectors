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

    @http.route(_cancel_url, type='http', auth='public', methods=['GET', 'POST'], csrf=False, save_session=False)
    def payu_cancel(self, **kwargs):
        """Cancel the transaction only if it is in a non-terminal state."""

        txn_ref = kwargs.get('txn_ref')
        
        TERMINAL_STATES = ('done', 'cancel', 'error', 'authorized')

        if not txn_ref:
            _logger.warning("PayU Cancel URL missing txn_ref parameter.")
            return request.redirect('/payment/status')

        tx = request.env[PAYMENT_TRANSACTION_MODEL].sudo().search(
            [('reference', '=', txn_ref)], limit=1
        )

        if not tx:
            _logger.warning("No transaction found for reference %s", txn_ref)
            return request.redirect('/payment/status')

        if tx.state in TERMINAL_STATES:
            _logger.info(
                "Transaction %s is in terminal state '%s'. Cancel skipped.",
                txn_ref, tx.state
            )
            return request.redirect('/payment/status')

        # Only cancel if in non-terminal state
        _logger.info("Canceling transaction %s (current state: %s)", txn_ref, tx.state)
        tx._set_canceled()
        return request.redirect('/payment/status')