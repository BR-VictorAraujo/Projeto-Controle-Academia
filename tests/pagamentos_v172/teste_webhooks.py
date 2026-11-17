# -*- coding: utf-8 -*-
"""
Testes manuais da v1.7.2 (webhooks de pagamento). Rodam DENTRO do container web,
contra um servidor waitress na porta 5001 apontando para um banco descartavel
cujo nome termina em _teste (ver README.md desta pasta).

Uso:
  python tests/pagamentos_v172/teste_webhooks.py <modo>

Modos:
  conexao_morta   mata as conexoes do app no Postgres (pg_terminate_backend) e manda avisos
  apos_restart    so manda 1 aviso e confere (usar logo depois de 'docker compose restart db')
  cenarios        a, b, c, d, e, vencimento (item 7), frases (item 8), integrity
"""

import os
import sys
import json
import random
import threading
from datetime import date

import psycopg
import requests

BANCO = os.environ.get('BANCO_TESTE', 'gymflow_v172_teste')
if not BANCO.endswith('_teste'):
    sys.exit('Recusado: o banco precisa terminar em _teste')
DSN = f'postgresql://gymflow:gymflow@db:5432/{BANCO}'
BASE = os.environ.get('BASE_URL', 'http://localhost:5001')
URL_CIELO = BASE + '/api/v1/pagamentos/webhooks/cielo'

FALHAS = []


def conferir(cond, msg):
    print(('  [OK]   ' if cond else '  [FALHA] ') + msg)
    if not cond:
        FALHAS.append(msg)


def con():
    return psycopg.connect(DSN, autocommit=True, application_name='teste_v172')


def sql(q, *args):
    with con() as c:
        cur = c.execute(q, args)
        try:
            return cur.fetchall()
        except psycopg.ProgrammingError:
            return None


def criar_cenario(vencimento, status='ativa', tentativas=0, rpid=None):
    """Aluno + assinatura Cielo de teste. Retorna dict com ids e order_number."""
    n = random.randint(10**9, 10**10 - 1)
    order = f'assin{n}'
    plano_id = sql("SELECT id FROM planos WHERE nome='Mensal'")[0][0]
    aluno_id = sql(
        "INSERT INTO alunos (nome, tipo_documento, documento, plano, vencimento, ativo, "
        "biometria_status, biometria_2_status, criado_em) VALUES "
        "(%s, 'cpf', %s, 'Mensal', %s, true, 'pendente', 'pendente', now()) RETURNING id",
        f'Aluno Teste V172 {n}', str(n), vencimento)[0][0]
    ass_id = sql(
        "INSERT INTO assinatura_recorrente (aluno_id, plano_id, gateway, order_number, status, "
        "valor_mensal, data_inicio, tentativas_negadas, recurrent_payment_id, criado_em) VALUES "
        "(%s, %s, 'cielo', %s, %s, 60.00, %s, %s, %s, now()) RETURNING id",
        aluno_id, plano_id, order, status, vencimento, tentativas, rpid)[0][0]
    return {'aluno_id': aluno_id, 'ass_id': ass_id, 'order': order, 'rpid': rpid}


def chave_nova():
    return '%032x' % random.getrandbits(128)


def completo(cen, chave, status='2', created='11/11/2026 03:45:43',
             campo='meucheckout_order_number', rpid=None, extra=None):
    p = {"nsu": "051002", "tid": "28769360595NP9TK55HE", "amount": "6000",
         "end_date": "08/10/2027 00:00:00", "interval": "Mensal", "product_id": "link-teste",
         "start_date": "09/11/2026 11:28:30", "created_date": created,
         "order_number": cen['order'], "product_type": "5", "shipping_type": "5",
         "payment_status": status, "shipping_price": "0", "recurrent_status": "Ativa",
         "test_transaction": "False", "authorization_code": "011173",
         "payment_method_type": "1",
         "product_description": "Cobranca recorrente de mensalidade - GymFlow",
         "payment_installments": "1", "payment_method_brand": "1",
         "customer_identity_type": "0", campo: chave,
         "payment_maskedcreditcard": "415275******2128",
         "pagador_recurrent_payment_id": rpid or cen.get('rpid') or 'ec6f46ce-0000-0000-0000-%012d' % cen['ass_id']}
    if created is None:
        p.pop('created_date')
    if status != '2':
        p.pop('authorization_code')
    if extra:
        p.update(extra)
    return p


def curto(cen, chave, status='2', campo='meucheckout_order_number', rpid=None):
    p = {"nsu": "051002", "brand": "Undefined", "amount": "6000", "order_number": cen['order'],
         "payment_status": status, "test_transaction": "False", "authorization_code": "011173",
         campo: chave,
         "pagador_recurrent_payment_id": rpid or cen.get('rpid') or 'ec6f46ce-0000-0000-0000-%012d' % cen['ass_id']}
    if status != '2':
        p.pop('authorization_code')
    return p


def post(payload):
    r = requests.post(URL_CIELO, json=payload, timeout=30)
    return r.status_code


def estado(cen):
    venc, ativo = sql("SELECT vencimento, ativo FROM alunos WHERE id=%s", cen['aluno_id'])[0]
    st, tent = sql("SELECT status, tentativas_negadas FROM assinatura_recorrente WHERE id=%s", cen['ass_id'])[0]
    pags = sql("SELECT count(*) FROM pagamentos WHERE assinatura_recorrente_id=%s", cen['ass_id'])[0][0]
    return {'venc': venc, 'ativo': ativo, 'status': st, 'tent': tent, 'pags': pags}


def resultados_log(cen):
    return sql("SELECT tipo_evento, nivel, resultado FROM log_integracao_pagamento "
               "WHERE assinatura_recorrente_id=%s ORDER BY id", cen['ass_id'])


def matar_conexoes_app():
    linhas = sql("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                 "WHERE datname=%s AND pid<>pg_backend_pid() "
                 "AND application_name NOT IN ('psql', 'teste_v172')", BANCO)
    return len(linhas or [])


# ───────────────────────────────────────────────────────────────────────────
def modo_conexao_morta():
    print('== Conexao morta (pg_terminate_backend) ==')
    # Aquece o pool com varias requisicoes simultaneas
    cen0 = criar_cenario(date(2026, 11, 10))
    ths = [threading.Thread(target=post, args=(completo(cen0, chave_nova()),)) for _ in range(6)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    total, erros = 0, 0
    # (quantidade de avisos, matar antes de CADA aviso?)
    rodadas = [(1, False), (5, False), (1, False), (5, True), (5, False), (3, True)]
    for rodada, (qtd, sempre) in enumerate(rodadas, 1):
        # aquece o pool de novo antes de matar
        ths = [threading.Thread(target=post, args=(completo(cen0, chave_nova()),)) for _ in range(4)]
        [t.start() for t in ths]
        [t.join() for t in ths]
        mortas = matar_conexoes_app()
        print(f'  rodada {rodada}: {mortas} conexao(oes) do app encerradas; {qtd} aviso(s) seguidos'
              + (' (matando as conexoes antes de cada aviso)' if sempre else ''))
        for i in range(qtd):
            if sempre and i:
                print(f'    +{matar_conexoes_app()} encerradas')
            cen = criar_cenario(date(2026, 11, 10))
            codigo = post(completo(cen, chave_nova()))
            e = estado(cen)
            total += 1
            ok = codigo == 200 and e['pags'] == 1
            if not ok:
                erros += 1
            print(f'    HTTP {codigo}, pagamentos gravados={e["pags"]} -> {"OK" if ok else "FALHOU"}')
    # Rodada paralela: mata tudo e dispara 6 avisos ao mesmo tempo
    mortas = matar_conexoes_app()
    cens = [criar_cenario(date(2026, 11, 10)) for _ in range(6)]
    cods = {}
    ths = [threading.Thread(target=lambda c=c: cods.__setitem__(c['ass_id'], post(completo(c, chave_nova()))))
           for c in cens]
    [t.start() for t in ths]
    [t.join() for t in ths]
    for c in cens:
        e = estado(c)
        total += 1
        ok = cods[c['ass_id']] == 200 and e['pags'] == 1
        erros += 0 if ok else 1
    print(f'  rodada paralela: {mortas} conexoes encerradas; 6 avisos simultaneos; '
          f'codigos={sorted(cods.values())}')
    conferir(erros == 0, f'{total - erros}/{total} avisos processados na primeira tentativa')


def modo_apos_restart():
    print('== Apos docker compose restart db ==')
    cen = criar_cenario(date(2026, 11, 10))
    codigo = post(completo(cen, chave_nova()))
    e = estado(cen)
    conferir(codigo == 200 and e['pags'] == 1, f'primeiro aviso apos restart: HTTP {codigo}, pagamentos={e["pags"]}')


def modo_cenarios():
    hoje = date.today()

    print('== a) completo + curto sequenciais (meucheckout_order_number) ==')
    cen = criar_cenario(date(2026, 11, 10))
    k = chave_nova()
    c1 = post(completo(cen, k))
    c2 = post(curto(cen, k))
    e = estado(cen)
    conferir(c1 == c2 == 200, f'HTTP {c1}/{c2}')
    conferir(e['pags'] == 1, f'1 pagamento (obtido {e["pags"]})')
    conferir(e['venc'] == date(2026, 12, 11), f'vencimento 11/12/2026 (obtido {e["venc"]})')
    for r in resultados_log(cen):
        print('     log:', r)

    print('== b) completo + curto em paralelo (20 rodadas) ==')
    esperado = date(hoje.year + (hoje.month // 12), hoje.month % 12 + 1, hoje.day)
    ruins = curto_primeiro = 0
    for _ in range(20):
        cen = criar_cenario(date(2026, 11, 10))
        k = chave_nova()
        barreira = threading.Barrier(2)
        cods = []

        def enviar(p):
            barreira.wait()
            cods.append(post(p))
        ts = [threading.Thread(target=enviar, args=(completo(cen, k),)),
              threading.Thread(target=enviar, args=(curto(cen, k),))]
        [t.start() for t in ts]
        [t.join() for t in ts]
        e = estado(cen)
        # Quem ganha a corrida define a data: o completo traz created_date (11/11 -> 11/12);
        # o curto nao traz e usa hoje (+1 mes). Nos dois casos: 1 pagamento, vencimento +1 vez.
        if e['venc'] != date(2026, 12, 11):
            curto_primeiro += 1
        if not (e['pags'] == 1 and e['venc'] in (date(2026, 12, 11), esperado) and cods == [200, 200]):
            ruins += 1
            print('    rodada ruim:', cods, e)
    conferir(ruins == 0, f'20 rodadas paralelas com 1 pagamento e vencimento +1 mes uma vez (ruins={ruins}; '
                         f'em {curto_primeiro} o aviso curto foi processado primeiro -> vencimento hoje+1 mes)')

    print('== b2) mesma chave em 2 assinaturas diferentes, em paralelo (caminho IntegrityError) ==')
    ruins = 0
    for _ in range(10):
        ca, cb = criar_cenario(date(2026, 11, 10)), criar_cenario(date(2026, 11, 10))
        k = chave_nova()
        barreira = threading.Barrier(2)
        cods = []

        def enviar(p):
            barreira.wait()
            cods.append(post(p))
        ts = [threading.Thread(target=enviar, args=(completo(ca, k),)),
              threading.Thread(target=enviar, args=(completo(cb, k),))]
        [t.start() for t in ts]
        [t.join() for t in ts]
        total = sql("SELECT count(*) FROM pagamentos WHERE payment_id_gateway=%s", k)[0][0]
        if total != 1 or cods != [200, 200]:
            ruins += 1
            print('    rodada ruim:', cods, total)
    conferir(ruins == 0, f'10 rodadas: 1 pagamento por chave, HTTP 200 nos dois (ruins={ruins})')
    dup = sql("SELECT count(*) FROM log_integracao_pagamento WHERE resultado LIKE 'Aviso repetido ignorado (já processado)%%'")[0][0]
    print(f'     logs "Aviso repetido ignorado" no banco: {dup}')

    print('== c) formato antigo (checkout_cielo_order_number) ==')
    cen = criar_cenario(date(2026, 9, 1))
    k = '41f28923bd3040fe9cb1'[:12] + '%08x' % random.getrandbits(32)
    c1 = post(curto(cen, k, campo='checkout_cielo_order_number'))
    c2 = post(curto(cen, k, campo='checkout_cielo_order_number'))
    e = estado(cen)
    esperado = date(hoje.year + (hoje.month // 12), hoje.month % 12 + 1, hoje.day)
    conferir(c1 == c2 == 200 and e['pags'] == 1, f'1 pagamento (obtido {e["pags"]}), HTTP {c1}/{c2}')
    conferir(e['venc'] == esperado, f'sem created_date -> hoje + 1 mes = {esperado} (obtido {e["venc"]})')

    print('== d) aviso sem nenhuma chave ==')
    cen = criar_cenario(date(2026, 11, 10))
    p = curto(cen, 'x')
    p.pop('meucheckout_order_number')
    c1 = post(p)
    e = estado(cen)
    erro = sql("SELECT nivel, resultado FROM log_integracao_pagamento WHERE nivel='ERROR' "
               "AND payload_bruto->>'order_number'=%s ORDER BY id DESC LIMIT 1", cen['order'])
    conferir(c1 == 200 and e['pags'] == 0 and e['venc'] == date(2026, 11, 10),
             f'HTTP {c1}, pagamentos={e["pags"]}, vencimento inalterado')
    conferir(bool(erro), f'log ERROR: {erro}')

    print('== e) recusa completa + curta (sequencial e paralela) ==')
    cen = criar_cenario(date(2026, 11, 10), tentativas=1)
    k = chave_nova()
    c1 = post(completo(cen, k, status='3', created='14/11/2026 05:00:12'))
    c2 = post(curto(cen, k, status='3'))
    e = estado(cen)
    conferir(c1 == c2 == 200 and e['tent'] == 2, f'tentativas 1 -> 2 (obtido {e["tent"]})')
    for r in resultados_log(cen):
        print('     log:', r)
    cen = criar_cenario(date(2026, 11, 10), tentativas=0)
    k = chave_nova()
    barreira = threading.Barrier(3)

    def enviar(p):
        barreira.wait()
        post(p)
    ts = [threading.Thread(target=enviar, args=(x,)) for x in
          (completo(cen, k, status='3'), curto(cen, k, status='3'), curto(cen, k, status='3'))]
    [t.start() for t in ts]
    [t.join() for t in ts]
    e = estado(cen)
    conferir(e['tent'] == 1, f'paralelo (3 avisos): tentativas 0 -> 1 (obtido {e["tent"]})')
    # 4a recusa (chave nova a cada dia) bloqueia
    cen = criar_cenario(date(2026, 11, 10), tentativas=3)
    post(completo(cen, chave_nova(), status='3'))
    e = estado(cen)
    conferir(e['tent'] == 4 and e['status'] == 'bloqueada_inadimplencia' and e['ativo'] is False,
             f'4a recusa com chave nova bloqueia ({e})')
    for r in resultados_log(cen):
        print('     log:', r)

    print('== item 7) regra de vencimento ==')
    cen = criar_cenario(date(2026, 11, 13))
    post(completo(cen, chave_nova(), created='11/11/2026 03:45:43'))
    e = estado(cen)
    # regra 1.7.2: max(vencimento atual, data da cobranca) + 1 mes
    conferir(e['venc'] == date(2026, 12, 13), f'venc 13/11, cobranca de 11/11 -> 13/12/2026 (obtido {e["venc"]})')
    post(completo(cen, chave_nova(), created='11/11/2026 03:45:43'))
    e2 = estado(cen)
    conferir(e2['venc'] == date(2027, 1, 13) and e2['pags'] == 2,
             f'outra cobranca (chave diferente): vale outro mes -> 13/01/2027 ({e2["venc"]}), pagamentos={e2["pags"]}')
    cen = criar_cenario(date(2027, 1, 20))
    post(completo(cen, chave_nova(), created='11/11/2026 03:45:43'))
    e = estado(cen)
    conferir(e['venc'] == date(2027, 2, 20) and e['pags'] == 1,
             f'vencimento a frente nao regride e soma 1 mes -> 20/02/2027 ({e["venc"]})')
    cen = criar_cenario(date(2026, 11, 1))
    post(curto(cen, chave_nova()))
    e = estado(cen)
    conferir(e['venc'] == esperado, f'curto sem created_date -> hoje + 1 mes ({e["venc"]})')
    cen = criar_cenario(date(2026, 11, 1))
    post(completo(cen, chave_nova(), created='13/31/2026 10:00:00'))
    e = estado(cen)
    conferir(e['venc'] == esperado, f'created_date invalido -> hoje + 1 mes ({e["venc"]})')
    for r in resultados_log(cen):
        print('     log:', r)

    print('== item 8) frases legiveis ==')
    cen = criar_cenario(date(2026, 11, 13))
    post(completo(cen, chave_nova(), extra={'recurrent_status': 'Desativada'}))
    post(completo(cen, chave_nova(), status='3', created='14/11/2026 05:00:12',
                  extra={'return_message': 'Saldo insuficiente'}))
    post(completo(cen, chave_nova(), status='5'))
    for r in resultados_log(cen):
        print('     log:', r)
    p = curto({'order': 'assin0000000000', 'ass_id': 0}, chave_nova())
    post(p)
    print('     log:', sql("SELECT nivel, resultado FROM log_integracao_pagamento "
                           "WHERE payload_bruto->>'order_number'='assin0000000000' ORDER BY id DESC LIMIT 1"))


if __name__ == '__main__':
    modo = sys.argv[1] if len(sys.argv) > 1 else 'cenarios'
    {'conexao_morta': modo_conexao_morta, 'apos_restart': modo_apos_restart,
     'cenarios': modo_cenarios}[modo]()
    print()
    print('RESULTADO:', 'TUDO OK' if not FALHAS else f'{len(FALHAS)} FALHA(S)')
    sys.exit(1 if FALHAS else 0)
