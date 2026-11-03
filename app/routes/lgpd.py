# -*- coding: utf-8 -*-
"""
Rotas LGPD (v1.7.0 simplificada)

  lgpd.responder     POST /lgpd/responder        (logado, CSRF)
  lgpd.configuracao  GET  /configuracoes/lgpd    (perfil admin)

Regras de servico em app/lgpd.py. Nunca registrar nomes ou conteudo do
termo em app.logger; em log_auditoria apenas ids.
"""

from flask import (Blueprint, render_template, redirect, url_for, request,
                   flash, session, current_app)

from app import db
from app.models import LgpdTermoVersao, LgpdManifestacao, Usuario
from app.routes.auth import login_required, perfil_required, paginar
from app import lgpd as svc

bp = Blueprint('lgpd', __name__)

MSG_NAO_ACEITO = ('Não foi possível continuar. O aceite do termo é necessário para utilizar '
                  'o sistema. Em caso de dúvidas, procure a administração da academia.')

ROTULOS = {'aceito': 'Aceito', 'nao_aceito': 'Não aceito'}


@bp.app_template_filter('utc_naive')
def filtro_utc_naive(dt):
    """timestamptz (aware) -> UTC naive, para usar antes de local_time /
    local_date: {{ v.publicado_em|utc_naive|local_time }}"""
    return svc.para_utc_naive(dt)


@bp.app_context_processor
def contexto_lgpd():
    return dict(lgpd_csrf_token=svc.csrf_token)


def _encerrar_sessao_recusa():
    session.clear()
    flash(MSG_NAO_ACEITO, 'warning')
    return redirect(url_for('auth.login'))


@bp.route('/lgpd/responder', methods=['POST'])
@login_required
def responder():
    manifestacao = request.form.get('manifestacao', '')

    if not svc.csrf_valido():
        if manifestacao == 'nao_aceito':
            return _encerrar_sessao_recusa()
        flash('Formulário expirado. Tente novamente.', 'danger')
        return redirect(url_for('auth.dashboard'))

    try:
        versao_id = int(request.form.get('termo_versao_id', ''))
    except (TypeError, ValueError):
        versao_id = None

    try:
        if versao_id is None:
            raise svc.ManifestacaoInvalida('Requisição inválida.')
        svc.registrar(session.get('usuario_id'), versao_id,
                      request.form.get('conteudo_hash', ''), manifestacao)
        gravado = True
    except svc.ManifestacaoInvalida as e:
        gravado = False
        erro = str(e)
    except Exception as e:
        gravado = False
        erro = None
        current_app.logger.error('LGPD: falha ao registrar resposta (usuario_id=%s, erro=%s)',
                                 session.get('usuario_id'), type(e).__name__)

    if manifestacao == 'nao_aceito':
        # Registrada ou nao, o usuario nao segue usando o sistema
        return _encerrar_sessao_recusa()

    if gravado:
        flash('Termo aceito.', 'success')
    elif erro:
        flash(f'{erro} Sua resposta não foi registrada.', 'danger')
    else:
        flash('Não foi possível registrar sua resposta. Tente novamente.', 'danger')
    return redirect(url_for('auth.dashboard'))


@bp.route('/configuracoes/lgpd')
@perfil_required('admin')
def configuracao():
    try:
        aplicada = svc.migracao_aplicada()
    except Exception as e:
        db.session.rollback()
        current_app.logger.error('LGPD: falha ao verificar migracao (erro=%s)', type(e).__name__)
        return svc.resposta_indisponivel()

    vigente = None
    pag = None
    if aplicada:
        vigente = svc.versao_vigente()
        pag = paginar(
            db.session.query(LgpdManifestacao, LgpdTermoVersao, Usuario)
            .join(LgpdTermoVersao, LgpdTermoVersao.id == LgpdManifestacao.termo_versao_id)
            .join(Usuario, Usuario.id == LgpdManifestacao.usuario_id)
            .order_by(LgpdManifestacao.registrado_em.desc(), LgpdManifestacao.id.desc()))

    return render_template('lgpd/configuracao_lgpd.html',
                           aplicada=aplicada, vigente=vigente, pag=pag,
                           rotulos=ROTULOS, pagina_ativa='configuracoes')
