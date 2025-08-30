from odoo import _, fields, models


class PayUPaymentProviderCredential(models.Model):
    _name = 'payu.credential'
    _description = 'PayU Credential by Currency'
    
    provider_id = fields.Many2one('payment.provider', required=True, ondelete='cascade', string='Payment Provider')
    currency_id = fields.Many2one('res.currency', required=True, string='Currency')
    merchant_key = fields.Char('PayU Merchant Key', groups='base.group_system')
    merchant_salt = fields.Char('PayU Merchant Salt', groups='base.group_system')

    _sql_constraints = [
        ('uniq_provider_currency', 'unique(provider_id, currency_id)', 
         'You can only have one credential set per provider and currency.')
    ]
