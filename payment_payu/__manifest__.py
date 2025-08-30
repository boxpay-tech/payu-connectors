# -*- coding: utf-8 -*-
{
    'name': 'PayU Payment Provider',
    'version': '1.0',
    'category': 'Accounting/Payment Providers',
    'sequence': 350,
    'summary': 'Payment Provider: PayU Integration',
    'description': "This module provides the integration of PayU as a payment provider in Odoo.",
    'icon': '/payment_payu/static/src/description/icon.svg',
    'author': 'PayU',
    'depends': ['payment'],
    'data': [
        'views/payment_payu_templates.xml',
        'views/payment_provider_views.xml',
        'data/payment_provider_data.xml',
        'security/ir.model.access.csv'
    ],
    'test': ['tests/test_payment_provider.py',
             'tests/test_payment_transaction.py'],
    'post_init_hook': 'post_init_hook',
    'uninstall_hook': 'uninstall_hook',
    'license': 'LGPL-3',
}