# -*- coding: utf-8 -*-
"""
Rotas LGPD (v1.7.0) — url_prefix /privacidade

Titular (qualquer usuario logado; isentas do gate):
  lgpd.manifestacao, lgpd.registrar, lgpd.recusado, lgpd.minhas,
  lgpd.versao_publicada
Administracao (perfil admin): lgpd.admin_*

Regras de servico em app/lgpd.py. Todos os POST deste blueprint exigem o
token CSRF local (campo csrf_token). Nunca registrar nomes, conteudo de
termos ou manifestacoes em app.logger; em log_auditoria apenas ids.
"""

import hashlib
import re
from datetime import datetime, timedelta, timezone

from flask import (Blueprint, render_template, redirect, url_for, request,
                   flash, session, current_app)
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import (LgpdTermo, LgpdTermoVersao, LgpdManifestacao,
                        LgpdTermoAuditoria, Configuracao, Usuario)
from app.routes.auth import (login_required, perfil_required,
                             registrar_log_sem_commit, paginar)
from app import lgpd as svc

bp = Blueprint('lgpd', __name__, url_prefix='/privacidade')

RE_CODIGO = re.compile(r'^[a-z0-9_]+$')

ROTULOS_ACAO = {
    'criou_termo':      'Criou termo',
    'editou_termo':     'Editou termo',
    'criou_versao':     'Criou versão (rascunho)',
    'editou_rascunho':  'Editou rascunho',
    'excluiu_rascunho': 'Excluiu rascunho',
    'publicou_versao':  'Publicou versão',
    'ativou_termo':     'Ativou termo',
    'desativou_termo':  'Desativou termo',
    'alterou_config':   'Alterou configuração',
}


# ─────────────────────────────────────────────────────────────────────────────
# Filtros / contexto de template
# ─────────────────────────────────────────────────────────────────────────────

@bp.app_template_filter('utc_naive')
def filtro_utc_naive(dt):
    """timestamptz (aware) -> UTC naive, para usar antes de local_time /
    local_date: {{ v.publicado_em|utc_naive|local_time }}"""
    return svc.para_utc_naive(dt)


@bp.app_context_processor
def contexto_lgpd():
    disponivel = False
    if session.get('logado'):
        try:
            disponivel = svc.migracao_aplicada()
        except Exception:
            db.session.rollback()
            disponivel = False
    return dict(
        lgpd_menu=disponivel,
        lgpd_csrf_token=svc.csrf_token,
        lgpd_rotulo_manifestacao=svc.ROTULOS_MANIFESTACAO,
        lgpd_rotulo_tipo=svc.ROTULOS_TIPO,
    )


# ─────────────────────────────────────────────────────────────────────────────
# before_request do blueprint: CSRF + migracao aplicada
# ─────────────────────────────────────────────────────────────────────────────

@bp.before_request
def antes_da_requisicao():
    if not session.get('logado'):
        return None  # login_required / perfil_required tratam
    endpoint = request.endpoint or ''
    eh_admin = endpoint.startswith('lgpd.admin_')
    if eh_admin and session.get('perfil') != 'admin':
        return None  # perfil_required trata

    if request.method == 'POST' and not svc.csrf_valido():
        flash('Formulário expirado ou inválido. Tente novamente.', 'danger')
        return redirect(url_for('lgpd.admin_termos' if eh_admin else 'lgpd.minhas'))

    try:
        aplicada = svc.migracao_aplicada()
    except Exception as e:
        db.session.rollback()
        current_app.logger.error('LGPD: falha ao verificar migracao (erro=%s)', type(e).__name__)
        return svc.resposta_indisponivel()

    if not aplicada:
        if eh_admin:
            return render_template('lgpd/admin_indisponivel.html', pagina_ativa='lgpd_admin')
        flash('A área de privacidade ainda não está disponível neste sistema.', 'info')
        return redirect(url_for('auth.dashboard'))
    return None


def _uid():
    return session.get('usuario_id')


def _falha_verificacao(e):
    db.session.rollback()
    current_app.logger.error('LGPD: falha ao consultar situacao (usuario_id=%s, erro=%s)',
                             _uid(), type(e).__name__)
    return svc.resposta_indisponivel()


# ─────────────────────────────────────────────────────────────────────────────
# Titular
# ─────────────────────────────────────────────────────────────────────────────

@bp.route('/manifestacao')
@login_required
def manifestacao():
    codigo  = (request.args.get('termo') or '').strip()
    retorno = 'minhas' if request.args.get('retorno') == 'minhas' else ''
    try:
        situacoes = svc.situacao_usuario(_uid())
        gate = svc.gate_ativo()
    except Exception as e:
        return _falha_verificacao(e)

    pendentes = svc.pendencias(situacoes)
    alvo = None
    if codigo:
        alvo = next((s for s in situacoes if s['termo'].codigo == codigo), None)
        if alvo is None:
            flash('Termo não encontrado ou sem versão vigente.', 'warning')
            return redirect(url_for('lgpd.minhas'))
    elif pendentes:
        alvo = pendentes[0]
    else:
        if gate and svc.recusas_bloqueantes(situacoes):
            return redirect(url_for('lgpd.recusado'))
        return redirect(url_for('auth.dashboard'))

    return render_template('lgpd/manifestacao.html',
                           s=alvo,
                           total_pendentes=len(pendentes),
                           retorno=retorno,
                           gate=gate)


@bp.route('/manifestacao/registrar', methods=['POST'])
@login_required
def registrar():
    codigo  = (request.form.get('termo_codigo') or '').strip()
    retorno = 'minhas' if request.form.get('retorno') == 'minhas' else ''

    def voltar():
        args = {}
        if codigo and RE_CODIGO.match(codigo):
            args['termo'] = codigo
        if retorno:
            args['retorno'] = retorno
        return redirect(url_for('lgpd.manifestacao', **args))

    try:
        termo_versao_id = int(request.form.get('termo_versao_id', ''))
    except (TypeError, ValueError):
        flash('Requisição inválida. Nada foi gravado.', 'danger')
        return voltar()

    try:
        resultado = svc.registrar_manifestacao(
            _uid(), termo_versao_id,
            request.form.get('conteudo_hash', ''),
            request.form.get('manifestacao', ''))
    except svc.ManifestacaoInvalida as e:
        flash(f'{e} Nada foi gravado.', 'danger')
        return voltar()
    except Exception as e:
        current_app.logger.error('LGPD: falha ao registrar manifestacao (usuario_id=%s, erro=%s)',
                                 _uid(), type(e).__name__)
        flash('Não foi possível registrar sua resposta. Nada foi gravado. Tente novamente.', 'danger')
        return voltar()

    if resultado == 'registrado':
        flash('Sua resposta foi registrada.', 'success')
    else:
        flash('Sua resposta já estava registrada.', 'info')

    if retorno == 'minhas':
        return redirect(url_for('lgpd.minhas'))
    return redirect(url_for('lgpd.manifestacao'))


@bp.route('/recusado')
@login_required
def recusado():
    codigo = (request.args.get('termo') or '').strip()
    try:
        situacoes = svc.situacao_usuario(_uid())
        contato = svc.contato_privacidade()
    except Exception as e:
        return _falha_verificacao(e)

    if svc.pendencias(situacoes):
        return redirect(url_for('lgpd.manifestacao'))

    recusas = svc.recusas_bloqueantes(situacoes)
    if codigo:
        extra = next((s for s in situacoes
                      if s['termo'].codigo == codigo
                      and s['termo'].tipo == 'consentimento'
                      and s['situacao'] == 'nao_concordo'), None)
        if extra is not None and extra not in recusas:
            recusas.append(extra)

    if not recusas:
        return redirect(url_for('auth.dashboard'))

    return render_template('lgpd/recusado.html', recusas=recusas, contato=contato)


@bp.route('/minhas')
@login_required
def minhas():
    uid = _uid()
    try:
        situacoes = svc.situacao_usuario(uid)
        historico = paginar(
            db.session.query(LgpdManifestacao, LgpdTermoVersao, LgpdTermo)
            .join(LgpdTermoVersao, LgpdTermoVersao.id == LgpdManifestacao.termo_versao_id)
            .join(LgpdTermo, LgpdTermo.id == LgpdTermoVersao.termo_id)
            .filter(LgpdManifestacao.usuario_id == uid)
            .order_by(LgpdManifestacao.id.desc()))
    except Exception as e:
        return _falha_verificacao(e)

    return render_template('lgpd/minhas.html', situacoes=situacoes, historico=historico)


@bp.route('/versao/<int:versao_id>')
@login_required
def versao_publicada(versao_id):
    v = db.session.get(LgpdTermoVersao, versao_id)
    if v is None or v.status != 'publicada':
        flash('Versão não encontrada.', 'warning')
        return redirect(url_for('lgpd.minhas'))
    return render_template('lgpd/versao_publicada.html', v=v, termo=v.termo)


# ─────────────────────────────────────────────────────────────────────────────
# Administracao — helpers
# ─────────────────────────────────────────────────────────────────────────────

def _sha(texto):
    return hashlib.sha256((texto or '').encode('utf-8')).hexdigest()


def _auditar(acao, termo_id=None, termo_versao_id=None, detalhes=None):
    """Grava lgpd_termo_auditoria + log_auditoria (somente ids) na
    transacao corrente, sem commit."""
    detalhes = detalhes or {}
    db.session.add(LgpdTermoAuditoria(
        termo_id=termo_id,
        termo_versao_id=termo_versao_id,
        acao=acao,
        usuario_id=_uid(),
        detalhes=detalhes,
    ))
    partes = []
    if termo_id:
        partes.append(f'termo_id={termo_id}')
    vid = termo_versao_id or detalhes.get('versao_id')
    if vid:
        partes.append(f'termo_versao_id={vid}')
    registrar_log_sem_commit(f'lgpd {acao}', ' | '.join(partes) or None)


def _set_config(chave, valor, descricao=None):
    c = Configuracao.query.filter_by(chave=chave).first()
    if c:
        c.valor = valor
    else:
        db.session.add(Configuracao(chave=chave, valor=valor, descricao=descricao))


def _tem_versao_publicada(termo_id):
    return db.session.query(
        LgpdTermoVersao.query.filter_by(termo_id=termo_id, status='publicada').exists()
    ).scalar()


def _contem_preencher(texto):
    return '[preencher' in (texto or '').lower()


def _erro_banco(e, mensagem):
    db.session.rollback()
    current_app.logger.error('LGPD admin: %s (erro=%s)', mensagem, type(e).__name__)
    flash(f'{mensagem} Nenhuma alteração foi gravada.', 'danger')


# ─────────────────────────────────────────────────────────────────────────────
# Administracao — termos
# ─────────────────────────────────────────────────────────────────────────────

@bp.route('/admin/termos')
@perfil_required('admin')
def admin_termos():
    termos = LgpdTermo.query.order_by(LgpdTermo.ativo.desc(), LgpdTermo.id).all()
    linhas = []
    with db.session.no_autoflush:
        for t in termos:
            contagem = dict(db.session.query(LgpdTermoVersao.status, func.count())
                            .filter(LgpdTermoVersao.termo_id == t.id)
                            .group_by(LgpdTermoVersao.status).all())
            linhas.append({
                'termo':      t,
                'vigente':    svc.versao_vigente(t),
                'publicadas': contagem.get('publicada', 0),
                'rascunhos':  contagem.get('rascunho', 0),
            })
    return render_template('lgpd/admin_termos.html', linhas=linhas,
                           gate=svc.gate_ativo(), aba='termos',
                           pagina_ativa='lgpd_admin')


def _ler_form_termo(termo=None, travado=False):
    """Le e valida o formulario de termo. Retorna (dados, erros)."""
    f = request.form
    dados = {
        'codigo':           (f.get('codigo') or '').strip().lower(),
        'titulo':           (f.get('titulo') or '').strip(),
        'tipo':             (f.get('tipo') or '').strip(),
        'finalidade':       svc.canonicalizar(f.get('finalidade') or '').strip(),
        'bloqueia_sistema': f.get('bloqueia_sistema') == '1',
        'ativo':            f.get('ativo') == '1',
    }
    if travado and termo is not None:
        # codigo e tipo nao mudam depois que existe versao publicada
        dados['codigo'] = termo.codigo
        dados['tipo'] = termo.tipo
    if dados['tipo'] == 'ciencia':
        # Ciencia pendente sempre bloqueia (gate); a opcao so se aplica a consentimento
        dados['bloqueia_sistema'] = True

    erros = []
    if not dados['codigo'] or len(dados['codigo']) > 40 or not RE_CODIGO.match(dados['codigo']):
        erros.append('Código inválido: use apenas letras minúsculas, números e "_" (até 40 caracteres).')
    if not dados['titulo'] or len(dados['titulo']) > 150:
        erros.append('Informe o título (até 150 caracteres).')
    if dados['tipo'] not in svc.TIPOS_TERMO:
        erros.append('Tipo inválido.')
    if not dados['finalidade']:
        erros.append('Informe a finalidade.')
    if not erros:
        with db.session.no_autoflush:
            existente = LgpdTermo.query.filter_by(codigo=dados['codigo']).first()
        if existente is not None and (termo is None or existente.id != termo.id):
            erros.append('Já existe um termo com esse código.')
    return dados, erros


@bp.route('/admin/termos/novo', methods=['GET', 'POST'])
@perfil_required('admin')
def admin_termo_novo():
    dados = {'codigo': '', 'titulo': '', 'tipo': 'ciencia', 'finalidade': '',
             'bloqueia_sistema': True, 'ativo': True}
    if request.method == 'POST':
        dados, erros = _ler_form_termo()
        if erros:
            for e in erros:
                flash(e, 'danger')
        else:
            try:
                termo = LgpdTermo(**dados)
                db.session.add(termo)
                db.session.flush()
                _auditar('criou_termo', termo_id=termo.id, detalhes={
                    'codigo': termo.codigo, 'tipo': termo.tipo,
                    'bloqueia_sistema': termo.bloqueia_sistema, 'ativo': termo.ativo,
                    'finalidade_hash': _sha(termo.finalidade),
                })
                db.session.commit()
                flash('Termo criado. Agora crie a primeira versão.', 'success')
                return redirect(url_for('lgpd.admin_versoes', termo_id=termo.id))
            except IntegrityError as e:
                _erro_banco(e, 'Não foi possível criar o termo (código duplicado ou dado inválido).')
            except Exception as e:
                _erro_banco(e, 'Não foi possível criar o termo.')
    return render_template('lgpd/admin_termo_form.html', dados=dados, termo=None,
                           travado=False, aba='termos', pagina_ativa='lgpd_admin')


@bp.route('/admin/termos/<int:termo_id>/editar', methods=['GET', 'POST'])
@perfil_required('admin')
def admin_termo_editar(termo_id):
    termo = db.session.get(LgpdTermo, termo_id)
    if termo is None:
        flash('Termo não encontrado.', 'warning')
        return redirect(url_for('lgpd.admin_termos'))
    travado = _tem_versao_publicada(termo.id)
    dados = {'codigo': termo.codigo, 'titulo': termo.titulo, 'tipo': termo.tipo,
             'finalidade': termo.finalidade, 'bloqueia_sistema': termo.bloqueia_sistema,
             'ativo': termo.ativo}

    if request.method == 'POST':
        novos, erros = _ler_form_termo(termo, travado)
        if erros:
            for e in erros:
                flash(e, 'danger')
            dados = novos
        else:
            campos = [k for k in novos if novos[k] != dados[k]]
            if not campos:
                flash('Nenhuma alteração.', 'info')
                return redirect(url_for('lgpd.admin_termos'))
            try:
                antes = dict(dados)
                for k in campos:
                    setattr(termo, k, novos[k])
                detalhes = {'campos': campos}
                for k in campos:
                    if k in ('titulo', 'finalidade'):
                        detalhes[f'{k}_hash_antes'] = _sha(antes[k])
                        detalhes[f'{k}_hash_depois'] = _sha(novos[k])
                    else:
                        detalhes[f'{k}_antes'] = antes[k]
                        detalhes[f'{k}_depois'] = novos[k]
                campos_sem_ativo = [c for c in campos if c != 'ativo']
                if campos_sem_ativo:
                    _auditar('editou_termo', termo_id=termo.id, detalhes=detalhes)
                if 'ativo' in campos:
                    _auditar('ativou_termo' if novos['ativo'] else 'desativou_termo',
                             termo_id=termo.id, detalhes={'ativo_antes': antes['ativo'],
                                                          'ativo_depois': novos['ativo']})
                db.session.commit()
                flash('Termo atualizado.', 'success')
                return redirect(url_for('lgpd.admin_termos'))
            except IntegrityError as e:
                _erro_banco(e, 'Não foi possível salvar o termo (código duplicado ou dado inválido).')
                dados = novos
            except Exception as e:
                _erro_banco(e, 'Não foi possível salvar o termo.')
                dados = novos

    return render_template('lgpd/admin_termo_form.html', dados=dados, termo=termo,
                           travado=travado, aba='termos', pagina_ativa='lgpd_admin')


# ─────────────────────────────────────────────────────────────────────────────
# Administracao — versoes
# ─────────────────────────────────────────────────────────────────────────────

@bp.route('/admin/termos/<int:termo_id>/versoes')
@perfil_required('admin')
def admin_versoes(termo_id):
    termo = db.session.get(LgpdTermo, termo_id)
    if termo is None:
        flash('Termo não encontrado.', 'warning')
        return redirect(url_for('lgpd.admin_termos'))
    versoes = (LgpdTermoVersao.query.filter_by(termo_id=termo.id)
               .order_by(LgpdTermoVersao.status.desc(),  # rascunho antes
                         LgpdTermoVersao.vigencia_inicio.desc().nullsfirst(),
                         LgpdTermoVersao.id.desc())
               .all())
    vigente = svc.versao_vigente(termo)
    referencia = svc.versao_referencia(termo)
    return render_template('lgpd/admin_versoes.html', termo=termo, versoes=versoes,
                           vigente=vigente, referencia=referencia,
                           aba='termos', pagina_ativa='lgpd_admin')


def _ler_form_versao(termo, versao_atual=None):
    f = request.form
    dados = {
        'versao':             (f.get('versao') or '').strip(),
        'conteudo':           svc.canonicalizar(f.get('conteudo') or ''),
        'alteracao_material': f.get('alteracao_material') == '1',
        'resumo_alteracoes':  svc.canonicalizar(f.get('resumo_alteracoes') or '').strip() or None,
    }
    erros = []
    if not dados['versao'] or len(dados['versao']) > 20:
        erros.append('Informe a versão (até 20 caracteres).')
    if not dados['conteudo'].strip():
        erros.append('Informe o conteúdo do termo.')
    if not erros:
        with db.session.no_autoflush:
            existente = LgpdTermoVersao.query.filter_by(termo_id=termo.id,
                                                        versao=dados['versao']).first()
        if existente is not None and (versao_atual is None or existente.id != versao_atual.id):
            erros.append('Já existe uma versão com esse número para este termo.')
    return dados, erros


@bp.route('/admin/termos/<int:termo_id>/versoes/nova', methods=['GET', 'POST'])
@perfil_required('admin')
def admin_versao_nova(termo_id):
    termo = db.session.get(LgpdTermo, termo_id)
    if termo is None:
        flash('Termo não encontrado.', 'warning')
        return redirect(url_for('lgpd.admin_termos'))

    dados = {'versao': '', 'conteudo': '', 'alteracao_material': True, 'resumo_alteracoes': ''}
    copiada_de = None
    copiar = request.values.get('copiar', type=int)
    if copiar:
        origem = db.session.get(LgpdTermoVersao, copiar)
        if origem is not None and origem.termo_id == termo.id:
            copiada_de = origem
            if request.method == 'GET':
                dados['conteudo'] = origem.conteudo

    if request.method == 'POST':
        dados, erros = _ler_form_versao(termo)
        if erros:
            for e in erros:
                flash(e, 'danger')
        else:
            try:
                v = LgpdTermoVersao(
                    termo_id=termo.id,
                    versao=dados['versao'],
                    conteudo=dados['conteudo'],
                    conteudo_hash=svc.hash_conteudo(dados['conteudo']),
                    alteracao_material=dados['alteracao_material'],
                    resumo_alteracoes=dados['resumo_alteracoes'],
                    status='rascunho',
                    criado_por=_uid(),
                )
                db.session.add(v)
                db.session.flush()
                # Rascunho pode ser excluido: a auditoria guarda o id em
                # detalhes (termo_versao_id NULL) para nao travar a exclusao
                # pela FK RESTRICT.
                _auditar('criou_versao', termo_id=termo.id, detalhes={
                    'versao_id': v.id, 'versao': v.versao, 'hash': v.conteudo_hash,
                    'alteracao_material': v.alteracao_material,
                    'copiada_de_versao_id': copiada_de.id if copiada_de else None,
                })
                db.session.commit()
                flash('Rascunho criado.', 'success')
                return redirect(url_for('lgpd.admin_versoes', termo_id=termo.id))
            except IntegrityError as e:
                _erro_banco(e, 'Não foi possível criar a versão (número duplicado ou dado inválido).')
            except Exception as e:
                _erro_banco(e, 'Não foi possível criar a versão.')

    return render_template('lgpd/admin_versao_form.html', termo=termo, v=None, dados=dados,
                           copiada_de=copiada_de, aba='termos', pagina_ativa='lgpd_admin')


def _carregar_rascunho(versao_id):
    v = db.session.get(LgpdTermoVersao, versao_id)
    if v is None:
        flash('Versão não encontrada.', 'warning')
        return None, redirect(url_for('lgpd.admin_termos'))
    if v.status != 'rascunho':
        flash('Versão publicada não pode ser alterada nem excluída. Crie uma nova versão.', 'warning')
        return None, redirect(url_for('lgpd.admin_versoes', termo_id=v.termo_id))
    return v, None


@bp.route('/admin/versoes/<int:versao_id>/editar', methods=['GET', 'POST'])
@perfil_required('admin')
def admin_versao_editar(versao_id):
    v, resp = _carregar_rascunho(versao_id)
    if resp:
        return resp
    termo = v.termo
    dados = {'versao': v.versao, 'conteudo': v.conteudo,
             'alteracao_material': v.alteracao_material,
             'resumo_alteracoes': v.resumo_alteracoes or ''}

    if request.method == 'POST':
        novos, erros = _ler_form_versao(termo, v)
        if erros:
            for e in erros:
                flash(e, 'danger')
            dados = novos
        else:
            antes = {'versao': v.versao, 'conteudo': v.conteudo,
                     'alteracao_material': v.alteracao_material,
                     'resumo_alteracoes': v.resumo_alteracoes}
            campos = [k for k in novos if novos[k] != antes[k]]
            if not campos:
                flash('Nenhuma alteração.', 'info')
                return redirect(url_for('lgpd.admin_versoes', termo_id=termo.id))
            try:
                hash_antes = v.conteudo_hash
                v.versao = novos['versao']
                v.conteudo = novos['conteudo']
                v.conteudo_hash = svc.hash_conteudo(novos['conteudo'])
                v.alteracao_material = novos['alteracao_material']
                v.resumo_alteracoes = novos['resumo_alteracoes']
                _auditar('editou_rascunho', termo_id=termo.id, detalhes={
                    'versao_id': v.id, 'campos': campos,
                    'hash_antes': hash_antes, 'hash_depois': v.conteudo_hash,
                })
                db.session.commit()
                flash('Rascunho atualizado.', 'success')
                return redirect(url_for('lgpd.admin_versoes', termo_id=termo.id))
            except IntegrityError as e:
                _erro_banco(e, 'Não foi possível salvar o rascunho (número duplicado ou dado inválido).')
                dados = novos
            except Exception as e:
                _erro_banco(e, 'Não foi possível salvar o rascunho.')
                dados = novos

    return render_template('lgpd/admin_versao_form.html', termo=termo, v=v, dados=dados,
                           copiada_de=None, aba='termos', pagina_ativa='lgpd_admin')


@bp.route('/admin/versoes/<int:versao_id>/excluir', methods=['POST'])
@perfil_required('admin')
def admin_versao_excluir(versao_id):
    v, resp = _carregar_rascunho(versao_id)
    if resp:
        return resp
    termo_id = v.termo_id
    try:
        _auditar('excluiu_rascunho', termo_id=termo_id, detalhes={
            'versao_id': v.id, 'versao': v.versao, 'hash': v.conteudo_hash,
        })
        db.session.delete(v)
        db.session.commit()
        flash('Rascunho excluído.', 'success')
    except Exception as e:
        _erro_banco(e, 'Não foi possível excluir o rascunho.')
    return redirect(url_for('lgpd.admin_versoes', termo_id=termo_id))


def _problemas_publicacao(v, nova_vigencia):
    """Lista de impedimentos para publicar v com a vigencia informada."""
    problemas = []
    if v.status != 'rascunho':
        problemas.append('Esta versão já foi publicada.')
    if _contem_preencher(v.conteudo):
        problemas.append('O texto ainda contém trechos "[PREENCHER". Edite o rascunho antes de publicar.')
    if svc.canonicalizar(v.conteudo) != v.conteudo or svc.hash_conteudo(v.conteudo) != (v.conteudo_hash or '').strip():
        problemas.append('O hash armazenado não confere com o conteúdo. Abra o rascunho e salve novamente.')
    if not (v.conteudo or '').strip():
        problemas.append('O conteúdo está vazio.')
    # A primeira versao publicada (menor vigencia) e sempre material
    if nova_vigencia is not None:
        menor = (db.session.query(func.min(LgpdTermoVersao.vigencia_inicio))
                 .filter(LgpdTermoVersao.termo_id == v.termo_id,
                         LgpdTermoVersao.status == 'publicada',
                         LgpdTermoVersao.id != v.id)
                 .scalar())
        if (menor is None or nova_vigencia < menor) and not v.alteracao_material:
            problemas.append('A primeira versão publicada de um termo deve ser marcada como '
                             'alteração material. Edite o rascunho.')
    return problemas


@bp.route('/admin/versoes/<int:versao_id>/publicar', methods=['GET', 'POST'])
@perfil_required('admin')
def admin_versao_publicar(versao_id):
    v = db.session.get(LgpdTermoVersao, versao_id)
    if v is None:
        flash('Versão não encontrada.', 'warning')
        return redirect(url_for('lgpd.admin_termos'))
    termo = v.termo
    if v.status != 'rascunho':
        flash('Esta versão já foi publicada.', 'info')
        return redirect(url_for('lgpd.admin_versoes', termo_id=termo.id))

    fuso = svc.fuso_horario()

    if request.method == 'POST':
        modo = request.form.get('modo_vigencia', 'imediata')
        try:
            # Serializa publicacoes do mesmo termo
            db.session.execute(select(LgpdTermo.id).where(LgpdTermo.id == termo.id).with_for_update())
            db.session.refresh(v)
            agora = svc.agora_banco()

            erros = []
            if request.form.get('confirmo') != '1':
                erros.append('Confirme que revisou o texto antes de publicar.')
            if modo == 'agendada':
                bruto = (request.form.get('vigencia_inicio') or '').strip()
                try:
                    local = datetime.strptime(bruto, '%Y-%m-%dT%H:%M')
                    nova_vig = local.replace(tzinfo=timezone(timedelta(hours=fuso)))
                    if nova_vig <= agora:
                        erros.append('A vigência agendada deve ser no futuro.')
                except ValueError:
                    nova_vig = None
                    erros.append('Informe data e hora de início da vigência.')
            else:
                nova_vig = agora
            erros += _problemas_publicacao(v, nova_vig)

            if erros:
                db.session.rollback()
                for e in erros:
                    flash(e, 'danger')
                return redirect(url_for('lgpd.admin_versao_publicar', versao_id=versao_id))

            v.status = 'publicada'
            v.vigencia_inicio = nova_vig
            v.publicado_por = _uid()
            v.publicado_em = func.now()
            _auditar('publicou_versao', termo_id=termo.id, termo_versao_id=v.id, detalhes={
                'versao': v.versao, 'hash': v.conteudo_hash,
                'alteracao_material': v.alteracao_material,
                'vigencia_inicio': nova_vig.isoformat(),
                'modo_vigencia': 'agendada' if modo == 'agendada' else 'imediata',
            })
            db.session.commit()
            flash(f'Versão {v.versao} publicada.', 'success')
            return redirect(url_for('lgpd.admin_versoes', termo_id=termo.id))
        except Exception as e:
            _erro_banco(e, 'Não foi possível publicar a versão.')
            return redirect(url_for('lgpd.admin_versao_publicar', versao_id=versao_id))

    try:
        problemas = _problemas_publicacao(v, svc.agora_banco())
    except Exception as e:
        _erro_banco(e, 'Não foi possível verificar a versão.')
        return redirect(url_for('lgpd.admin_versoes', termo_id=termo.id))
    sugestao = (datetime.utcnow() + timedelta(hours=fuso, days=1)).strftime('%Y-%m-%dT%H:00')
    return render_template('lgpd/admin_versao_publicar.html', termo=termo, v=v,
                           problemas=problemas, sugestao_vigencia=sugestao,
                           vigente=svc.versao_vigente(termo),
                           aba='termos', pagina_ativa='lgpd_admin')


@bp.route('/admin/versoes/<int:versao_id>')
@perfil_required('admin')
def admin_versao_ver(versao_id):
    v = db.session.get(LgpdTermoVersao, versao_id)
    if v is None:
        flash('Versão não encontrada.', 'warning')
        return redirect(url_for('lgpd.admin_termos'))
    hash_recalculado = hashlib.sha256((v.conteudo or '').encode('utf-8')).hexdigest()
    criado_por = db.session.get(Usuario, v.criado_por) if v.criado_por else None
    publicado_por = db.session.get(Usuario, v.publicado_por) if v.publicado_por else None
    total_manifestacoes = (db.session.query(func.count(LgpdManifestacao.id))
                           .filter(LgpdManifestacao.termo_versao_id == v.id).scalar())
    return render_template('lgpd/admin_versao_ver.html', termo=v.termo, v=v,
                           hash_confere=(hash_recalculado == (v.conteudo_hash or '').strip()),
                           hash_recalculado=hash_recalculado,
                           criado_por=criado_por, publicado_por=publicado_por,
                           total_manifestacoes=total_manifestacoes,
                           aba='termos', pagina_ativa='lgpd_admin')


# ─────────────────────────────────────────────────────────────────────────────
# Administracao — manifestacoes, situacao, auditoria, configuracao
# ─────────────────────────────────────────────────────────────────────────────

@bp.route('/admin/manifestacoes')
@perfil_required('admin')
def admin_manifestacoes():
    f_usuario = request.args.get('usuario_id', type=int)
    f_termo = request.args.get('termo_id', type=int)
    f_manif = request.args.get('manifestacao', '')

    q = (db.session.query(LgpdManifestacao, LgpdTermoVersao, LgpdTermo, Usuario)
         .join(LgpdTermoVersao, LgpdTermoVersao.id == LgpdManifestacao.termo_versao_id)
         .join(LgpdTermo, LgpdTermo.id == LgpdTermoVersao.termo_id)
         .join(Usuario, Usuario.id == LgpdManifestacao.usuario_id))
    if f_usuario:
        q = q.filter(LgpdManifestacao.usuario_id == f_usuario)
    if f_termo:
        q = q.filter(LgpdTermo.id == f_termo)
    if f_manif in svc.ROTULOS_MANIFESTACAO and f_manif != 'pendente':
        q = q.filter(LgpdManifestacao.manifestacao == f_manif)
    pag = paginar(q.order_by(LgpdManifestacao.id.desc()))

    return render_template('lgpd/admin_manifestacoes.html', pag=pag,
                           usuarios=Usuario.query.order_by(Usuario.nome).all(),
                           termos=LgpdTermo.query.order_by(LgpdTermo.titulo).all(),
                           f_usuario=f_usuario, f_termo=f_termo, f_manif=f_manif,
                           aba='manifestacoes', pagina_ativa='lgpd_admin')


@bp.route('/admin/situacao')
@perfil_required('admin')
def admin_situacao():
    somente_pendentes = request.args.get('pendentes') == '1'
    incluir_inativos = request.args.get('inativos') == '1'
    q = Usuario.query
    if not incluir_inativos:
        q = q.filter(Usuario.ativo.is_(True))
    usuarios = q.order_by(Usuario.nome).all()

    termos_exigidos = []
    linhas = []
    with db.session.no_autoflush:
        for t in svc.termos_ativos():
            if svc.versao_vigente(t) is not None:
                termos_exigidos.append(t)
        for u in usuarios:
            por_termo = {s['termo'].id: s for s in svc.situacao_usuario(u.id)}
            pend = any(s['situacao'] == 'pendente' for s in por_termo.values())
            recusa = any(s['situacao'] == 'nao_concordo' for s in por_termo.values())
            if somente_pendentes and not (pend or recusa):
                continue
            linhas.append({'usuario': u, 'por_termo': por_termo,
                           'pendente': pend, 'recusa': recusa})

    return render_template('lgpd/admin_situacao.html', linhas=linhas,
                           termos=termos_exigidos, somente_pendentes=somente_pendentes,
                           incluir_inativos=incluir_inativos,
                           aba='situacao', pagina_ativa='lgpd_admin')


@bp.route('/admin/auditoria')
@perfil_required('admin')
def admin_auditoria():
    pag = paginar(
        db.session.query(LgpdTermoAuditoria, LgpdTermo, Usuario)
        .outerjoin(LgpdTermo, LgpdTermo.id == LgpdTermoAuditoria.termo_id)
        .outerjoin(Usuario, Usuario.id == LgpdTermoAuditoria.usuario_id)
        .order_by(LgpdTermoAuditoria.id.desc()))
    return render_template('lgpd/admin_auditoria.html', pag=pag, rotulos=ROTULOS_ACAO,
                           aba='auditoria', pagina_ativa='lgpd_admin')


@bp.route('/admin/configuracao', methods=['GET', 'POST'])
@perfil_required('admin')
def admin_config():
    if request.method == 'POST':
        gate_antes = svc.gate_ativo()
        contato_antes = svc.contato_privacidade()
        gate_depois = request.form.get('lgpd_gate_ativo') == '1'
        contato_depois = (request.form.get('lgpd_contato_privacidade') or '').strip()

        erros = []
        if len(contato_depois) > 200:
            erros.append('O contato de privacidade deve ter até 200 caracteres.')
        if gate_depois and not gate_antes:
            existe = db.session.query(
                db.session.query(LgpdTermoVersao)
                .join(LgpdTermo, LgpdTermo.id == LgpdTermoVersao.termo_id)
                .filter(LgpdTermo.ativo.is_(True), LgpdTermoVersao.status == 'publicada')
                .exists()
            ).scalar()
            if not existe:
                erros.append('Para ligar a exigência é preciso ter ao menos um termo ativo '
                             'com versão publicada.')
        if erros:
            for e in erros:
                flash(e, 'danger')
            return redirect(url_for('lgpd.admin_config'))

        campos = []
        detalhes = {}
        if gate_depois != gate_antes:
            campos.append('lgpd_gate_ativo')
            detalhes['lgpd_gate_ativo_antes'] = '1' if gate_antes else '0'
            detalhes['lgpd_gate_ativo_depois'] = '1' if gate_depois else '0'
        if contato_depois != contato_antes:
            campos.append('lgpd_contato_privacidade')
        if not campos:
            flash('Nenhuma alteração.', 'info')
            return redirect(url_for('lgpd.admin_config'))
        detalhes['campos'] = campos
        try:
            if 'lgpd_gate_ativo' in campos:
                _set_config('lgpd_gate_ativo', '1' if gate_depois else '0',
                            'LGPD: exigir ciência/consentimento após o login (1/0)')
            if 'lgpd_contato_privacidade' in campos:
                _set_config('lgpd_contato_privacidade', contato_depois,
                            'LGPD: canal de contato exibido ao titular')
            _auditar('alterou_config', detalhes=detalhes)
            db.session.commit()
            flash('Configuração salva.', 'success')
        except Exception as e:
            _erro_banco(e, 'Não foi possível salvar a configuração.')
        return redirect(url_for('lgpd.admin_config'))

    return render_template('lgpd/admin_config.html',
                           gate=svc.gate_ativo(), contato=svc.contato_privacidade(),
                           aba='config', pagina_ativa='lgpd_admin')
