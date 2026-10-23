# -*- coding: utf-8 -*-
"""
Servico LGPD (v1.7.0) — ciencia e consentimento dos usuarios do sistema
(funcionarios). Sem rotas: as rotas ficam em app/routes/lgpd.py.

Conceitos:
- Termo do tipo 'ciencia' (ex.: aviso de privacidade): o titular registra
  "Li e estou ciente". Ciencia NAO e consentimento.
- Termo do tipo 'consentimento': o titular registra "Concordo" ou
  "Nao concordo". Nada e presumido; recusa nao exclui conta nem dados.

A funcionalidade so e habilitada quando a migracao
migrations/001_v1.7.0_lgpd_up.sql foi aplicada (linha '1.7.0_001_lgpd' em
schema_migracao). Sem a migracao, ou com o gate desligado
(configuracoes.lgpd_gate_ativo <> '1'), o sistema se comporta como antes.

Uso do decorator @requer_consentimento (nenhuma rota usa nesta versao):

    from app.routes.auth import login_required
    from app.lgpd import requer_consentimento

    @bp.route('/alguma-funcao')
    @login_required
    @requer_consentimento('codigo_do_termo')
    def alguma_funcao():
        ...

    So faz sentido para termos do tipo 'consentimento' com
    bloqueia_sistema = false (termos que bloqueiam o sistema ja sao
    tratados pelo gate global). A rota so executa se a manifestacao valida
    do usuario para o termo for 'concordo'.

Regra de logs: nunca registrar nomes, conteudo de termos ou manifestacoes
de terceiros em app.logger; apenas ids.
"""

import hashlib
import hmac
import secrets
import unicodedata
from datetime import timezone
from functools import wraps

from flask import (session, request, redirect, url_for, jsonify, flash,
                   current_app, Response, g)
from sqlalchemy import text, select, func, and_, or_, case

from app import db
from app.models import (LgpdTermo, LgpdTermoVersao, LgpdManifestacao,
                        Configuracao, Usuario)


MIGRACAO_VERSAO = '1.7.0_001_lgpd'

TIPOS_TERMO = ('ciencia', 'consentimento')
MANIFESTACOES_POR_TIPO = {
    'ciencia':       ('ciente',),
    'consentimento': ('concordo', 'nao_concordo'),
}
ROTULOS_MANIFESTACAO = {
    'ciente':       'Ciente',
    'concordo':     'Concordo',
    'nao_concordo': 'Não concordo',
    'pendente':     'Pendente',
}
ROTULOS_TIPO = {
    'ciencia':       'Ciência',
    'consentimento': 'Consentimento',
}

# Endpoints nunca bloqueados pelo gate
ENDPOINTS_ISENTOS = frozenset({
    'static',
    'auth.index',
    'auth.login',
    'auth.logout',
    'pagamentos.webhook_cielo',
    'pagamentos.webhook_asaas',
    'acessos.acesso_biometria',     # FingerPoint, sem sessao de usuario
})

# Endpoints do blueprint lgpd voltados ao titular (isentos do gate)
ENDPOINTS_TITULAR = frozenset({
    'lgpd.manifestacao',
    'lgpd.registrar',
    'lgpd.recusado',
    'lgpd.minhas',
    'lgpd.versao_publicada',
})

# Cache de processo: guarda apenas o resultado positivo. Se a migracao
# down for executada com o servico no ar, reinicie o servico.
_migracao_ok = False


class ManifestacaoInvalida(Exception):
    """Erro de validacao ao registrar manifestacao (mensagem exibivel)."""


# ─────────────────────────────────────────────────────────────────────────────
# Hash canonico (§3)
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
# Disponibilidade / configuracao
# ─────────────────────────────────────────────────────────────────────────────

def migracao_aplicada():
    """True se a migracao 1.7.0 foi aplicada. Levanta excecao em erro de
    banco (o chamador decide; o gate falha fechado)."""
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


def _param(chave, padrao=''):
    c = Configuracao.query.filter_by(chave=chave).first()
    return c.valor if c and c.valor is not None else padrao


def gate_ativo():
    return _param('lgpd_gate_ativo', '0') == '1'


def contato_privacidade():
    return (_param('lgpd_contato_privacidade', '') or '').strip()


def fuso_horario():
    try:
        return int(_param('fuso_horario', '-3'))
    except (TypeError, ValueError):
        return -3


def para_utc_naive(dt):
    """Converte datetime aware (timestamptz) para UTC naive, formato que os
    filtros local_time/local_date existentes esperam."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def agora_banco():
    """now() do PostgreSQL (inicio da transacao corrente)."""
    return db.session.execute(select(func.now())).scalar()


# ─────────────────────────────────────────────────────────────────────────────
# Regras de versao / situacao
# ─────────────────────────────────────────────────────────────────────────────

def _ordem_versao():
    return (LgpdTermoVersao.vigencia_inicio.desc(),
            LgpdTermoVersao.publicado_em.desc().nullslast(),
            LgpdTermoVersao.id.desc())


def _versoes_vigentes(termo):
    return LgpdTermoVersao.query.filter(
        LgpdTermoVersao.termo_id == termo.id,
        LgpdTermoVersao.status == 'publicada',
        LgpdTermoVersao.vigencia_inicio <= func.now(),
    )


def versao_vigente(termo):
    """Versao publicada e vigente mais recente (vigencia_inicio, depois
    publicado_em). None se o termo nao tem versao vigente."""
    return _versoes_vigentes(termo).order_by(*_ordem_versao()).first()


def versao_referencia(termo):
    """Versao publicada e vigente mais recente com alteracao_material."""
    return (_versoes_vigentes(termo)
            .filter(LgpdTermoVersao.alteracao_material.is_(True))
            .order_by(*_ordem_versao()).first())


def manifestacao_valida(usuario_id, termo, referencia):
    """Manifestacao mais recente (maior id) do usuario em versoes do termo
    publicadas em ou apos a versao de referencia."""
    if referencia is None:
        return None
    V = LgpdTermoVersao
    M = LgpdManifestacao
    posterior = [V.id == referencia.id,
                 V.vigencia_inicio > referencia.vigencia_inicio]
    if referencia.publicado_em is not None:
        posterior.append(and_(V.vigencia_inicio == referencia.vigencia_inicio,
                              V.publicado_em >= referencia.publicado_em))
    return (db.session.query(M)
            .join(V, V.id == M.termo_versao_id)
            .filter(M.usuario_id == usuario_id,
                    V.termo_id == termo.id,
                    V.status == 'publicada',
                    or_(*posterior))
            .order_by(M.id.desc())
            .first())


def termos_ativos():
    """Termos ativos, ciencia antes de consentimento."""
    return (LgpdTermo.query.filter(LgpdTermo.ativo.is_(True))
            .order_by(case((LgpdTermo.tipo == 'ciencia', 0), else_=1),
                      LgpdTermo.id)
            .all())


def situacao_termo(usuario_id, termo):
    """Situacao do usuario para um termo. None se o termo nao e exigido
    (inativo ou sem versao vigente)."""
    if not termo.ativo:
        return None
    vigente = versao_vigente(termo)
    if vigente is None:
        return None
    referencia = versao_referencia(termo)
    # Fallback defensivo: a primeira versao publicada e sempre material
    # (validado na publicacao); se por algum motivo nao houver versao
    # material vigente, a propria vigente serve de referencia.
    if referencia is None:
        referencia = vigente
    valida = manifestacao_valida(usuario_id, termo, referencia)
    return {
        'termo':        termo,
        'versao':       vigente,
        'referencia':   referencia,
        'manifestacao': valida,
        'situacao':     valida.manifestacao if valida else 'pendente',
    }


def situacao_usuario(usuario_id):
    """Lista de situacoes para todos os termos exigidos (ativos com versao
    vigente), ordem: ciencia antes de consentimento."""
    resultado = []
    with db.session.no_autoflush:
        for termo in termos_ativos():
            s = situacao_termo(usuario_id, termo)
            if s is not None:
                resultado.append(s)
    return resultado


def pendencias(situacoes):
    return [s for s in situacoes if s['situacao'] == 'pendente']


def recusas_bloqueantes(situacoes):
    return [s for s in situacoes
            if s['termo'].tipo == 'consentimento'
            and s['termo'].bloqueia_sistema
            and s['situacao'] == 'nao_concordo']


# ─────────────────────────────────────────────────────────────────────────────
# Registro de manifestacao
# ─────────────────────────────────────────────────────────────────────────────

def registrar_manifestacao(usuario_id, termo_versao_id, conteudo_hash_enviado,
                           manifestacao, canal='web'):
    """Registra a manifestacao do titular em uma unica transacao
    (insert + log_auditoria) e faz commit.

    Retorna 'registrado' ou 'ja_registrado' (idempotente).
    Levanta ManifestacaoInvalida (validacao) ou a excecao de banco; em
    qualquer excecao faz rollback antes de propagar."""
    from app.version import APP_VERSAO
    from app.routes.auth import registrar_log_sem_commit

    try:
        # Serializa por usuario (evita duplicidade por clique duplo)
        bloqueado = db.session.execute(
            select(Usuario.id).where(Usuario.id == usuario_id).with_for_update()
        ).scalar()
        if bloqueado is None:
            raise ManifestacaoInvalida('Usuário não encontrado.')

        with db.session.no_autoflush:
            versao = db.session.get(LgpdTermoVersao, termo_versao_id)
            if versao is None or versao.status != 'publicada':
                raise ManifestacaoInvalida('Versão do termo inválida.')
            termo = db.session.get(LgpdTermo, versao.termo_id)
            if termo is None or not termo.ativo:
                raise ManifestacaoInvalida('Este termo não está ativo.')

            vigente = versao_vigente(termo)
            if vigente is None or vigente.id != versao.id:
                raise ManifestacaoInvalida(
                    'O termo foi atualizado enquanto você lia. Leia a versão atual e responda novamente.')

            enviado = (conteudo_hash_enviado or '').strip().lower()
            if not hmac.compare_digest(enviado.encode('ascii', 'ignore'),
                                       versao.conteudo_hash.strip().encode('ascii')):
                raise ManifestacaoInvalida(
                    'O conteúdo exibido não confere com a versão registrada. Recarregue a página e responda novamente.')

            if manifestacao not in MANIFESTACOES_POR_TIPO.get(termo.tipo, ()):
                raise ManifestacaoInvalida('Resposta inválida para este tipo de termo.')

            referencia = versao_referencia(termo) or vigente
            atual = manifestacao_valida(usuario_id, termo, referencia)
            if atual is not None and atual.manifestacao == manifestacao:
                db.session.rollback()
                return 'ja_registrado'

        db.session.add(LgpdManifestacao(
            usuario_id=usuario_id,
            termo_versao_id=versao.id,
            manifestacao=manifestacao,
            conteudo_hash=versao.conteudo_hash,
            app_versao=APP_VERSAO,
            canal=canal,
        ))
        registrar_log_sem_commit(
            'registrou manifestacao lgpd',
            f'termo_id={termo.id} | termo_versao_id={versao.id}')
        db.session.commit()
        return 'registrado'
    except Exception:
        db.session.rollback()
        raise


# ─────────────────────────────────────────────────────────────────────────────
# Gate (before_request) e decorator
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
    """Falha fechada: nao foi possivel verificar a situacao LGPD."""
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
        '<p style="color:#6c757d;font-size:14px;">Não foi possível verificar sua situação '
        'nos termos de privacidade. Tente novamente em instantes. Se o problema '
        'continuar, procure a administração da academia.</p>'
        '<p><a href="" style="color:#FF6B00;">Tentar novamente</a> &nbsp;·&nbsp; '
        '<a href="/logout" style="color:#6c757d;">Sair</a></p>'
        '</div></body></html>'
    )
    return Response(html, status=503, mimetype='text/html')


def verificar_gate():
    """before_request global. Ver regras na especificacao v1.7.0."""
    endpoint = request.endpoint
    if not endpoint or endpoint in ENDPOINTS_ISENTOS or endpoint in ENDPOINTS_TITULAR:
        return None
    if not session.get('logado'):
        return None

    usuario_id = session.get('usuario_id')
    try:
        if not migracao_aplicada():
            return None
        if not gate_ativo():
            return None
        if not usuario_id:
            # Sessao logada sem id de usuario: nao da para verificar
            return redirect(url_for('auth.logout'))
        situacoes = situacao_usuario(usuario_id)
    except Exception as e:
        db.session.rollback()
        current_app.logger.error('LGPD: falha ao verificar situacao (usuario_id=%s, erro=%s)',
                                 usuario_id, type(e).__name__)
        return resposta_indisponivel()

    g.lgpd_situacoes = situacoes

    if pendencias(situacoes):
        if quer_json():
            return jsonify({'erro': 'lgpd_pendente'}), 403
        return redirect(url_for('lgpd.manifestacao'))

    if recusas_bloqueantes(situacoes):
        # Admin mantem a gestao dos termos mesmo tendo recusado um
        # consentimento (mas nao e isento da pendencia de ciencia).
        if endpoint.startswith('lgpd.admin_') and session.get('perfil') == 'admin':
            return None
        if quer_json():
            return jsonify({'erro': 'lgpd_recusado'}), 403
        return redirect(url_for('lgpd.recusado'))

    return None


def requer_consentimento(codigo):
    """Exige 'concordo' no termo de consentimento `codigo` para executar a
    rota. Sem efeito se a migracao nao foi aplicada, se o gate esta
    desligado, ou se o termo nao e exigido (inexistente, inativo, sem versao
    vigente ou de outro tipo). Usar abaixo de @login_required."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not session.get('logado'):
                return f(*args, **kwargs)
            usuario_id = session.get('usuario_id')
            try:
                if not migracao_aplicada() or not gate_ativo():
                    return f(*args, **kwargs)
                with db.session.no_autoflush:
                    termo = LgpdTermo.query.filter_by(codigo=codigo).first()
                    if termo is None:
                        current_app.logger.warning(
                            'LGPD: requer_consentimento com codigo inexistente: %s', codigo)
                        return f(*args, **kwargs)
                    if termo.tipo != 'consentimento':
                        return f(*args, **kwargs)
                    s = situacao_termo(usuario_id, termo)
            except Exception as e:
                db.session.rollback()
                current_app.logger.error('LGPD: falha em requer_consentimento (usuario_id=%s, erro=%s)',
                                         usuario_id, type(e).__name__)
                return resposta_indisponivel()

            if s is None or s['situacao'] == 'concordo':
                return f(*args, **kwargs)
            if quer_json():
                return jsonify({'erro': 'lgpd_consentimento_necessario', 'termo': codigo}), 403
            if s['situacao'] == 'pendente':
                return redirect(url_for('lgpd.manifestacao', termo=codigo))
            flash('Esta função depende de um consentimento que você não concedeu.', 'warning')
            return redirect(url_for('lgpd.recusado', termo=codigo))
        return decorated_function
    return decorator


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
