# -*- coding: utf-8 -*-
"""
Ajustes finais da v1.7.2 (webhooks), app em processo (test_client), banco
descartavel *_teste. Nada chama a Cielo real.

  docker compose exec -T -e DATABASE_URL=postgresql+psycopg://gymflow:gymflow@db:5432/gymflow_v172_teste \
      web python tests/pagamentos_v172/teste_ajustes.py

  1) reativacao indevida (pagamento antigo numa assinatura bloqueada)
  2) recusa em assinatura cancelada / expirada_cartao (Cielo e Asaas)
  3) data_pagamento = data da cobranca
  4) chave do pedido fora do padrao
  5) payload que nao e objeto JSON -> 400
  6) hide_parameters: erro de banco com CPF no payload nao vai para o servico.log
"""
import io
import json
import logging
import os
import random
import sys
from datetime import date
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if not os.environ.get('DATABASE_URL', '').endswith('_teste'):
    sys.exit('Recusado: DATABASE_URL precisa apontar para um banco *_teste')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import teste_webhooks as tw  # noqa: E402

import sqlalchemy as sa  # noqa: E402
from app import create_app, db, cielo_client  # noqa: E402
from app.cielo_client import CieloClientError  # noqa: E402

app = create_app()
app.config['CIELO_CLIENT_ID'] = None        # sem consulta do motivo da recusa (nada de HTTP)
app.config['CIELO_CLIENT_SECRET'] = None
app.config['ASAAS_WEBHOOK_TOKEN'] = 'token-teste'
app.config['PROPAGATE_EXCEPTIONS'] = False   # erro 500 vira resposta (como em producao)
conferir = tw.conferir
URL_CIELO = '/api/v1/pagamentos/webhooks/cielo'
URL_ASAAS = '/api/v1/pagamentos/webhooks/asaas'
H_ASAAS = {'asaas-access-token': 'token-teste'}
HOJE = date.today()
CPF = '52998224725'


def logs(cen, extra=''):
    return tw.sql("SELECT tipo_evento, nivel, resultado FROM log_integracao_pagamento "
                  "WHERE assinatura_recorrente_id=%s " + extra + " ORDER BY id", cen['ass_id'])


def data_pag(cen):
    return [r[0] for r in tw.sql("SELECT data_pagamento FROM pagamentos WHERE assinatura_recorrente_id=%s "
                                 "ORDER BY id", cen['ass_id'])]


def max_log_id():
    return tw.sql("SELECT coalesce(max(id), 0) FROM log_integracao_pagamento")[0][0]


def bloquear(cen, ultima_negativa, tentativas=4, status='bloqueada_inadimplencia'):
    tw.sql("UPDATE assinatura_recorrente SET status=%s, tentativas_negadas=%s, data_ultima_negativa=%s "
           "WHERE id=%s", status, tentativas, ultima_negativa, cen['ass_id'])
    tw.sql("UPDATE alunos SET ativo=false WHERE id=%s", cen['aluno_id'])


def asaas_cenario(venc, status='ativa', tentativas=0):
    cen = tw.criar_cenario(venc, status=status, tentativas=tentativas)
    cen['sub'] = f'sub_{random.getrandbits(40):x}'
    tw.sql("UPDATE assinatura_recorrente SET gateway='asaas', recurrent_payment_id=%s WHERE id=%s",
           cen['sub'], cen['ass_id'])
    return cen


def main():
    c = app.test_client()

    print('== 1) reativacao indevida ==')
    print('   1a) bloqueada (4 recusas, ultima em 15/11), aviso Cielo de cobranca de 11/11 (anterior a ultima recusa)')
    cen = tw.criar_cenario(date(2026, 12, 11))
    bloquear(cen, '2026-11-15 10:00:00')
    r = c.post(URL_CIELO, json=tw.completo(cen, tw.chave_nova(), created='11/11/2026 03:45:43'))
    e = tw.estado(cen)
    conferir(r.status_code == 200 and e['pags'] == 1 and e['status'] == 'bloqueada_inadimplencia'
             and e['ativo'] is False and e['tent'] == 4 and e['venc'] == date(2027, 1, 11),  # regra 1.7.2: 11/12 + 1 mes
             f'lancado sem reativar ({e})')
    w = logs(cen, "AND tipo_evento='pagamento_lancado'")
    conferir(len(w) == 1 and w[0][1] == 'WARNING'
             and w[0][2].startswith('Pagamento antigo lançado; aluno continua bloqueado — conferir'),
             f'WARNING: {w[0][2] if w else None}')
    print('   1b) bloqueada, cobranca de 16/11 (depois da ultima recusa) e vencimento a frente -> reativa')
    cen = tw.criar_cenario(date(2027, 1, 20))
    bloquear(cen, '2026-11-15 10:00:00')
    c.post(URL_CIELO, json=tw.completo(cen, tw.chave_nova(), created='16/11/2026 03:45:43'))
    e = tw.estado(cen)
    conferir(e['status'] == 'ativa' and e['ativo'] is True and e['tent'] == 0 and e['venc'] == date(2027, 2, 20),
             f'reativou; vencimento 20/01 + 1 mes = 20/02/2027 (regra 1.7.2) ({e})')
    # 1.7.2: avancar o vencimento nao e mais criterio (com a regra nova ele sempre avanca);
    # so reativa cobranca posterior a ultima recusa.
    print('   1c) bloqueada, cobranca de 11/11 (antes da recusa de 15/11) que avanca o vencimento (10/11 -> 11/12) -> NAO reativa')
    cen = tw.criar_cenario(date(2026, 11, 10))
    bloquear(cen, '2026-11-15 10:00:00')
    c.post(URL_CIELO, json=tw.completo(cen, tw.chave_nova(), created='11/11/2026 03:45:43'))
    e = tw.estado(cen)
    w = logs(cen, "AND tipo_evento='pagamento_lancado'")
    conferir(e['status'] == 'bloqueada_inadimplencia' and e['ativo'] is False and e['tent'] == 4
             and e['venc'] == date(2026, 12, 11) and e['pags'] == 1 and w and w[0][1] == 'WARNING',
             f'lancado sem reativar, vencimento avancou ({e}); log: {w[0][2] if w else None}')
    print('   1d) ativa com 2 recusas (ultima 15/11), pagamento antigo de 11/11 -> recusas mantidas, WARNING')
    cen = tw.criar_cenario(date(2026, 12, 11), tentativas=2)
    tw.sql("UPDATE assinatura_recorrente SET data_ultima_negativa='2026-11-15 10:00' WHERE id=%s", cen['ass_id'])
    c.post(URL_CIELO, json=tw.completo(cen, tw.chave_nova(), created='11/11/2026 03:45:43'))
    e = tw.estado(cen)
    w = logs(cen, "AND tipo_evento='pagamento_lancado'")
    conferir(e['tent'] == 2 and e['status'] == 'ativa' and e['pags'] == 1 and w and w[0][1] == 'WARNING',
             f'{e}; log: {w[0][2] if w else None}')
    print('   1e) Asaas: bloqueada, confirmedDate 2026-11-11 antes da ultima recusa -> nao reativa')
    cen = asaas_cenario(date(2026, 12, 11))
    bloquear(cen, '2026-11-15 10:00:00')
    ev = {'event': 'PAYMENT_CONFIRMED', 'payment': {'id': f'pay_{random.getrandbits(40):x}', 'subscription': cen['sub'],
                                                    'value': 60.0, 'confirmedDate': '2026-11-11'}}
    c.post(URL_ASAAS, json=ev, headers=H_ASAAS)
    e = tw.estado(cen)
    conferir(e['pags'] == 1 and e['status'] == 'bloqueada_inadimplencia' and e['ativo'] is False,
             f'Asaas: lancado sem reativar ({e})')

    print('== 2) recusa em assinatura encerrada ==')
    for status in ('cancelada', 'expirada_cartao'):
        cen = tw.criar_cenario(date(2026, 12, 11), status=status, tentativas=3)
        with mock.patch.object(cielo_client, 'consultar_pedido') as cp:
            r = c.post(URL_CIELO, json=tw.completo(cen, tw.chave_nova(), status='3', created='14/11/2026 05:00:12'))
        e = tw.estado(cen)
        ign = logs(cen, "AND resultado LIKE 'Recusa ignorada:%%'")
        conferir(r.status_code == 200 and e['tent'] == 3 and e['status'] == status and e['ativo'] is True
                 and len(logs(cen, "AND tipo_evento='tentativa_negada'")) == 0 and cp.call_count == 0,
                 f'Cielo {status}: 4a recusa nao contada nem bloqueia ({e})')
        conferir(len(ign) == 1, f'log: {ign[0][1:] if ign else None}')
    for evento in ('PAYMENT_DELETED', 'PAYMENT_OVERDUE', 'PAYMENT_REFUSED'):
        cen = asaas_cenario(date(2026, 12, 11), status='cancelada', tentativas=3)
        ev = {'event': evento, 'payment': {'id': f'pay_{random.getrandbits(40):x}', 'subscription': cen['sub'],
                                           'value': 60.0, 'dueDate': '2026-11-14'}}
        r = c.post(URL_ASAAS, json=ev, headers=H_ASAAS)
        e = tw.estado(cen)
        ign = logs(cen, "AND resultado LIKE 'Recusa ignorada: assinatura cancelada%%'")
        conferir(r.status_code == 200 and e['tent'] == 3 and e['ativo'] is True and len(ign) == 1,
                 f'Asaas {evento} em cancelada: nao contada ({e})')
    print('   log:', ign[0][1:])

    print('== 3) data_pagamento = data da cobranca ==')
    cen = tw.criar_cenario(date(2026, 11, 10))
    c.post(URL_CIELO, json=tw.completo(cen, tw.chave_nova(), created='11/11/2026 03:45:43'))
    conferir(data_pag(cen) == [date(2026, 11, 11)], f'Cielo completo created 11/11 -> {data_pag(cen)}')
    cen = tw.criar_cenario(date(2026, 11, 10))
    c.post(URL_CIELO, json=tw.curto(cen, tw.chave_nova()))
    conferir(data_pag(cen) == [HOJE], f'Cielo curto sem created_date -> hoje {data_pag(cen)}')
    cen = asaas_cenario(date(2026, 11, 10))
    ev = {'event': 'PAYMENT_CONFIRMED', 'payment': {'id': f'pay_{random.getrandbits(40):x}', 'subscription': cen['sub'],
                                                    'value': 60.0, 'confirmedDate': '2026-11-12'}}
    c.post(URL_ASAAS, json=ev, headers=H_ASAAS)
    conferir(data_pag(cen) == [date(2026, 11, 12)], f'Asaas confirmedDate 2026-11-12 -> {data_pag(cen)}')

    print('== 4) chave do pedido fora do padrao -> ERROR, 200, nada lancado ==')
    invalidas = {'19 hex': 'a' * 19, '33 hex': 'b' * 33, '21 hex': 'c' * 21, 'nao hex': 'z' * 32,
                 'caminho': '../../v1/products/x', 'com espaco no meio': 'e' * 16 + ' ' + 'f' * 15,
                 'hex + barra': 'a' * 20 + '/x'}
    for nome, chave in invalidas.items():
        cen = tw.criar_cenario(date(2026, 11, 10))
        r = c.post(URL_CIELO, json=tw.completo(cen, chave))
        e = tw.estado(cen)
        erro = tw.sql("SELECT resultado FROM log_integracao_pagamento WHERE nivel='ERROR' "
                      "AND payload_bruto->>'order_number'=%s ORDER BY id DESC LIMIT 1", cen['order'])
        conferir(r.status_code == 200 and e['pags'] == 0 and e['venc'] == date(2026, 11, 10)
                 and erro and 'fora do padrão' in erro[0][0], f'{nome}: HTTP {r.status_code}, pags={e["pags"]}')
    print('   log:', erro[0][0])
    for nome, chave in (('20 hex maiusculo', '%020X' % random.getrandbits(80)), ('32 hex', tw.chave_nova())):
        cen = tw.criar_cenario(date(2026, 11, 10))
        c.post(URL_CIELO, json=tw.completo(cen, chave))
        conferir(tw.estado(cen)['pags'] == 1, f'{nome}: aceita (1 pagamento)')
    with app.app_context(), mock.patch.object(cielo_client.requests, 'get') as g, \
            mock.patch.object(cielo_client, '_obter_token', return_value='tok'):
        try:
            cielo_client.consultar_pedido('../../v1/products/x')
            conferir(False, 'consultar_pedido deveria recusar')
        except CieloClientError:
            conferir(g.call_count == 0, 'consultar_pedido com chave invalida: recusado sem HTTP')

    print('== 5) payload que nao e objeto JSON -> 400 + ERROR (sem 500) ==')
    antes = max_log_id()
    casos = [
        ('lista', dict(json=[{'order_number': 'assin1', 'payment_status': '2'}])),
        ('texto JSON', dict(data='"abc"', content_type='application/json')),
        ('numero JSON', dict(data='123', content_type='application/json')),
        ('vazio JSON', dict(data='', content_type='application/json')),
        ('JSON invalido', dict(data='{"a": ', content_type='application/json')),
        ('objeto vazio', dict(json={})),
        ('form vazio', dict(data='', content_type='application/x-www-form-urlencoded')),
    ]
    for nome, kw in casos:
        r = c.post(URL_CIELO, **kw)
        conferir(r.status_code == 400, f'Cielo {nome}: HTTP {r.status_code}')
    r = c.post(URL_ASAAS, json=[{'event': 'PAYMENT_CONFIRMED'}], headers=H_ASAAS)
    conferir(r.status_code == 400, f'Asaas lista: HTTP {r.status_code}')
    erros = tw.sql("SELECT nivel, resultado FROM log_integracao_pagamento WHERE id>%s AND tipo_evento='erro_parsing' "
                   "ORDER BY id", antes)
    conferir(len(erros) == len(casos) + 1 and all(n == 'ERROR' for n, _ in erros),
             f'{len(erros)} logs ERROR (esperado {len(casos) + 1})')
    print('   log:', erros[0][1])
    cen = tw.criar_cenario(date(2026, 11, 10))
    r = c.post(URL_CIELO, data=tw.curto(cen, tw.chave_nova()))   # form-urlencoded valido continua aceito
    conferir(r.status_code == 200 and tw.estado(cen)['pags'] == 1, f'form-urlencoded valido: HTTP {r.status_code}, 1 pagamento')

    print('== 6) hide_parameters ==')
    url = app.config['SQLALCHEMY_DATABASE_URI']
    insert = sa.text("INSERT INTO log_integracao_pagamento (tipo_evento, nivel, payment_id_gateway, payload_bruto, "
                     "criado_em) VALUES ('teste', 'INFO', :k, CAST(:p AS json), now())")
    params = {'k': 'x' * 60, 'p': json.dumps({'customer_identity': CPF, 'customer_name': 'Fulana de Tal'})}
    msgs = {}
    for nome, opcoes in (('sem hide_parameters', {}), ('com hide_parameters', {'hide_parameters': True})):
        eng = sa.create_engine(url, **opcoes)
        try:
            with eng.begin() as con:
                con.execute(insert, params)
            msgs[nome] = '(sem erro)'
        except Exception as e:
            msgs[nome] = str(e)
        eng.dispose()
    with app.app_context():
        try:
            db.session.execute(insert, params)
            db.session.commit()
            msgs['engine do app'] = '(sem erro)'
        except Exception as e:
            db.session.rollback()
            msgs['engine do app'] = str(e)
    conferir(CPF in msgs['sem hide_parameters'], 'controle: sem hide_parameters o CPF aparece na mensagem do erro')
    conferir(CPF not in msgs['engine do app'] and 'hide_parameters=True' in msgs['engine do app'],
             'engine do app (config.py): mensagem sem os parametros -> '
             + msgs['engine do app'].splitlines()[-1][:120])

    # Ponta a ponta: servico.log (mesmo formato do serve.py) durante um aviso que
    # quebra no banco (pagador_recurrent_payment_id com 60 caracteres -> 500)
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(logging.Formatter('%(asctime)s %(levelname)s [%(name)s] %(message)s'))
    raiz = logging.getLogger()
    raiz.addHandler(h)
    raiz.setLevel(logging.INFO)
    try:
        cen = tw.criar_cenario(date(2026, 11, 10))
        p = tw.completo(cen, tw.chave_nova(), rpid='r' * 60,
                        extra={'customer_identity': CPF, 'customer_name': 'Fulana de Tal',
                               'customer_email': 'fulana@example.com'})
        r = c.post(URL_CIELO, json=p)
    finally:
        raiz.removeHandler(h)
    texto = buf.getvalue()
    conferir(r.status_code == 500, f'aviso com erro de banco: HTTP {r.status_code}')
    conferir(CPF not in texto and 'Fulana' not in texto and 'fulana@' not in texto,
             'servico.log sem CPF, nome e e-mail')
    conferir('Falha ao gravar log_integracao_pagamento: DataError' in texto
             or 'Falha ao gravar log_integracao_pagamento: StringDataRightTruncation' in texto,
             'falha do _log registrada so com o tipo do erro')
    print('   servico.log (linhas de erro):')
    for linha in texto.splitlines():
        if ' ERROR ' in linha or 'SQL parameters' in linha or linha.startswith('sqlalchemy') or linha.startswith('psycopg'):
            print('     ' + linha[:200])

    print()
    print('RESULTADO:', 'TUDO OK' if not tw.FALHAS else f'{len(tw.FALHAS)} FALHA(S)')
    return 1 if tw.FALHAS else 0


if __name__ == '__main__':
    sys.exit(main())
