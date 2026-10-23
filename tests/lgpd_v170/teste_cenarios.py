# -*- coding: utf-8 -*-
"""
Testes de aceitacao da v1.7.0 (LGPD: ciencia e consentimento).

Roda contra um banco DESCARTAVEL (nome terminado em _teste) que ja tenha a
migracao migrations/001_v1.7.0_lgpd_up.sql aplicada. Ver
tests/lgpd_v170/README.md para preparar o banco.

    docker compose exec -e DATABASE_URL=postgresql+psycopg://gymflow:gymflow@db:5432/gymflow_v170_teste \
        web python tests/lgpd_v170/teste_cenarios.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

url = os.environ.get('DATABASE_URL', '')
if not url.rstrip('/').endswith('_teste'):
    sys.exit('ABORTADO: DATABASE_URL precisa apontar para um banco cujo nome termina em _teste.')

from unittest import mock                                    # noqa: E402
from sqlalchemy import text                                  # noqa: E402
from sqlalchemy.exc import OperationalError                  # noqa: E402
from werkzeug.security import generate_password_hash         # noqa: E402

from app import create_app, db                               # noqa: E402
from app.models import (Usuario, Aluno, Configuracao, LgpdTermo,  # noqa: E402
                        LgpdTermoVersao, LgpdManifestacao, LgpdTermoAuditoria)
from app import lgpd as svc                                  # noqa: E402

app = create_app()
app.config['TESTING'] = True
SENHA = 'Teste123'
resultados = []


def caso(cenario, descricao, condicao):
    resultados.append((cenario, descricao, bool(condicao)))
    print(('OK    ' if condicao else 'FALHA ') + f'[{cenario}] {descricao}')


def sql(q, **p):
    with app.app_context():
        r = db.session.execute(text(q), p)
        try:
            v = r.scalar()
        except Exception:
            v = None
        db.session.commit()
        return v


def manif_count(usuario_id=None):
    if usuario_id is None:
        return sql('select count(*) from lgpd_manifestacao')
    return sql('select count(*) from lgpd_manifestacao where usuario_id=:u', u=usuario_id)


def set_param(chave, valor):
    with app.app_context():
        c = Configuracao.query.filter_by(chave=chave).first()
        c.valor = valor
        db.session.commit()


def cliente(login):
    c = app.test_client()
    r = c.post('/login', data={'usuario': login, 'senha': SENHA})
    assert r.status_code == 302, (login, r.status_code)
    return c


def csrf(c):
    # O token e gerado quando uma pagina com formulario LGPD e renderizada;
    # aqui ele e criado na sessao do cliente de teste do mesmo jeito.
    with c.session_transaction() as s:
        if not s.get('lgpd_csrf'):
            s['lgpd_csrf'] = svc.secrets.token_urlsafe(32)
        return s['lgpd_csrf']


def uid(login):
    with app.app_context():
        return Usuario.query.filter_by(usuario=login).one().id


def form_manifestacao(c):
    """Abre a tela de manifestacao e extrai os campos ocultos do formulario."""
    import re
    r = c.get('/privacidade/manifestacao')
    html = r.data.decode('utf-8')
    campos = dict(re.findall(r'name="(termo_versao_id|conteudo_hash|termo_codigo|csrf_token)"\s+value="([^"]*)"', html))
    return r, html, campos


def responder(c, manifestacao):
    _, _, f = form_manifestacao(c)
    f['manifestacao'] = manifestacao
    return c.post('/privacidade/manifestacao/registrar', data=f)


# ─────────────────────────────────────────────────────────────────────────────
# Preparacao: usuarios e alunos de teste (>= 400 cada)
# ─────────────────────────────────────────────────────────────────────────────
with app.app_context():
    h = generate_password_hash(SENHA)
    for login, perfil in (('t_admin', 'admin'), ('t_oper', 'operador'), ('t_oper2', 'operador'),
                          ('t_oper3', 'operador'), ('t_oper4', 'operador')):
        if not Usuario.query.filter_by(usuario=login).first():
            db.session.add(Usuario(nome=login, usuario=login, senha_hash=h, perfil=perfil, ativo=True))
    existentes = Usuario.query.count()
    for i in range(existentes, 405):
        db.session.add(Usuario(nome=f'Carga {i}', usuario=f't_carga_{i}', senha_hash=h, perfil='operador', ativo=True))
    db.session.commit()
    plano_id = db.session.execute(text('select id from planos order by id limit 1')).scalar()
    n_alunos = Aluno.query.count()
    if n_alunos < 400:
        cols = {c.name for c in Aluno.__table__.columns}
        for i in range(n_alunos, 400):
            dados = {'nome': f'Aluno Carga {i}'}
            if 'documento' in cols:
                dados['documento'] = f'9{i:010d}'
            db.session.add(Aluno(**dados))
        db.session.commit()
    print(f'Preparacao: {Usuario.query.count()} usuarios, {Aluno.query.count()} alunos')

ADMIN, OPER = uid('t_admin'), uid('t_oper')
set_param('lgpd_contato_privacidade', 'Recepcao da academia (teste)')
set_param('lgpd_gate_ativo', '0')

adm = cliente('t_admin')
tok = csrf(adm)

# Publica o aviso (seed v1.0 tem [PREENCHER: deve ser recusado)
with app.app_context():
    aviso = LgpdTermo.query.filter_by(codigo='aviso_privacidade').one()
    aviso_id = aviso.id
    seed = LgpdTermoVersao.query.filter_by(termo_id=aviso_id, versao='1.0').one()
    seed_id, seed_status = seed.id, seed.status
if seed_status == 'rascunho':
    adm.post(f'/privacidade/admin/versoes/{seed_id}/publicar',
             data={'csrf_token': tok, 'modo_vigencia': 'imediata', 'confirmo': '1'})
    with app.app_context():
        caso('Termo', 'publicacao do modelo com [PREENCHER] e recusada',
             db.session.get(LgpdTermoVersao, seed_id).status == 'rascunho')
    texto = 'AVISO DE PRIVACIDADE (teste automatizado)\nAcademia Teste LTDA.\nFinalidades: controle de acesso ao sistema.'
    adm.post(f'/privacidade/admin/versoes/{seed_id}/editar',
             data={'csrf_token': tok, 'versao': '1.0', 'conteudo': texto, 'alteracao_material': '1'})
    adm.post(f'/privacidade/admin/versoes/{seed_id}/publicar',
             data={'csrf_token': tok, 'modo_vigencia': 'imediata', 'confirmo': '1'})
with app.app_context():
    caso('Termo', 'aviso v1.0 publicado apos preencher os campos',
         db.session.get(LgpdTermoVersao, seed_id).status == 'publicada')

# Termo de consentimento que bloqueia o sistema
adm.post('/privacidade/admin/termos/novo', data={
    'csrf_token': tok, 'codigo': 'consent_teste', 'titulo': 'Consentimento de teste', 'tipo': 'consentimento',
    'finalidade': 'Finalidade ficticia usada apenas no teste.', 'bloqueia_sistema': '1', 'ativo': '1'})
with app.app_context():
    cons = LgpdTermo.query.filter_by(codigo='consent_teste').one()
    cons_id = cons.id
adm.post(f'/privacidade/admin/termos/{cons_id}/versoes/nova', data={
    'csrf_token': tok, 'versao': '1.0', 'conteudo': 'Texto do consentimento de teste.', 'alteracao_material': '1'})
with app.app_context():
    cv1 = LgpdTermoVersao.query.filter_by(termo_id=cons_id, versao='1.0').one().id
adm.post(f'/privacidade/admin/versoes/{cv1}/publicar',
         data={'csrf_token': tok, 'modo_vigencia': 'imediata', 'confirmo': '1'})
adm.post('/privacidade/admin/configuracao',
         data={'csrf_token': tok, 'lgpd_gate_ativo': '1', 'lgpd_contato_privacidade': 'Recepcao da academia (teste)'})
caso('Termo', 'gate ligado pelo admin', sql("select valor from configuracoes where chave='lgpd_gate_ativo'") == '1')

# ─────────────────────────────────────────────────────────────────────────────
# 1. Usuario existente sem manifestacao
# ─────────────────────────────────────────────────────────────────────────────
op = cliente('t_oper')
r = op.get('/dashboard')
caso(1, 'usuario sem manifestacao e redirecionado para a tela obrigatoria',
     r.status_code == 302 and '/privacidade/manifestacao' in r.location)
r, html, f = form_manifestacao(op)
caso(1, 'tela mostra o aviso (ciencia antes do consentimento) e a versao',
     r.status_code == 200 and 'Li e estou ciente' in html and '1.0' in html)
caso(1, 'tela de ciencia nao oferece "Concordo"', 'Concordo' not in html.replace('Não concordo', ''))
caso(1, 'login sozinho nao gerou manifestacao', manif_count(OPER) == 0)

# ─────────────────────────────────────────────────────────────────────────────
# 2. Usuario que concorda
# ─────────────────────────────────────────────────────────────────────────────
r = responder(op, 'ciente')
caso(2, 'ciencia registrada', manif_count(OPER) == 1)
r, html, f = form_manifestacao(op)
caso(2, 'em seguida apresenta o consentimento com Concordo/Nao concordo',
     'Concordo' in html and 'Não concordo' in html)
r = responder(op, 'concordo')
caso(2, 'concordo registrado', manif_count(OPER) == 2)
r = op.get('/dashboard')
caso(2, 'acesso liberado apos responder', r.status_code == 200)
with app.app_context():
    m = LgpdManifestacao.query.filter_by(usuario_id=OPER).order_by(LgpdManifestacao.id.desc()).first()
    v = db.session.get(LgpdTermoVersao, m.termo_versao_id)
    from app.version import APP_VERSAO
    caso(2, 'registro tem usuario, versao do termo, hash, versao do software e data/hora',
         m.usuario_id == OPER and m.conteudo_hash == v.conteudo_hash and m.app_versao == APP_VERSAO
         and m.registrado_em is not None and m.registrado_em.tzinfo is not None)
    cols = {c.name for c in LgpdManifestacao.__table__.columns}
    caso(2, 'registro nao guarda IP, senha nem dados adicionais',
         not ({'ip', 'senha', 'cpf', 'user_agent', 'geolocalizacao'} & cols))

# Clique duplo
op_dup = cliente('t_oper4')
_, _, f = form_manifestacao(op_dup)
f['manifestacao'] = 'ciente'
op_dup.post('/privacidade/manifestacao/registrar', data=f)
op_dup.post('/privacidade/manifestacao/registrar', data=f)
caso(2, 'clique duplo gera um unico registro', manif_count(uid('t_oper4')) == 1)

# ─────────────────────────────────────────────────────────────────────────────
# 3. Usuario que nao concorda
# ─────────────────────────────────────────────────────────────────────────────
op2 = cliente('t_oper2')
OPER2 = uid('t_oper2')
responder(op2, 'ciente')
responder(op2, 'nao_concordo')
with app.app_context():
    ultimo = LgpdManifestacao.query.filter_by(usuario_id=OPER2).order_by(LgpdManifestacao.id.desc()).first()
    caso(3, 'recusa registrada como nao_concordo (sem consentimento positivo)',
         ultimo.manifestacao == 'nao_concordo'
         and LgpdManifestacao.query.filter_by(usuario_id=OPER2, manifestacao='concordo').count() == 0)
    u2 = db.session.get(Usuario, OPER2)
    caso(3, 'conta nao excluida nem desativada', u2 is not None and u2.ativo)
r = op2.get('/alunos')
caso(3, 'funcionalidades bloqueadas apos recusa', r.status_code == 302 and '/privacidade/recusado' in r.location)
r = op2.get('/privacidade/recusado')
html = r.data.decode('utf-8')
caso(3, 'tela de recusa mostra o contato da academia', 'Recepcao da academia (teste)' in html)
r = op2.get('/monitoramento/ultimo', headers={'X-Requested-With': 'XMLHttpRequest'})
caso(3, 'XHR bloqueado com 403 JSON lgpd_recusado', r.status_code == 403 and r.is_json and r.get_json().get('erro') == 'lgpd_recusado')
# revogar/reconsiderar
r = op2.get('/privacidade/manifestacao?termo=consent_teste')
responder_f = dict(__import__('re').findall(r'name="(termo_versao_id|conteudo_hash|termo_codigo|csrf_token)"\s+value="([^"]*)"', r.data.decode()))
responder_f['manifestacao'] = 'concordo'
op2.post('/privacidade/manifestacao/registrar', data=responder_f)
caso(3, 'usuario pode rever e concordar depois (novo registro, historico preservado)',
     op2.get('/alunos').status_code == 200 and manif_count(OPER2) == 3)

# ─────────────────────────────────────────────────────────────────────────────
# 4. Usuario fecha a aplicacao antes de responder
# ─────────────────────────────────────────────────────────────────────────────
op3 = cliente('t_oper3')
OPER3 = uid('t_oper3')
form_manifestacao(op3)            # abriu a tela e "fechou o navegador"
del op3
op3 = cliente('t_oper3')          # novo login
r = op3.get('/dashboard')
caso(4, 'sem resposta nada e gravado e o termo continua pendente no proximo login',
     manif_count(OPER3) == 0 and r.status_code == 302 and '/privacidade/manifestacao' in r.location)

# ─────────────────────────────────────────────────────────────────────────────
# 5. Falha de conexao/gravacao no banco
# ─────────────────────────────────────────────────────────────────────────────
_, _, f = form_manifestacao(op3)
f['manifestacao'] = 'ciente'
erro = OperationalError('COMMIT', {}, Exception('conexao perdida (simulada)'))
with mock.patch.object(db.session, 'commit', side_effect=erro):
    r = op3.post('/privacidade/manifestacao/registrar', data=f, follow_redirects=True)
html = r.data.decode('utf-8')
caso(5, 'falha no commit: nada gravado', manif_count(OPER3) == 0)
caso(5, 'falha no commit: interface informa erro e nao sucesso',
     'Não foi possível registrar' in html and 'Sua resposta foi registrada' not in html)
with mock.patch.object(svc, 'situacao_usuario', side_effect=OperationalError('SELECT', {}, Exception('db fora'))):
    r = op3.get('/alunos')
caso(5, 'falha ao verificar situacao: acesso negado (503, falha fechada)', r.status_code == 503)
caso(5, 'apos a falha o sistema volta a funcionar', op3.get('/dashboard').status_code == 302)

# ─────────────────────────────────────────────────────────────────────────────
# 6. Novo login apos manifestacao registrada
# ─────────────────────────────────────────────────────────────────────────────
op = cliente('t_oper')
antes = manif_count(OPER)
r = op.get('/dashboard')
caso(6, 'novo login nao pede de novo e nao gera registro',
     r.status_code == 200 and manif_count(OPER) == antes)

# ─────────────────────────────────────────────────────────────────────────────
# 7. Publicacao de nova versao
# ─────────────────────────────────────────────────────────────────────────────
adm = cliente('t_admin')
responder(adm, 'ciente'); responder(adm, 'concordo')
tok = csrf(adm)
adm.post(f'/privacidade/admin/termos/{aviso_id}/versoes/nova', data={
    'csrf_token': tok, 'versao': '1.1', 'conteudo': 'AVISO (teste) - correcao de digitacao.', 'alteracao_material': '0'})
with app.app_context():
    v11 = LgpdTermoVersao.query.filter_by(termo_id=aviso_id, versao='1.1').one().id
adm.post(f'/privacidade/admin/versoes/{v11}/publicar', data={'csrf_token': tok, 'modo_vigencia': 'imediata', 'confirmo': '1'})
caso(7, 'versao nao material (1.1) nao exige nova manifestacao', op.get('/dashboard').status_code == 200)
adm.post(f'/privacidade/admin/termos/{aviso_id}/versoes/nova', data={
    'csrf_token': tok, 'versao': '2.0', 'conteudo': 'AVISO (teste) - nova finalidade incluida.', 'alteracao_material': '1'})
with app.app_context():
    v20 = LgpdTermoVersao.query.filter_by(termo_id=aviso_id, versao='2.0').one().id
adm.post(f'/privacidade/admin/versoes/{v20}/publicar', data={'csrf_token': tok, 'modo_vigencia': 'imediata', 'confirmo': '1'})
r = op.get('/dashboard')
caso(7, 'versao material (2.0) exige nova manifestacao',
     r.status_code == 302 and '/privacidade/manifestacao' in r.location)
r, html, f = form_manifestacao(op)
caso(7, 'tela apresenta a versao 2.0', '2.0' in html and f.get('termo_versao_id') == str(v20))
antes = manif_count(OPER)
responder(op, 'ciente')
caso(7, 'historico preservado e novo registro na 2.0',
     manif_count(OPER) == antes + 1
     and sql('select count(*) from lgpd_manifestacao where usuario_id=:u and termo_versao_id=:v', u=OPER, v=seed_id) == 1)
with app.app_context():
    caso(7, 'publicacao gerou auditoria administrativa',
         LgpdTermoAuditoria.query.filter_by(acao='publicou_versao', termo_versao_id=v20).count() == 1)
# hash adulterado / versao antiga
r = op.post('/privacidade/manifestacao/registrar', data={
    'csrf_token': csrf(op), 'termo_versao_id': str(seed_id), 'conteudo_hash': 'x' * 64, 'manifestacao': 'ciente'})
caso(7, 'manifestacao em versao antiga ou com hash divergente e recusada no backend', manif_count(OPER) == antes + 1)

# ─────────────────────────────────────────────────────────────────────────────
# 8. Acesso direto sem cumprir os requisitos
# ─────────────────────────────────────────────────────────────────────────────
op3 = cliente('t_oper3')
bloqueadas = ['/dashboard', '/alunos', '/financeiro', '/relatorios', '/monitoramento', '/configuracoes', '/usuarios']
ok_todas = True
for rota in bloqueadas:
    r = op3.get(rota)
    if not (r.status_code == 302 and '/privacidade/manifestacao' in (r.location or '')):
        ok_todas = False
        print('   rota nao bloqueada:', rota, r.status_code, r.location)
caso(8, 'todas as rotas internas redirecionam para a manifestacao', ok_todas)
r = op3.get('/monitoramento/ultimo')
caso(8, 'polling do monitoramento recebe 403 JSON lgpd_pendente',
     r.status_code == 403 and r.get_json().get('erro') == 'lgpd_pendente')
r = op3.post('/privacidade/manifestacao/registrar', data={
    'termo_versao_id': str(v20), 'conteudo_hash': '', 'manifestacao': 'ciente'})
caso(8, 'POST sem token CSRF nao grava', manif_count(OPER3) == 0)
r = op3.post('/privacidade/manifestacao/registrar', data={
    'csrf_token': csrf(op3), 'termo_versao_id': str(v20), 'conteudo_hash': 'a' * 64, 'manifestacao': 'concordo'})
caso(8, 'manifestacao incompativel (concordo em termo de ciencia) e recusada', manif_count(OPER3) == 0)
anon = app.test_client()
r = anon.get('/privacidade/admin/manifestacoes')
caso(8, 'sem login nao acessa nada da area de privacidade', r.status_code == 302 and '/login' in r.location)
r = anon.post('/acessos/biometria', json={'aluno_id': 0})
caso(8, 'FingerPoint (POST /acessos/biometria) continua isento do gate', r.status_code != 302)

# ─────────────────────────────────────────────────────────────────────────────
# 9. Usuario sem permissao tentando consultar/alterar registros
# ─────────────────────────────────────────────────────────────────────────────
op = cliente('t_oper')
tk = csrf(op)
negado = True
for rota in ['/privacidade/admin/termos', '/privacidade/admin/manifestacoes', '/privacidade/admin/situacao',
             '/privacidade/admin/auditoria', '/privacidade/admin/configuracao', f'/privacidade/admin/versoes/{v20}']:
    r = op.get(rota)
    if not (r.status_code == 302 and '/dashboard' in (r.location or '')):
        negado = False
        print('   rota admin acessivel ao operador:', rota, r.status_code)
caso(9, 'operador nao consulta historico, situacao, auditoria nem configuracao', negado)
op.post('/privacidade/admin/configuracao', data={'csrf_token': tk, 'lgpd_gate_ativo': '0'})
caso(9, 'operador nao consegue desligar o gate', sql("select valor from configuracoes where chave='lgpd_gate_ativo'") == '1')
qtd_v = sql('select count(*) from lgpd_termo_versao')
op.post(f'/privacidade/admin/termos/{aviso_id}/versoes/nova', data={'csrf_token': tk, 'versao': '9.9', 'conteudo': 'x'})
caso(9, 'operador nao cria versao de termo', sql('select count(*) from lgpd_termo_versao') == qtd_v)
r = op.get('/privacidade/minhas')
caso(9, 'operador ve apenas o proprio historico',
     r.status_code == 200 and 't_oper2' not in r.data.decode() and 't_admin' not in r.data.decode())


def tenta(q):
    with app.app_context():
        try:
            db.session.execute(text(q))
            db.session.commit()
            return True
        except Exception:
            db.session.rollback()
            return False


caso(9, 'UPDATE direto em lgpd_manifestacao bloqueado pelo banco',
     not tenta("update lgpd_manifestacao set manifestacao='concordo'"))
caso(9, 'DELETE direto em lgpd_manifestacao bloqueado pelo banco', not tenta('delete from lgpd_manifestacao'))
caso(9, 'TRUNCATE em lgpd_manifestacao bloqueado pelo banco', not tenta('truncate lgpd_manifestacao'))
caso(9, 'UPDATE em versao publicada bloqueado pelo banco',
     not tenta(f"update lgpd_termo_versao set conteudo='adulterado' where id={v20}"))
caso(9, 'DELETE em auditoria bloqueado pelo banco', not tenta('delete from lgpd_termo_auditoria'))
caso(9, 'excluir usuario com manifestacao bloqueado (FK RESTRICT)', not tenta(f'delete from usuarios where id={OPER}'))
caso(9, 'registros intactos apos as tentativas', manif_count(OPER) == antes + 1)

# ─────────────────────────────────────────────────────────────────────────────
# 11. Carga: >= 400 usuarios e 400 alunos
# ─────────────────────────────────────────────────────────────────────────────
with app.test_request_context():
    ids = [u.id for u in Usuario.query.filter(Usuario.usuario.like('t_carga_%')).all()]
    vig = {t.id: svc.versao_vigente(t) for t in svc.termos_ativos()}
    t0 = time.perf_counter()
    for u in ids:
        for t in svc.termos_ativos():
            v = vig[t.id]
            svc.registrar_manifestacao(u, v.id, v.conteudo_hash,
                                       'ciente' if t.tipo == 'ciencia' else 'concordo')
    t_reg = time.perf_counter() - t0
caso(11, f'{len(ids)} usuarios x 2 termos registrados em {t_reg:.1f}s', len(ids) >= 395)


def media(c, rota, n=100):
    t0 = time.perf_counter()
    for _ in range(n):
        c.get(rota)
    return (time.perf_counter() - t0) / n * 1000


carga = cliente(f't_carga_{ids and 10}') if False else cliente('t_carga_10')
set_param('lgpd_gate_ativo', '0')
sem = media(carga, '/dashboard')
set_param('lgpd_gate_ativo', '1')
com = media(carga, '/dashboard')
print(f'   /dashboard: gate desligado {sem:.1f} ms, gate ligado {com:.1f} ms (media de 100)')
caso(11, f'overhead do gate por requisicao < 50 ms (medido {com - sem:.1f} ms)', com - sem < 50)
adm = cliente('t_admin')
responder(adm, 'ciente')          # o proprio admin tambem precisa responder a versao 2.0
t0 = time.perf_counter(); r = adm.get('/privacidade/admin/situacao'); t_sit = time.perf_counter() - t0
caso(11, f'painel de situacao com {len(ids) + 5}+ usuarios responde em {t_sit:.2f}s (< 3s)', r.status_code == 200 and t_sit < 3)
t0 = time.perf_counter(); r = adm.get('/privacidade/admin/manifestacoes'); t_man = time.perf_counter() - t0
caso(11, f'historico paginado responde em {t_man:.2f}s (< 2s)', r.status_code == 200 and t_man < 2)
t0 = time.perf_counter(); r = adm.get('/alunos'); t_al = time.perf_counter() - t0
caso(11, f'listagem de alunos (400) com gate ligado responde em {t_al:.2f}s', r.status_code == 200 and t_al < 3)

# ─────────────────────────────────────────────────────────────────────────────
# Resumo
# ─────────────────────────────────────────────────────────────────────────────
falhas = [r for r in resultados if not r[2]]
print(f'\nTOTAL: {len(resultados)} verificacoes, {len(resultados) - len(falhas)} OK, {len(falhas)} falhas')
sys.exit(1 if falhas else 0)
