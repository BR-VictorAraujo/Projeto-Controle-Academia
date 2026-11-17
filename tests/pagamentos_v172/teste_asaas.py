# -*- coding: utf-8 -*-
"""
Webhook Asaas na v1.7.2 (trava, idempotencia, data de pagamento, recusa contada uma vez).
Banco descartavel *_teste, app em processo (test_client) com token de teste.

  docker compose exec -T -e DATABASE_URL=postgresql+psycopg://gymflow:gymflow@db:5432/gymflow_v172_teste \
      web python tests/pagamentos_v172/teste_asaas.py
"""
import os
import sys
import random
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if not os.environ.get('DATABASE_URL', '').endswith('_teste'):
    sys.exit('Recusado: DATABASE_URL precisa apontar para um banco *_teste')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import teste_webhooks as tw  # noqa: E402

from app import create_app  # noqa: E402

app = create_app()
app.config['ASAAS_WEBHOOK_TOKEN'] = 'token-teste'
conferir = tw.conferir
URL = '/api/v1/pagamentos/webhooks/asaas'
H = {'asaas-access-token': 'token-teste'}


def main():
    c = app.test_client()
    cen = tw.criar_cenario(date(2026, 11, 10))
    link, sub = f'lnk_{cen["ass_id"]}', f'sub_{random.getrandbits(40):x}'
    tw.sql("UPDATE assinatura_recorrente SET gateway='asaas', link_pagamento_id=%s WHERE id=%s", link, cen['ass_id'])
    pay_id = f'pay_{random.getrandbits(40):x}'
    evento = {'event': 'PAYMENT_CONFIRMED', 'payment': {
        'id': pay_id, 'subscription': sub, 'paymentLink': link, 'value': 60.0,
        'confirmedDate': '2026-11-12', 'dueDate': '2026-11-12',
        'creditCard': {'creditCardNumber': '8829', 'creditCardBrand': 'MASTERCARD'}}}
    r1 = c.post(URL, json=evento, headers=H)
    r2 = c.post(URL, json=evento, headers=H)
    e = tw.estado(cen)
    vinc = tw.sql("SELECT recurrent_payment_id FROM assinatura_recorrente WHERE id=%s", cen['ass_id'])[0][0]
    conferir(r1.status_code == r2.status_code == 200 and e['pags'] == 1, f'confirmado 2x -> 1 pagamento ({e["pags"]})')
    conferir(e['venc'] == date(2026, 12, 12), f'vencimento = confirmedDate + 1 mes ({e["venc"]})')
    conferir(vinc == sub, 'subscription vinculada na 1a confirmacao')

    neg = {'event': 'PAYMENT_OVERDUE', 'payment': {'id': f'pay_{random.getrandbits(40):x}', 'subscription': sub,
                                                   'value': 60.0, 'dueDate': '2026-12-12'}}
    c.post(URL, json=neg, headers=H)
    c.post(URL, json=neg, headers=H)
    conferir(tw.estado(cen)['tent'] == 1, f'OVERDUE 2x com o mesmo payment.id -> tentativas 1 ({tw.estado(cen)["tent"]})')
    r = c.post(URL, json=evento)
    conferir(r.status_code == 401, f'sem token -> 401 ({r.status_code})')
    for linha in tw.sql("SELECT nivel, resultado FROM log_integracao_pagamento WHERE assinatura_recorrente_id=%s "
                        "AND tipo_evento<>'webhook_recebido' ORDER BY id", cen['ass_id']):
        print('     log:', linha)
    print()
    print('RESULTADO:', 'TUDO OK' if not tw.FALHAS else f'{len(tw.FALHAS)} FALHA(S)')
    return 1 if tw.FALHAS else 0


if __name__ == '__main__':
    sys.exit(main())
