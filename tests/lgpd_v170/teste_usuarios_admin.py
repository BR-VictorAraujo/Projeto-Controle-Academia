# -*- coding: utf-8 -*-
"""
Testes da restricao de /usuarios ao perfil admin (v1.7.0).
Mesmo preparo de banco descartavel de teste_cenarios.py (ver README.md).
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

if not os.environ.get('DATABASE_URL', '').rstrip('/').endswith('_teste'):
    sys.exit('ABORTADO: DATABASE_URL precisa apontar para um banco cujo nome termina em _teste.')

from sqlalchemy import text                            # noqa: E402
from werkzeug.security import generate_password_hash   # noqa: E402
from app import create_app, db                         # noqa: E402
from app.models import Usuario                         # noqa: E402

app = create_app()
app.config['TESTING'] = True
SENHA = 'Teste123'
resultados = []


def caso(descricao, condicao):
    resultados.append(bool(condicao))
    print(('OK    ' if condicao else 'FALHA ') + descricao)


def get(login):
    with app.app_context():
        u = Usuario.query.filter_by(usuario=login).first()
        return None if u is None else (u.id, u.perfil, u.ativo, u.nome)


def cliente(login):
    c = app.test_client()
    assert c.post('/login', data={'usuario': login, 'senha': SENHA}).status_code == 302
    return c


with app.app_context():
    # rodar em banco sem versao de termo publicada (sem pop-up), para isolar o teste de perfil
    assert not db.session.execute(text('select count(*) from lgpd_termo_versao')).scalar(), \
        'use um banco sem versao de termo publicada'
    db.session.execute(text("update usuarios set perfil='operador' where perfil='admin' and usuario<>'admin'"))
    db.session.execute(text("update usuarios set ativo=false where usuario='admin'"))
    h = generate_password_hash(SENHA)
    for login, perfil in (('u_admin', 'admin'), ('u_oper', 'operador'), ('u_alvo', 'operador')):
        if not Usuario.query.filter_by(usuario=login).first():
            db.session.add(Usuario(nome=login, usuario=login, senha_hash=h, perfil=perfil, ativo=True))
    db.session.commit()

ADM, OPER, ALVO = get('u_admin')[0], get('u_oper')[0], get('u_alvo')[0]
op = cliente('u_oper')
adm = cliente('u_admin')

# Operador
r = op.get('/usuarios')
caso('operador: GET /usuarios redireciona ao dashboard', r.status_code == 302 and '/dashboard' in r.location)
r = op.get('/usuarios', follow_redirects=True)
caso('operador: mensagem "Acesso restrito"', 'Acesso restrito ao perfil administrador' in r.data.decode())
op.post('/usuarios/novo', data={'nome': 'x', 'usuario': 'u_criado_por_oper', 'senha': SENHA,
                                'confirmar_senha': SENHA, 'perfil': 'admin'})
caso('operador: nao cria usuario', get('u_criado_por_oper') is None)
op.post(f'/usuarios/{OPER}/editar', data={'nome': 'u_oper', 'usuario': 'u_oper', 'perfil': 'admin', 'ativo': '1'})
caso('operador: nao se promove a admin', get('u_oper')[1] == 'operador')
op.get(f'/usuarios/{ALVO}/toggle')
caso('operador: nao desativa outro usuario', get('u_alvo')[2] is True)
anon = app.test_client()
caso('sem login: /usuarios redireciona ao login', '/login' in (anon.get('/usuarios').location or ''))

# Admin: fluxo normal
caso('admin: GET /usuarios 200', adm.get('/usuarios').status_code == 200)
adm.post('/usuarios/novo', data={'nome': 'Novo', 'usuario': 'u_novo', 'senha': SENHA,
                                 'confirmar_senha': SENHA, 'perfil': 'operador'})
caso('admin: cria usuario', get('u_novo') is not None)
adm.post(f'/usuarios/{ALVO}/editar', data={'nome': 'Alvo Editado', 'usuario': 'u_alvo', 'perfil': 'operador', 'ativo': '1'})
caso('admin: edita outro usuario', get('u_alvo')[3] == 'Alvo Editado')
adm.get(f'/usuarios/{ALVO}/toggle')
caso('admin: desativa outro usuario', get('u_alvo')[2] is False)
adm.get(f'/usuarios/{ALVO}/toggle')
caso('admin: reativa outro usuario', get('u_alvo')[2] is True)
adm.post(f'/usuarios/{ADM}/editar', data={'nome': 'Admin Renomeado', 'usuario': 'u_admin', 'perfil': 'admin', 'ativo': '1'})
caso('admin: edita o proprio nome normalmente', get('u_admin')[3] == 'Admin Renomeado')

# Autobloqueio
adm.get(f'/usuarios/{ADM}/toggle')
caso('admin: nao se desativa pelo toggle', get('u_admin')[2] is True)
adm.post(f'/usuarios/{ADM}/editar', data={'nome': 'Admin Renomeado', 'usuario': 'u_admin', 'perfil': 'admin', 'ativo': '0'})
caso('admin: nao se desativa pelo formulario', get('u_admin')[2] is True)
adm.post(f'/usuarios/{ADM}/editar', data={'nome': 'Admin Renomeado', 'usuario': 'u_admin', 'perfil': 'operador', 'ativo': '1'})
caso('admin unico: nao remove o proprio perfil admin', get('u_admin')[1] == 'admin')
adm.post(f'/usuarios/{ALVO}/editar', data={'nome': 'Alvo Editado', 'usuario': 'u_alvo', 'perfil': 'admin', 'ativo': '1'})
caso('admin: promove outro usuario a admin', get('u_alvo')[1] == 'admin')
adm.post(f'/usuarios/{ADM}/editar', data={'nome': 'Admin Renomeado', 'usuario': 'u_admin', 'perfil': 'operador', 'ativo': '1'})
caso('com outro admin ativo: pode deixar de ser admin', get('u_admin')[1] == 'operador')
adm2 = cliente('u_admin')
caso('apos novo login o rebaixado perde o acesso', adm2.get('/usuarios').status_code == 302)

print(f'\nTOTAL: {len(resultados)} verificacoes, {sum(resultados)} OK, {len(resultados) - sum(resultados)} falhas')
sys.exit(0 if all(resultados) else 1)
