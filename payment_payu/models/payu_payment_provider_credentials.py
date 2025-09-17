from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

class PayUPaymentProviderCredential(models.Model):
    _name = 'payu.credential'
    _description = 'PayU Credential by Currency'

    provider_id = fields.Many2one('payment.provider', required=True, ondelete='cascade', string='Payment Provider')
    currency_id = fields.Many2one('res.currency', required=True, string='Currency')
    merchant_key = fields.Char('PayU Merchant Key', groups='base.group_system')
    merchant_salt = fields.Char('PayU Merchant Salt', groups='base.group_system')
    cross_border_transactions = fields.Boolean(string="Cross Border Transactions", default=False, help="Check the box if merchant account is enabled for corss border transactions")

    _sql_constraints = [
        ('uniq_provider_currency', 'unique(provider_id, currency_id)', 
         'You can only have one credential set per provider and currency.')
    ]

    @api.constrains('currency_id', 'merchant_key', 'merchant_salt')
    def _check_required_fields(self):
        for record in self:
            if not record.currency_id or not record.merchant_key or not record.merchant_salt:
                raise ValidationError(
                    _("All fields Currency, Merchant Key, and Merchant Salt must be filled in each PayU credential.")
                )
