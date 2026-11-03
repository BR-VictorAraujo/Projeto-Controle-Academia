# -*- coding: utf-8 -*-
"""
Testes de aceitacao da v1.7.0 (LGPD: termo com aceite em pop-up).

Roda contra um banco DESCARTAVEL (nome terminado em _teste) com a migracao
migrations/001_v1.7.0_lgpd_up.sql aplicada e SEM versao de termo publicada.
Ver tests/lgpd_v170/README.md.
"""
import os
import re
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

if not os.environ.get('DATABASE_URL', '').rstrip('/').endswith('_teste'):
    sys.exit('ABORTADO: DATABASE_URL precisa apontar para um banco cujo nome termina em _teste.')

from unittest import mock                                    # noqa: E402
from sqlalchemy import text                                  # noqa: E402
from sqlalchemy.exc import OperationalError                  # noqa: E402
from werkzeug.security import generate_password_hash         # noqa: E402

from app import create_app, db                               # noqa: E402
from app.models import Usuario, Aluno, LgpdTermoVersao, LgpdManifestacao  # noqa: E402
from app import lgpd as svc                                  # noqa: E402

app = create_app()
app.config['TESTING'] = True
SENHA = 'Teste123'
MODAL = 'formTermoLgpd'
resultados = []


def caso(cenario, descricao, condicao):
    resultados.append(bool(condicao))
    print(('OK    ' if condicao else 'FALHA ') + f'[{cenario}] {descricao}')


def sql(q, **p):
    with app.app_context():
        v = db.session.execute(text(q), p).scalar()
        db.session.commit()
        return v


def tenta(q):
    with app.app_context():
        try:
            db.session.execute(text(q))
            db.session.commit()
            return True
        except Exception:
            db.session.rollback()
            return False


def publicar(versao, texto):
    """Simula a migracao gerada por tools/gerar_migracao_termo.py."""
    c = svc.canonicalizar(texto)
    sql('insert into lgpd_termo_versao (versao, conteudo, conteudo_hash) values (:v, :c, :h) returning id',
        v=versao, c=c, h=svc.hash_conteudo(c))
    with app.app_context():
        return LgpdTermoVersao.query.filter_by(versao=versao).one().id


def uid(login):
    with app.app_context():
        return Usuario.query.filter_by(usuario=login).one().id


def n_manif(usuario_id=None):
    if usuario_id is None:
        return sql('select count(*) from lgpd_manifestacao')
    return sql('select count(*) from lgpd_manifestacao where usuario_id=:u', u=usuario_id)


def cliente(login):
    c = app.test_client()
    r = c.post('/login', data={'usuario': login, 'senha': SENHA})
    assert r.status_code == 302, (login, r.status_code)
    return c


def campos_modal(c):
    html = c.get('/dashboard').data.decode('utf-8')
    return html, dict(re.findall(r'name="(csrf_token|termo_versao_id|conteudo_hash)" value="([^"]*)"', html))


def responder(c, manifestacao):
    _, f = campos_modal(c)
    f['manifestacao'] = manifestacao
    return c.post('/lgpd/responder', data=f)


# ─────────────────────────────────────────────────────────────────────────────
# Preparacao: >= 400 usuarios e 400 alunos
# ─────────────────────────────────────────────────────────────────────────────
with app.app_context():
    h = generate_password_hash(SENHA)
    for login, perfil in (('t_admin', 'admin'), ('t_oper', 'operador'), ('t_oper2', 'operador'),
                          ('t_oper3', 'operador'), ('t_oper4', 'operador')):
        if not Usuario.query.filter_by(usuario=login).first():
            db.session.add(Usuario(nome=login, usuario=login, senha_hash=h, perfil=perfil, ativo=True))
    for i in range(Usuario.query.count(), 405):
        db.session.add(Usuario(nome=f'Carga {i}', usuario=f't_carga_{i}', senha_hash=h, perfil='operador', ativo=True))
    for i in range(Aluno.query.count(), 400):
        db.session.add(Aluno(nome=f'Aluno Carga {i}', documento=f'9{i:010d}'))
    db.session.commit()
    print(f'Preparacao: {Usuario.query.count()} usuarios, {Aluno.query.count()} alunos')

ADMIN, OPER, OPER2, OPER3, OPER4 = (uid(x) for x in ('t_admin', 't_oper', 't_oper2', 't_oper3', 't_oper4'))

# ─────────────────────────────────────────────────────────────────────────────
# 0. Sem termo publicado: sistema igual ao anterior
# ─────────────────────────────────────────────────────────────────────────────
op = cliente('t_oper')
r = op.get('/dashboard')
caso(0, 'sem termo publicado: dashboard normal, sem pop-up', r.status_code == 200 and MODAL not in r.data.decode())
caso(0, 'sem termo publicado: demais telas acessiveis', op.get('/alunos').status_code == 200)

V1 = publicar('1.0', 'TERMO DE USO E PRIVACIDADE (teste)\nAcademia Teste LTDA.\nFinalidade: controle de acesso.')

# ─────────────────────────────────────────────────────────────────────────────
# 1. Usuario existente sem manifestacao
# ─────────────────────────────────────────────────────────────────────────────
op = cliente('t_oper')
html, f = campos_modal(op)
caso(1, 'primeiro acesso mostra o pop-up com Aceito / Nao aceito e a versao',
     MODAL in html and 'Aceito' in html and 'Não aceito' in html and '1.0' in html)
caso(1, 'pop-up sem botao de fechar e com backdrop estatico',
     'data-bs-backdrop="static"' in html and 'data-bs-keyboard="false"' in html and 'btn-close' not in html)
caso(1, 'dados do dashboard nao sao exibidos atras do pop-up', 'Aluno Carga' not in html)
caso(1, 'login sozinho nao registra aceite', n_manif(OPER) == 0)
adm = cliente('t_admin')
caso(1, 'admin tambem ve o pop-up no primeiro acesso', MODAL in campos_modal(adm)[0])

# ─────────────────────────────────────────────────────────────────────────────
# 2. Usuario que aceita
# ─────────────────────────────────────────────────────────────────────────────
r = responder(op, 'aceito')
caso(2, 'aceite registrado e volta ao dashboard', n_manif(OPER) == 1 and '/dashboard' in r.location)
r = op.get('/dashboard')
caso(2, 'apos aceitar o pop-up some e o dashboard completo carrega', r.status_code == 200 and MODAL not in r.data.decode())
caso(2, 'demais telas liberadas', op.get('/alunos').status_code == 200)
with app.app_context():
    m = LgpdManifestacao.query.filter_by(usuario_id=OPER).one()
    from app.version import APP_VERSAO
    caso(2, 'registro: usuario, aceito, versao, hash, versao do software e data/hora',
         m.manifestacao == 'aceito' and m.termo_versao_id == V1 and m.app_versao == APP_VERSAO
         and m.conteudo_hash == db.session.get(LgpdTermoVersao, V1).conteudo_hash
         and m.registrado_em is not None and m.registrado_em.tzinfo is not None)
    caso(2, 'registro sem IP, senha ou dados adicionais',
         not ({'ip', 'senha', 'cpf', 'user_agent', 'geolocalizacao'} & {c.name for c in LgpdManifestacao.__table__.columns}))
    caso(2, 'log de auditoria do aceite sem IP',
         sql("select count(*) from log_auditoria where acao like '%lgpd%' and ip is not null") == 0)
# clique duplo
op4 = cliente('t_oper4')
_, f = campos_modal(op4)
f['manifestacao'] = 'aceito'
op4.post('/lgpd/responder', data=f)
op4.post('/lgpd/responder', data=f)
caso(2, 'clique duplo gera um unico registro', n_manif(OPER4) == 1)

# ─────────────────────────────────────────────────────────────────────────────
# 3. Usuario que nao aceita
# ─────────────────────────────────────────────────────────────────────────────
op2 = cliente('t_oper2')
r = responder(op2, 'nao_aceito')
caso(3, 'recusa registrada como nao_aceito',
     sql("select manifestacao from lgpd_manifestacao where usuario_id=:u order by id desc limit 1", u=OPER2) == 'nao_aceito')
caso(3, 'recusa volta para a tela de login', r.status_code == 302 and '/login' in r.location)
r = op2.get('/login')
caso(3, 'login exibe a mensagem generica', 'O aceite do termo é necessário' in r.data.decode())
r = op2.get('/dashboard')
caso(3, 'sessao encerrada apos a recusa', r.status_code == 302 and '/login' in r.location)
with app.app_context():
    u = db.session.get(Usuario, OPER2)
    caso(3, 'conta nao excluida nem desativada', u is not None and u.ativo)
op2 = cliente('t_oper2')
caso(3, 'no proximo login o pop-up aparece de novo', MODAL in campos_modal(op2)[0])
responder(op2, 'aceito')
caso(3, 'pode aceitar depois; historico mantem a recusa e o aceite', n_manif(OPER2) == 2 and op2.get('/alunos').status_code == 200)

# ─────────────────────────────────────────────────────────────────────────────
# 4. Fecha a aplicacao antes de responder
# ─────────────────────────────────────────────────────────────────────────────
op3 = cliente('t_oper3')
op3.get('/dashboard')
del op3
op3 = cliente('t_oper3')
caso(4, 'nada gravado e o pop-up volta no proximo login', n_manif(OPER3) == 0 and MODAL in campos_modal(op3)[0])

# ─────────────────────────────────────────────────────────────────────────────
# 5. Falha de conexao/gravacao
# ─────────────────────────────────────────────────────────────────────────────
_, f = campos_modal(op3)
f['manifestacao'] = 'aceito'
with mock.patch.object(db.session, 'commit', side_effect=OperationalError('COMMIT', {}, Exception('simulada'))):
    r = op3.post('/lgpd/responder', data=f, follow_redirects=True)
html = r.data.decode('utf-8')
caso(5, 'falha no commit: nada gravado', n_manif(OPER3) == 0)
caso(5, 'falha no commit: interface informa erro, nao "Termo aceito"',
     'Não foi possível registrar' in html and 'Termo aceito' not in html and MODAL in html)
with mock.patch.object(svc, 'versao_vigente', side_effect=OperationalError('SELECT', {}, Exception('db fora'))):
    r = op3.get('/alunos')
caso(5, 'falha ao verificar o termo: acesso negado (503, falha fechada)', r.status_code == 503)
caso(5, 'apos a falha o sistema volta a funcionar', MODAL in campos_modal(op3)[0])

# ─────────────────────────────────────────────────────────────────────────────
# 6. Novo login apos aceite
# ─────────────────────────────────────────────────────────────────────────────
op = cliente('t_oper')
r = op.get('/dashboard')
caso(6, 'novo login nao mostra o pop-up nem gera registro', MODAL not in r.data.decode() and n_manif(OPER) == 1)

# ─────────────────────────────────────────────────────────────────────────────
# 7. Nova versao do termo
# ─────────────────────────────────────────────────────────────────────────────
V2 = publicar('2.0', 'TERMO DE USO E PRIVACIDADE (teste) - versao 2\nNova finalidade incluida.')
op = cliente('t_oper')
html, f = campos_modal(op)
caso(7, 'nova versao: pop-up volta com a versao 2.0', MODAL in html and f.get('termo_versao_id') == str(V2))
r = op.post('/lgpd/responder', data={'csrf_token': f['csrf_token'], 'termo_versao_id': str(V1),
                                     'conteudo_hash': sql('select conteudo_hash from lgpd_termo_versao where id=:i', i=V1),
                                     'manifestacao': 'aceito'})
caso(7, 'aceite enviado para a versao antiga e recusado', n_manif(OPER) == 1)
r = op.post('/lgpd/responder', data={**f, 'conteudo_hash': 'a' * 64, 'manifestacao': 'aceito'})
caso(7, 'aceite com hash divergente do texto exibido e recusado', n_manif(OPER) == 1)
responder(op, 'aceito')
caso(7, 'aceite da 2.0 registrado; aceite da 1.0 preservado',
     n_manif(OPER) == 2 and sql('select count(*) from lgpd_manifestacao where usuario_id=:u and termo_versao_id=:v', u=OPER, v=V1) == 1)

# ─────────────────────────────────────────────────────────────────────────────
# 8. Acesso direto sem aceitar
# ─────────────────────────────────────────────────────────────────────────────
op3 = cliente('t_oper3')
falhou = [r for r in ('/alunos', '/financeiro', '/relatorios', '/monitoramento', '/configuracoes', '/parametros')
          if not (op3.get(r).status_code == 302 and '/dashboard' in (op3.get(r).location or ''))]
caso(8, 'rotas internas redirecionam ao dashboard (pop-up) ' + (str(falhou) if falhou else ''), not falhou)
r = op3.get('/monitoramento/ultimo')
caso(8, 'polling recebe 403 JSON lgpd_pendente', r.status_code == 403 and (r.get_json() or {}).get('erro') == 'lgpd_pendente')
_, f = campos_modal(op3)
op3.post('/lgpd/responder', data={'termo_versao_id': f['termo_versao_id'], 'conteudo_hash': f['conteudo_hash'],
                                  'manifestacao': 'aceito'})
caso(8, 'POST sem token CSRF nao grava', n_manif(OPER3) == 0)
op3.post('/lgpd/responder', data={**f, 'manifestacao': 'talvez'})
caso(8, 'resposta invalida nao grava', n_manif(OPER3) == 0)
anon = app.test_client()
caso(8, 'sem login: /lgpd/responder exige login', '/login' in (anon.post('/lgpd/responder').location or ''))
caso(8, 'FingerPoint (POST /acessos/biometria) continua isento', anon.post('/acessos/biometria', json={'aluno_id': 0}).status_code != 302)

# ─────────────────────────────────────────────────────────────────────────────
# 9. Permissoes
# ─────────────────────────────────────────────────────────────────────────────
op = cliente('t_oper')
r = op.get('/configuracoes/lgpd')
caso(9, 'operador nao acessa Configuracoes > LGPD', r.status_code == 302 and '/dashboard' in r.location)
caso(9, 'operador nao ve a aba LGPD', '/configuracoes/lgpd' not in op.get('/configuracoes').data.decode())
adm = cliente('t_admin')
responder(adm, 'aceito')
html = adm.get('/configuracoes').data.decode()
caso(9, 'admin ve a aba LGPD', '/configuracoes/lgpd' in html)
r = adm.get('/configuracoes/lgpd')
html = r.data.decode()
caso(9, 'admin ve o termo vigente e a tabela de respostas',
     r.status_code == 200 and 'versao 2' in html and 'Não aceito' in html and 't_oper2' in html)
caso(9, 'tela LGPD nao permite alterar o termo (sem formulario de edicao)',
     '<textarea' not in html and adm.post('/configuracoes/lgpd').status_code == 405)
caso(9, 'UPDATE em resposta bloqueado pelo banco', not tenta("update lgpd_manifestacao set manifestacao='aceito'"))
caso(9, 'DELETE em resposta bloqueado pelo banco', not tenta('delete from lgpd_manifestacao'))
caso(9, 'TRUNCATE em resposta bloqueado pelo banco', not tenta('truncate lgpd_manifestacao'))
caso(9, 'UPDATE no texto do termo bloqueado pelo banco', not tenta(f"update lgpd_termo_versao set conteudo='x' where id={V2}"))
caso(9, 'DELETE de versao do termo bloqueado pelo banco', not tenta(f'delete from lgpd_termo_versao where id={V1}'))
caso(9, 'excluir usuario com resposta bloqueado (FK RESTRICT)', not tenta(f'delete from usuarios where id={OPER}'))
caso(9, 'registros intactos', n_manif(OPER) == 2)

# ─────────────────────────────────────────────────────────────────────────────
# 11. Carga
# ─────────────────────────────────────────────────────────────────────────────
with app.test_request_context():
    ids = [u.id for u in Usuario.query.filter(Usuario.usuario.like('t_carga_%')).all()]
    v = svc.versao_vigente()
    t0 = time.perf_counter()
    for u in ids:
        svc.registrar(u, v.id, v.conteudo_hash, 'aceito')
    t_reg = time.perf_counter() - t0
caso(11, f'{len(ids)} aceites registrados em {t_reg:.1f}s', len(ids) >= 395 and t_reg < 30)


def media(c, rota, n=100):
    t0 = time.perf_counter()
    for _ in range(n):
        c.get(rota)
    return (time.perf_counter() - t0) / n * 1000


carga = cliente('t_carga_10')
com = media(carga, '/alunos')
with mock.patch.object(svc, 'migracao_aplicada', return_value=False):
    sem = media(carga, '/alunos')
print(f'   /alunos (400 alunos): sem verificacao {sem:.1f} ms, com verificacao {com:.1f} ms (media de 100)')
caso(11, f'custo da verificacao por requisicao < 50 ms (medido {com - sem:.1f} ms)', com - sem < 50)
t0 = time.perf_counter(); r = adm.get('/configuracoes/lgpd'); t = time.perf_counter() - t0
caso(11, f'Configuracoes > LGPD com {n_manif()} respostas abre em {t:.2f}s', r.status_code == 200 and t < 2)

print(f'\nTOTAL: {len(resultados)} verificacoes, {sum(resultados)} OK, {len(resultados) - sum(resultados)} falhas')
sys.exit(0 if all(resultados) else 1)
