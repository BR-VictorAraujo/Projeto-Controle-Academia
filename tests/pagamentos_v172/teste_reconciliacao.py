# -*- coding: utf-8 -*-
"""
Item 9 da v1.7.2: reconciliacao com a Cielo e motivo da recusa, com a API da
Cielo SIMULADA (unittest.mock). Nada chama a Cielo real.

Roda dentro do container web, no banco descartavel *_teste (o mesmo do
servidor_teste.py, que precisa estar no ar na porta 5001 para o cenario
"webhook e reconciliacao ao mesmo tempo"):

  docker compose exec -T -e DATABASE_URL=postgresql+psycopg://gymflow:gymflow@db:5432/gymflow_v172_teste \
      web python tests/pagamentos_v172/teste_reconciliacao.py
"""
import os
import sys
import random
import threading
from datetime import date, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if not os.environ.get('DATABASE_URL', '').endswith('_teste'):
    sys.exit('Recusado: DATABASE_URL precisa apontar para um banco *_teste')

import requests  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import teste_webhooks as tw  # noqa: E402

from app import create_app, db, cielo_client  # noqa: E402
from app.cielo_client import CieloClientError  # noqa: E402
from app.routes import pagamentos as pg  # noqa: E402

app = create_app()
app.config['CIELO_CLIENT_ID'] = 'teste'
app.config['CIELO_CLIENT_SECRET'] = 'teste'

conferir = tw.conferir
HOJE = date.today()

# respostas simuladas por link_pagamento_id
RESPOSTAS = {}
FORA_DO_AR = set()


def listar_falso(link_id, timeout=15):
    if link_id in FORA_DO_AR:
        raise CieloClientError('Falha de comunicacao com a Cielo: ConnectTimeout')
    return RESPOSTAS.get(link_id, {'orders': []})


def order(chave, dia, status='Paid', price=6000):
    iso = dia.strftime('%Y-%m-%dT03:45:43')
    return {'orderNumber': chave, 'createdDate': iso,
            'payment': {'price': price, 'numberOfPayments': 1, 'createdDate': iso, 'status': status}}


def cenario_com_link(venc):
    cen = tw.criar_cenario(venc)
    link = f'link-{cen["ass_id"]}'
    tw.sql("UPDATE assinatura_recorrente SET link_pagamento_id=%s WHERE id=%s", link, cen['ass_id'])
    cen['link'] = link
    return cen


def rodar():
    with app.app_context():
        with mock.patch.object(cielo_client, 'listar_pagamentos_link', side_effect=listar_falso):
            return pg.reconciliar_cielo()


def logs(cen, tipo=None):
    q = "SELECT tipo_evento, nivel, resultado FROM log_integracao_pagamento WHERE assinatura_recorrente_id=%s"
    args = [cen['ass_id']]
    if tipo:
        q += " AND tipo_evento=%s"
        args.append(tipo)
    return tw.sql(q + " ORDER BY id", *args)


def main():
    print('== 1) order Paid sem pagamento -> lanca 1 vez; rodar de novo nao duplica ==')
    cen = cenario_com_link(date(2026, 11, 10))
    k = tw.chave_nova()
    RESPOSTAS[cen['link']] = {'orders': [order(k, date(2026, 11, 11))]}
    c1 = rodar()
    c2 = rodar()
    e = tw.estado(cen)
    conferir(e['pags'] == 1 and e['venc'] == date(2026, 12, 11),
             f'1 pagamento, vencimento 11/12/2026 ({e}); contagens {c1} / {c2}')
    for r in logs(cen, 'pagamento_lancado'):
        print('     log:', r)

    print('== 2) order Paid com pagamento ja existente pela chave -> nada ==')
    cen = cenario_com_link(date(2026, 11, 10))
    k = tw.chave_nova()
    tw.post(tw.completo(cen, k))
    RESPOSTAS[cen['link']] = {'orders': [order(k, date(2026, 11, 11))]}
    rodar()
    e = tw.estado(cen)
    conferir(e['pags'] == 1, f'continua 1 pagamento ({e["pags"]})')

    print('== 3) order Paid + pagamento recorrente SEM chave de 17/11 -> WARNING (1 vez), nao lanca ==')
    cen = cenario_com_link(date(2026, 12, 11))
    tw.sql("INSERT INTO pagamentos (aluno_id, valor, forma_pagamento, status, data_vencimento, data_pagamento, "
           "criado_em, criado_por, origem, assinatura_recorrente_id, payment_id_gateway) VALUES "
           "(%s, 60, 'cartao_recorrente', 'pago', '2026-11-10', '2026-11-17', '2026-11-17 08:00', "
           "'cielo_webhook', 'recorrente_gateway', %s, NULL)", cen['aluno_id'], cen['ass_id'])
    k = tw.chave_nova()
    RESPOSTAS[cen['link']] = {'orders': [order(k, date(2026, 11, 11))]}
    rodar()
    rodar()
    e = tw.estado(cen)
    avisos = logs(cen, 'reconciliacao_conferir')
    conferir(e['pags'] == 1 and len(avisos) == 1, f'nao lancou ({e["pags"]} pagamento) e 1 WARNING ({len(avisos)})')
    for r in avisos:
        print('     log:', r)
    print('   3b) pagamento MANUAL pago do mesmo aluno em 12/11 -> WARNING, nao lanca')
    cen = cenario_com_link(date(2026, 12, 11))
    tw.sql("INSERT INTO pagamentos (aluno_id, valor, forma_pagamento, status, data_vencimento, data_pagamento, "
           "criado_em, criado_por, origem) VALUES "
           "(%s, 60, 'pix', 'pago', '2026-11-10', '2026-11-12', '2026-11-12 10:00', 'admin', 'manual')",
           cen['aluno_id'])
    RESPOSTAS[cen['link']] = {'orders': [order(tw.chave_nova(), date(2026, 11, 11))]}
    rodar()
    e = tw.estado(cen)
    conferir(e['pags'] == 0 and len(logs(cen, 'reconciliacao_conferir')) == 1,
             f'nao lancou ({e["pags"]}) e 1 WARNING')
    print('   3c) pagamento sem chave de 40 dias antes (fora da janela de 25) -> lanca')
    cen = cenario_com_link(date(2026, 11, 10))
    tw.sql("INSERT INTO pagamentos (aluno_id, valor, forma_pagamento, status, data_vencimento, data_pagamento, "
           "criado_em, criado_por, origem, assinatura_recorrente_id, payment_id_gateway) VALUES "
           "(%s, 60, 'cartao_recorrente', 'pago', '2026-10-01', '2026-10-01', '2026-10-01 08:00', "
           "'cielo_webhook', 'recorrente_gateway', %s, NULL)", cen['aluno_id'], cen['ass_id'])
    RESPOSTAS[cen['link']] = {'orders': [order(tw.chave_nova(), date(2026, 11, 11))]}
    rodar()
    conferir(tw.estado(cen)['pags'] == 2, 'lancou (2 pagamentos: o antigo + o recuperado)')

    print('== 4) webhook e reconciliacao ao mesmo tempo -> 1 pagamento (10 rodadas) ==')
    ruins = 0
    for _ in range(10):
        cen = cenario_com_link(date(2026, 11, 10))
        k = tw.chave_nova()
        RESPOSTAS[cen['link']] = {'orders': [order(k, date(2026, 11, 11))]}
        barreira = threading.Barrier(2)

        def via_webhook():
            barreira.wait()
            tw.post(tw.completo(cen, k))

        def via_job():
            barreira.wait()
            rodar()
        ts = [threading.Thread(target=via_webhook), threading.Thread(target=via_job)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        e = tw.estado(cen)
        if e['pags'] != 1 or e['venc'] != date(2026, 12, 11):
            ruins += 1
            print('    rodada ruim:', e)
        RESPOSTAS.pop(cen['link'])
    conferir(ruins == 0, f'10 rodadas com 1 pagamento (ruins={ruins})')

    print('== 5) order Denied (e Pending/Expired) -> nao lanca ==')
    cen = cenario_com_link(date(2026, 11, 10))
    RESPOSTAS[cen['link']] = {'orders': [order(tw.chave_nova(), date(2026, 11, 14), 'Denied'),
                                         order(tw.chave_nova(), date(2026, 11, 15), 'Pending'),
                                         order(tw.chave_nova(), date(2026, 11, 15), 'Expired')]}
    rodar()
    e = tw.estado(cen)
    conferir(e['pags'] == 0 and e['tent'] == 0, f'nada lancado nem contado ({e})')
    print('   5b) order Paid de 50 dias atras (fora dos 40) -> nao lanca')
    cen = cenario_com_link(date(2026, 9, 1))
    RESPOSTAS[cen['link']] = {'orders': [order(tw.chave_nova(), HOJE - timedelta(days=50))]}
    rodar()
    conferir(tw.estado(cen)['pags'] == 0, 'nao lancou')

    print('== 6) API fora -> ERROR, job segue para a proxima assinatura ==')
    ca = cenario_com_link(date(2026, 11, 10))
    cb = cenario_com_link(date(2026, 11, 10))
    FORA_DO_AR.add(ca['link'])
    RESPOSTAS[cb['link']] = {'orders': [order(tw.chave_nova(), date(2026, 11, 11))]}
    cont = rodar()
    erros = logs(ca, 'erro_comunicacao')
    conferir(cont['erros'] >= 1 and erros and erros[-1][1] == 'ERROR', f'ERROR registrado: {erros[-1] if erros else None}')
    conferir(tw.estado(cb)['pags'] == 1, 'a proxima assinatura foi lancada mesmo com a falha anterior')
    FORA_DO_AR.clear()
    print('   6b) cliente HTTP: 1 retentativa em erro de rede, timeout repassado')
    with app.app_context(), mock.patch.object(cielo_client, '_obter_token', return_value='tok'):
        resp_ok = mock.Mock(status_code=200)
        resp_ok.json.return_value = {'orders': []}
        with mock.patch.object(cielo_client.requests, 'get',
                               side_effect=[requests.exceptions.ConnectionError('x'), resp_ok]) as g:
            r = cielo_client.listar_pagamentos_link('abc')
            conferir(r == {'orders': []} and g.call_count == 2 and g.call_args.kwargs['timeout'] == 15,
                     f'2a tentativa funcionou (chamadas={g.call_count}, timeout={g.call_args.kwargs["timeout"]})')
        with mock.patch.object(cielo_client.requests, 'get',
                               side_effect=requests.exceptions.ConnectTimeout('x')) as g:
            try:
                cielo_client.listar_pagamentos_link('abc')
                conferir(False, 'deveria levantar CieloClientError')
            except CieloClientError:
                conferir(g.call_count == 2, f'2 tentativas e CieloClientError (chamadas={g.call_count})')

    print('== 7) recusa com errorCode 51 -> log com "saldo/limite insuficiente" ==')
    cliente = app.test_client()
    url = '/api/v1/pagamentos/webhooks/cielo'
    pedido_negado = {'orderNumber': 'x', 'payment': {'status': 'Denied', 'errorCode': '51', 'brand': 'Master',
                                                     'cardMaskedNumber': '550209******4177'}}
    cen = tw.criar_cenario(date(2026, 11, 10), tentativas=1)
    k = tw.chave_nova()
    with mock.patch.object(cielo_client, 'consultar_pedido', return_value=pedido_negado) as cp:
        r1 = cliente.post(url, json=tw.completo(cen, k, status='3', created='14/11/2026 05:07:51'))
        r2 = cliente.post(url, json=tw.curto(cen, k, status='3'))
    rec = logs(cen, 'tentativa_negada')
    conferir(r1.status_code == r2.status_code == 200 and tw.estado(cen)['tent'] == 2 and cp.call_count == 1
             and 'saldo/limite insuficiente' in rec[0][2], f'log: {rec[0][2] if rec else None}')
    print('   7b) consulta falha -> fica "motivo nao informado"')
    cen = tw.criar_cenario(date(2026, 11, 10))
    with mock.patch.object(cielo_client, 'consultar_pedido', side_effect=CieloClientError('timeout')):
        cliente.post(url, json=tw.completo(cen, tw.chave_nova(), status='3'))
    rec = logs(cen, 'tentativa_negada')
    conferir('motivo não informado pela Cielo no aviso' in rec[0][2], f'log: {rec[0][2]}')
    print('   7c) codigo fora da tabela -> "codigo X"')
    cen = tw.criar_cenario(date(2026, 11, 10))
    with mock.patch.object(cielo_client, 'consultar_pedido',
                           return_value={'payment': {'status': 'Denied', 'errorCode': 'AB'}}):
        cliente.post(url, json=tw.completo(cen, tw.chave_nova(), status='3'))
    conferir('código AB' in logs(cen, 'tentativa_negada')[0][2], logs(cen, 'tentativa_negada')[0][2])

    print()
    print('RESULTADO:', 'TUDO OK' if not tw.FALHAS else f'{len(tw.FALHAS)} FALHA(S)')
    return 1 if tw.FALHAS else 0


if __name__ == '__main__':
    sys.exit(main())
