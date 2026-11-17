# -*- coding: utf-8 -*-
"""
Cenario h + item 8c da v1.7.2: telas de pagamento manual/financeiro continuam
funcionando e a tela de logs mostra a frase legivel, busca por aluno e mascara
CPF/e-mail/telefone no payload. Banco descartavel *_teste, app em processo
(test_client), logado como admin.

  docker compose exec -T -e DATABASE_URL=postgresql+psycopg://gymflow:gymflow@db:5432/gymflow_v172_teste \
      web python tests/pagamentos_v172/teste_telas.py
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if not os.environ.get('DATABASE_URL', '').endswith('_teste'):
    sys.exit('Recusado: DATABASE_URL precisa apontar para um banco *_teste')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import teste_webhooks as tw  # noqa: E402

from app import create_app, db  # noqa: E402
from app.models import Usuario, LogIntegracaoPagamento  # noqa: E402

app = create_app()
conferir = tw.conferir


def aceitar_termo_admin():
    """O banco copiado do dev tem termo publicado: registra o aceite do admin."""
    from app import lgpd as svc
    from sqlalchemy import text
    with app.app_context():
        admin = Usuario.query.filter_by(usuario='admin').first()
        v = db.session.execute(text(
            "SELECT id, conteudo_hash FROM lgpd_termo_versao ORDER BY publicado_em DESC, id DESC LIMIT 1")).first()
        if v:
            ja = db.session.execute(text(
                "SELECT 1 FROM lgpd_manifestacao WHERE usuario_id=:u AND termo_versao_id=:v AND manifestacao='aceito'"),
                {'u': admin.id, 'v': v[0]}).first()
            if not ja:
                svc.registrar(admin.id, v[0], v[1], 'aceito')


def main():
    aceitar_termo_admin()
    c = app.test_client()
    r = c.post('/login', data={'usuario': 'admin', 'senha': 'admin123'})
    conferir(r.status_code in (200, 302), f'login HTTP {r.status_code}')

    # Dados para as telas
    cen = tw.criar_cenario(date(2026, 11, 10))
    tw.post(tw.completo(cen, tw.chave_nova()))
    with app.app_context():
        db.session.add(LogIntegracaoPagamento(
            tipo_evento='webhook_recebido', nivel='INFO', assinatura_recorrente_id=cen['ass_id'],
            resultado=f'Teste de mascara — aluno #{cen["aluno_id"]}',
            payload_bruto={'customer_name': 'Fulana', 'customer_email': 'fulana@exemplo.com',
                           'customer_phone': '85999990000', 'customer_identity': '12345678900',
                           'customer_identity_type': '0', 'obs': 'contato fulana@exemplo.com',
                           'order_number': cen['order']}))
        db.session.commit()

    print('== Tela de logs ==')
    nome = sql_nome = tw.sql("SELECT nome FROM alunos WHERE id=%s", cen['aluno_id'])[0][0]
    for busca in (nome, str(cen['aluno_id']), '#' + str(cen['aluno_id']), cen['order']):
        r = c.get('/api/v1/pagamentos/recorrencias/logs', query_string={'f_busca': busca, 'nivel': 'todos'})
        html = r.get_data(as_text=True)
        conferir(r.status_code == 200 and 'Pagamento confirmado — ' in html and sql_nome in html,
                 f'busca "{busca}": HTTP {r.status_code}, frase legivel presente')
    r = c.get('/api/v1/pagamentos/recorrencias/logs', query_string={'f_busca': cen['order'], 'nivel': 'WARNING'})
    conferir(r.status_code == 200 and 'Pagamento confirmado — ' not in r.get_data(as_text=True),
             'filtro WARNING + busca: nao mostra o INFO')
    r = c.get('/api/v1/pagamentos/recorrencias/logs', query_string={'f_busca': 'Teste de mascara'})
    html = r.get_data(as_text=True)
    conferir('fulana@exemplo.com' not in html and '85999990000' not in html and '12345678900' not in html,
             'payload exibido sem e-mail, telefone e CPF')
    conferir('customer_identity_type' in html and 'Fulana' in html, 'demais campos continuam no payload')
    r = c.get('/api/v1/pagamentos/recorrencias/logs')
    conferir(r.status_code == 200, f'logs sem filtro HTTP {r.status_code}')

    print('== Recorrencias e ficha ==')
    r = c.get('/api/v1/pagamentos/recorrencias')
    conferir(r.status_code == 200, f'painel de recorrencias HTTP {r.status_code}')
    r = c.get(f'/api/v1/pagamentos/assinaturas/aluno/{cen["aluno_id"]}')
    j = r.get_json()
    conferir(r.status_code == 200 and j['existe'] and j['ultimo_pagamento'], f'assinatura do aluno: {r.status_code}')

    print('== h) Financeiro / pagamento manual ==')
    r = c.get('/financeiro')
    conferir(r.status_code == 200, f'/financeiro HTTP {r.status_code}')
    aluno_id = cen['aluno_id']
    antes = tw.sql("SELECT count(*) FROM pagamentos WHERE aluno_id=%s", aluno_id)[0][0]
    r = c.post('/financeiro/registrar', data={
        'aluno_id': aluno_id, 'valor': '60', 'forma_pagamento': 'dinheiro',
        'data_vencimento': '2027-02-10', 'data_pagamento': date.today().isoformat(), 'observacao': 'teste v172'})
    depois = tw.sql("SELECT count(*) FROM pagamentos WHERE aluno_id=%s", aluno_id)[0][0]
    venc = tw.sql("SELECT vencimento FROM alunos WHERE id=%s", aluno_id)[0][0]
    conferir(r.status_code == 302 and depois == antes + 1 and venc == date(2027, 2, 10),
             f'registrar pago: HTTP {r.status_code}, +{depois - antes} pagamento, vencimento {venc}')
    # dois lancamentos manuais SEM chave do mesmo aluno: o indice unico parcial nao atrapalha
    r = c.post('/financeiro/registrar', data={
        'aluno_id': aluno_id, 'dividido': '1', 'valor1': '30', 'forma1': 'pix', 'banco_pix1': 'X',
        'valor2': '30', 'forma2': 'dinheiro', 'data_vencimento': '2027-03-10'})
    pend = tw.sql("SELECT id FROM pagamentos WHERE aluno_id=%s AND status='pendente' ORDER BY id", aluno_id)
    conferir(r.status_code == 302 and len(pend) == 2, f'registrar dividido pendente: {len(pend)} lancamentos')
    r = c.post(f'/financeiro/{pend[0][0]}/pagar', data={'forma_pagamento': 'pix', 'banco_pix': 'X',
                                                       'data_pagamento': date.today().isoformat()})
    st = tw.sql("SELECT status FROM pagamentos WHERE id=%s", pend[0][0])[0][0]
    conferir(r.status_code == 302 and st == 'pago', f'confirmar pendente: HTTP {r.status_code}, status {st}')
    r = c.get('/financeiro/relatorio-diario')
    conferir(r.status_code == 200, f'/financeiro/relatorio-diario HTTP {r.status_code}')
    for formato in ('pdf', 'excel'):
        r = c.get('/financeiro/relatorio-mensal', query_string={
            'inicio': '2026-11-01', 'fim': '2026-11-30', 'formato': formato})
        conferir(r.status_code == 200, f'/financeiro/relatorio-mensal ({formato}, com secao recorrente) '
                                       f'HTTP {r.status_code} {r.mimetype}')

    print()
    print('RESULTADO:', 'TUDO OK' if not tw.FALHAS else f'{len(tw.FALHAS)} FALHA(S)')
    return 1 if tw.FALHAS else 0


if __name__ == '__main__':
    sys.exit(main())
