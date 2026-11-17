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
import logging
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
from app.models import LogIntegracaoPagamento  # noqa: E402
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
        raise CieloClientError('Cielo retornou HTTP 404')   # erro so desse link: a rodada segue
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

    print('== 6) link com erro HTTP 404 -> ERROR, job segue para a proxima assinatura ==')
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

    ajustes_finais()

    print()
    print('RESULTADO:', 'TUDO OK' if not tw.FALHAS else f'{len(tw.FALHAS)} FALHA(S)')
    return 1 if tw.FALHAS else 0


class _Captura(logging.Handler):
    """Linhas ERROR que iriam para o servico.log durante a rodada."""
    def __init__(self):
        super().__init__(logging.ERROR)
        self.linhas = []

    def emit(self, record):
        self.linhas.append(record.getMessage())


def rodar_real(post=None, get=None):
    """Rodada com o cielo_client de verdade e o HTTP simulado (requests.post/get)."""
    cielo_client._token_cache.update(access_token=None, expira_em=0)
    cap = _Captura()
    with app.app_context():
        antes = db.session.query(db.func.coalesce(db.func.max(LogIntegracaoPagamento.id), 0)).scalar()
        db.session.rollback()
        app.logger.addHandler(cap)
        try:
            with mock.patch.object(cielo_client.requests, 'post', **(post or {})) as p, \
                    mock.patch.object(cielo_client.requests, 'get', **(get or {})) as g:
                cont = pg.reconciliar_cielo()
        finally:
            app.logger.removeHandler(cap)
    novos = tw.sql("SELECT tipo_evento, nivel, resultado FROM log_integracao_pagamento WHERE id>%s ORDER BY id", antes)
    return cont, p.call_count, g.call_count, novos, cap.linhas


def elegiveis():
    return tw.sql("SELECT count(*) FROM assinatura_recorrente WHERE gateway='cielo' AND link_pagamento_id IS NOT NULL "
                  "AND status IN ('ativa', 'bloqueada_inadimplencia')")[0][0]


def ajustes_finais():
    print('== 8) credencial recusada (token HTTP 401) com 3 assinaturas novas -> 1 POST, 1 ERROR, rodada interrompida ==')
    for _ in range(3):
        cenario_com_link(date(2026, 11, 10))
    resp_401 = mock.Mock(status_code=401, text='{"error":"invalid_client"}')
    cont, posts, gets, novos, arq = rodar_real(post={'return_value': resp_401})
    erros = [r for r in novos if r[1] == 'ERROR']
    conferir(posts == 1 and gets == 0, f'{elegiveis()} assinaturas elegiveis; POST de token={posts}, GET={gets}')
    conferir(len(erros) == 1 and erros[0][0] == 'erro_autenticacao'
             and erros[0][2].startswith('Reconciliação interrompida: falha de autenticação/comunicação com a Cielo'),
             f'1 ERROR na tela: {erros}')
    conferir(len(arq) == 1, f'1 ERROR no servico.log: {arq}')
    conferir(len([r for r in novos if r[0] == 'erro_autenticacao']) == 1, 'erro_autenticacao gravado uma vez so')

    print('   8b) rede fora no token (ConnectTimeout) -> 1 POST, 1 ERROR')
    cont, posts, gets, novos, arq = rodar_real(
        post={'side_effect': requests.exceptions.ConnectTimeout('timeout')})
    erros = [r for r in novos if r[1] == 'ERROR']
    conferir(posts == 1 and gets == 0 and len(erros) == 1 and len(arq) == 1,
             f'POST={posts}, GET={gets}, tela: {erros[0][2] if erros else None} | servico.log: {arq}')

    print('   8c) token ok, GET com timeout (2 tentativas) -> interrompe na 1a assinatura')
    tok = mock.Mock(status_code=200)
    tok.json.return_value = {'access_token': 'tok', 'expires_in': 1200}
    cont, posts, gets, novos, arq = rodar_real(
        post={'return_value': tok}, get={'side_effect': requests.exceptions.ReadTimeout('timeout')})
    erros = [r for r in novos if r[1] == 'ERROR']
    conferir(posts == 1 and gets == 2 and len(erros) == 1 and erros[0][0] == 'erro_comunicacao' and len(arq) == 1,
             f'POST={posts}, GET={gets}, tela: {erros[0][2] if erros else None} | servico.log: {arq}')

    print('   8d) token ok, GET 401 (token recusado) -> interrompe, cache do token descartado')
    r401 = mock.Mock(status_code=401)
    cont, posts, gets, novos, arq = rodar_real(post={'return_value': tok}, get={'return_value': r401})
    erros = [r for r in novos if r[1] == 'ERROR']
    conferir(gets == 1 and len(erros) == 1 and cielo_client._token_cache['access_token'] is None,
             f'GET={gets}, tela: {erros[0][2] if erros else None}')

    print('== 9) cobranca malformada no meio da lista -> as outras da mesma assinatura sao lancadas ==')
    cen = cenario_com_link(date(2026, 11, 10))
    k1, k2, k3 = tw.chave_nova(), tw.chave_nova(), tw.chave_nova()
    RESPOSTAS[cen['link']] = {'orders': [order(k1, date(2026, 11, 11)),
                                         'lixo',
                                         {'orderNumber': k2, 'payment': 'nao-e-objeto'},
                                         order(k3, date(2026, 11, 12))]}
    c = rodar()
    erros = logs(cen, 'erro_parsing')
    chaves = [r[0] for r in tw.sql("SELECT payment_id_gateway FROM pagamentos WHERE assinatura_recorrente_id=%s "
                                   "ORDER BY id", cen['ass_id'])]
    conferir(chaves == [k1, k3], f'1a e 4a cobrancas lancadas ({len(chaves)} pagamentos); contagens {c}')
    conferir(len(erros) == 2 and all(r[1] == 'ERROR' for r in erros), f'2 ERROR na tela ({len(erros)})')
    for r in erros:
        print('     log:', r)

    print('   9b) resposta da Cielo que nao e objeto (lista) -> ERROR na tela, segue para a proxima assinatura')
    ca, cb = cenario_com_link(date(2026, 11, 10)), cenario_com_link(date(2026, 11, 10))
    RESPOSTAS[ca['link']] = ['inesperado']
    RESPOSTAS[cb['link']] = {'orders': [order(tw.chave_nova(), date(2026, 11, 11))]}
    rodar()
    erros = logs(ca, 'erro_parsing')
    conferir(len(erros) == 1 and tw.estado(cb)['pags'] == 1, f'log: {erros[0][2] if erros else None}')

    print('== 10) reconciliacao: pagamento antigo numa assinatura bloqueada -> lanca sem reativar ==')
    cen = cenario_com_link(date(2026, 12, 11))
    tw.sql("UPDATE assinatura_recorrente SET status='bloqueada_inadimplencia', tentativas_negadas=4, "
           "data_ultima_negativa='2026-11-15 10:00' WHERE id=%s", cen['ass_id'])
    tw.sql("UPDATE alunos SET ativo=false WHERE id=%s", cen['aluno_id'])
    RESPOSTAS[cen['link']] = {'orders': [order(tw.chave_nova(), date(2026, 11, 11))]}
    rodar()
    e = tw.estado(cen)
    w = logs(cen, 'pagamento_lancado')
    dp = tw.sql("SELECT data_pagamento FROM pagamentos WHERE assinatura_recorrente_id=%s", cen['ass_id'])
    conferir(e['pags'] == 1 and e['status'] == 'bloqueada_inadimplencia' and e['ativo'] is False and e['tent'] == 4,
             f'nao reativou ({e})')
    conferir(w and w[0][1] == 'WARNING' and 'aluno continua bloqueado — conferir' in w[0][2],
             f'log: {w[0][2] if w else None}')
    conferir(dp == [(date(2026, 11, 11),)], f'data_pagamento = data da cobranca ({dp})')


if __name__ == '__main__':
    sys.exit(main())
