# -*- coding: utf-8 -*-
"""
Servico LGPD (v1.7.0 simplificada) — termo unico de privacidade/uso.

- O desenvolvedor publica versoes do termo por script SQL (tools/
  gerar_migracao_termo.py). O texto nao e editavel pela interface.
- Enquanto houver versao vigente sem 'aceito' do usuario logado, o gate
  libera apenas o dashboard (que exibe o modal do termo), a resposta e os
  endpoints isentos; o resto redireciona ao dashboard (ou 403 JSON).
- 'nao_aceito' e registrado e a sessao e encerrada.
- Sem migracao aplicada ou sem versao publicada: comportamento anterior.

Regra de logs: nunca registrar nomes ou conteudo do termo em app.logger;
apenas ids.
"""

import hashlib
import hmac
import secrets
import unicodedata
from datetime import timezone

from flask import session, request, redirect, url_for, jsonify, current_app, Response, g
from sqlalchemy import text, select

from app import db
from app.models import LgpdTermoVersao, LgpdManifestacao, Usuario


MIGRACAO_VERSAO = '1.7.0_001_lgpd'
MANIFESTACOES = ('aceito', 'nao_aceito')

# Endpoints liberados enquanto o aceite esta pendente
ENDPOINTS_LIBERADOS = frozenset({
    'static',
    'auth.index',
    'auth.login',
    'auth.logout',
    'lgpd.responder',
    'pagamentos.webhook_cielo',
    'pagamentos.webhook_asaas',
    'acessos.acesso_biometria',     # FingerPoint, sem sessao de usuario
})
ENDPOINT_DASHBOARD = 'auth.dashboard'  # liberado apenas em GET (exibe o modal)

# Cache de processo: guarda apenas o resultado positivo. Se a migracao
# down for executada com o servico no ar, reinicie o servico.
_migracao_ok = False


class ManifestacaoInvalida(Exception):
    """Erro de validacao ao registrar a resposta (mensagem exibivel)."""


# ─────────────────────────────────────────────────────────────────────────────
# Hash canonico
# ─────────────────────────────────────────────────────────────────────────────

def canonicalizar(texto):
    """\\r\\n e \\r -> \\n; remove espacos/tabs a direita de cada linha;
    remove linhas vazias no fim; Unicode NFC."""
    if texto is None:
        texto = ''
    t = texto.replace('\r\n', '\n').replace('\r', '\n')
    linhas = [linha.rstrip(' \t') for linha in t.split('\n')]
    while linhas and linhas[-1] == '':
        linhas.pop()
    return unicodedata.normalize('NFC', '\n'.join(linhas))


def hash_conteudo(texto):
    """SHA-256 hex do texto canonicalizado, codificado em UTF-8."""
    return hashlib.sha256(canonicalizar(texto).encode('utf-8')).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# Consultas
# ─────────────────────────────────────────────────────────────────────────────

def migracao_aplicada():
    """True se a migracao 1.7.0 foi aplicada. Levanta excecao em erro de
    banco (o gate falha fechado)."""
    global _migracao_ok
    if _migracao_ok:
        return True
    existe = db.session.execute(
        text("SELECT to_regclass('schema_migracao') IS NOT NULL")
    ).scalar()
    if not existe:
        return False
    ok = db.session.execute(
        text("SELECT EXISTS (SELECT 1 FROM schema_migracao WHERE versao = :v)"),
        {'v': MIGRACAO_VERSAO},
    ).scalar()
    if ok:
        _migracao_ok = True
    return bool(ok)


def versao_vigente():
    """Versao de maior publicado_em (desempate: id). None se nao houver."""
    return (LgpdTermoVersao.query
            .order_by(LgpdTermoVersao.publicado_em.desc(), LgpdTermoVersao.id.desc())
            .first())


def ultima_manifestacao(usuario_id, versao_id):
    return (LgpdManifestacao.query
            .filter(LgpdManifestacao.usuario_id == usuario_id,
                    LgpdManifestacao.termo_versao_id == versao_id)
            .order_by(LgpdManifestacao.id.desc())
            .first())


def termo_pendente(usuario_id):
    """Versao vigente se o usuario ainda nao a aceitou; senao None."""
    with db.session.no_autoflush:
        versao = versao_vigente()
        if versao is None:
            return None
        ultima = ultima_manifestacao(usuario_id, versao.id)
    if ultima is not None and ultima.manifestacao == 'aceito':
        return None
    return versao


def pendente(usuario_id):
    return termo_pendente(usuario_id) is not None


def para_utc_naive(dt):
    """timestamptz (aware) -> UTC naive, formato esperado pelos filtros
    local_time/local_date existentes."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


# ─────────────────────────────────────────────────────────────────────────────
# Registro da resposta
# ─────────────────────────────────────────────────────────────────────────────

def registrar(usuario_id, versao_id, hash_enviado, manifestacao):
    """Registra a resposta em uma unica transacao (insert + log_auditoria)
    e faz commit. Retorna 'registrado' ou 'ja_registrado' (idempotente).
    Levanta ManifestacaoInvalida ou a excecao de banco, sempre apos rollback."""
    from app.version import APP_VERSAO
    from app.routes.auth import registrar_log_sem_commit

    try:
        if manifestacao not in MANIFESTACOES:
            raise ManifestacaoInvalida('Resposta inválida.')

        # Serializa por usuario (evita duplicidade por clique duplo)
        bloqueado = db.session.execute(
            select(Usuario.id).where(Usuario.id == usuario_id).with_for_update()
        ).scalar()
        if bloqueado is None:
            raise ManifestacaoInvalida('Usuário não encontrado.')

        with db.session.no_autoflush:
            vigente = versao_vigente()
            if vigente is None or vigente.id != versao_id:
                raise ManifestacaoInvalida(
                    'O termo foi atualizado. Leia a versão atual e responda novamente.')
            enviado = (hash_enviado or '').strip().lower()
            if not hmac.compare_digest(enviado.encode('ascii', 'ignore'),
                                       vigente.conteudo_hash.strip().encode('ascii')):
                raise ManifestacaoInvalida(
                    'O texto exibido não confere com a versão registrada. Recarregue a página.')
            ultima = ultima_manifestacao(usuario_id, vigente.id)
            if ultima is not None and ultima.manifestacao == manifestacao:
                db.session.rollback()
                return 'ja_registrado'

        db.session.add(LgpdManifestacao(
            usuario_id=usuario_id,
            termo_versao_id=vigente.id,
            manifestacao=manifestacao,
            conteudo_hash=vigente.conteudo_hash,
            app_versao=APP_VERSAO,
        ))
        registrar_log_sem_commit('registrou resposta termo lgpd',
                                 f'termo_versao_id={vigente.id}')
        db.session.commit()
        return 'registrado'
    except Exception:
        db.session.rollback()
        raise


# ─────────────────────────────────────────────────────────────────────────────
# Gate (before_request)
# ─────────────────────────────────────────────────────────────────────────────

def quer_json():
    if request.path.startswith('/monitoramento/'):
        return True
    if request.headers.get('X-Requested-With', '').lower() == 'xmlhttprequest':
        return True
    if request.is_json:
        return True
    am = request.accept_mimetypes
    return am.accept_json and not am.accept_html


def resposta_indisponivel():
    """Falha fechada: nao foi possivel verificar o aceite do termo."""
    if quer_json():
        return jsonify({'erro': 'lgpd_indisponivel'}), 503
    html = (
        '<!DOCTYPE html><html lang="pt-br"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
        '<title>Indisponível</title></head>'
        '<body style="font-family:system-ui,sans-serif;background:#f0f2f5;'
        'display:flex;align-items:center;justify-content:center;height:100vh;margin:0;">'
        '<div style="background:#fff;border-radius:16px;padding:32px;max-width:420px;'
        'box-shadow:0 4px 24px rgba(0,0,0,.08);text-align:center;">'
        '<h1 style="font-size:20px;color:#1a1a2e;">Não foi possível verificar</h1>'
        '<p style="color:#6c757d;font-size:14px;">Não foi possível verificar o aceite do '
        'termo de uso. Tente novamente em instantes. Se o problema continuar, '
        'procure a administração da academia.</p>'
        '<p><a href="" style="color:#FF6B00;">Tentar novamente</a> &nbsp;·&nbsp; '
        '<a href="/logout" style="color:#6c757d;">Sair</a></p>'
        '</div></body></html>'
    )
    return Response(html, status=503, mimetype='text/html')


def verificar_gate():
    """before_request global: exige o aceite da versao vigente do termo."""
    endpoint = request.endpoint
    if not endpoint or endpoint in ENDPOINTS_LIBERADOS:
        return None
    if not session.get('logado'):
        return None

    usuario_id = session.get('usuario_id')
    try:
        if not migracao_aplicada():
            return None
        if not usuario_id:
            return redirect(url_for('auth.logout'))
        versao = termo_pendente(usuario_id)
    except Exception as e:
        db.session.rollback()
        current_app.logger.error('LGPD: falha ao verificar aceite (usuario_id=%s, erro=%s)',
                                 usuario_id, type(e).__name__)
        return resposta_indisponivel()

    if versao is None:
        return None

    g.lgpd_termo_pendente = versao
    if endpoint == ENDPOINT_DASHBOARD and request.method == 'GET':
        return None
    if quer_json():
        return jsonify({'erro': 'lgpd_pendente'}), 403
    return redirect(url_for('auth.dashboard'))


# ─────────────────────────────────────────────────────────────────────────────
# CSRF minimo (somente blueprint lgpd)
# ─────────────────────────────────────────────────────────────────────────────

_CHAVE_CSRF = 'lgpd_csrf'


def csrf_token():
    token = session.get(_CHAVE_CSRF)
    if not token:
        token = secrets.token_urlsafe(32)
        session[_CHAVE_CSRF] = token
    return token


def csrf_valido():
    esperado = session.get(_CHAVE_CSRF)
    enviado = request.form.get('csrf_token', '')
    if not esperado or not enviado:
        return False
    return hmac.compare_digest(enviado.encode('utf-8'), esperado.encode('utf-8'))
