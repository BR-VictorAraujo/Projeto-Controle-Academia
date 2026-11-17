"""
Módulo API para Pagamentos — Cobrança Recorrente via Cartão (Cielo).

Namespace: /api/v1/pagamentos/*

Duas famílias de rota aqui dentro:
  - Rotas operadas pela recepção (atrás de login_required, sessão normal)
  - Webhook da Cielo (Basic Auth própria, NUNCA login_required de sessão,
    porque quem chama é a Cielo, não um usuário logado)
"""

from flask import Blueprint, request, jsonify, current_app, render_template, has_request_context
from functools import wraps
from datetime import date, timedelta, datetime
from dateutil.relativedelta import relativedelta
from decimal import Decimal, InvalidOperation
import hmac
import re

from sqlalchemy.exc import IntegrityError

from app import db
from app.models import Aluno, Plano, AssinaturaRecorrente, Pagamento, LogIntegracaoPagamento, Configuracao
from app.routes.auth import login_required, registrar_log, get_param
from app import cielo_client
from app.cielo_client import CieloClientError, CieloAuthError, CieloComunicacaoError, chave_pedido_valida
from app import asaas_client
from app.asaas_client import AsaasClientError

bp = Blueprint('pagamentos', __name__, url_prefix='/api/v1/pagamentos')


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────

def modulo_recorrencia_ativo():
    """Gate no padrão tecnologia_ativa() já usado no projeto — permite
    desligar a funcionalidade rapidamente via Configuracao, sem deploy."""
    return get_param('modulo_recorrencia_ativo', '1') == '1'


def gateway_ativo():
    """Retorna qual gateway ('cielo' ou 'asaas') está configurado como
    ativo para NOVAS assinaturas — lido de Configuracao, ajustável pela
    tela de Integrações sem precisar de deploy."""
    config = Configuracao.query.filter_by(chave='gateway_pagamento_recorrente').first()
    return config.valor if config and config.valor in ('cielo', 'asaas') else 'cielo'


def _cliente_do_gateway(gateway):
    """Resolve qual módulo cliente usar baseado no gateway informado —
    usado tanto na criação (gateway ativo) quanto em ações sobre
    assinaturas existentes (gateway salvo na própria linha, que pode
    ser diferente do ativo atual se o cliente já trocou de gateway)."""
    if gateway == 'asaas':
        return asaas_client
    return cielo_client


def asaas_webhook_auth_required(f):
    """Autenticação nativa da Asaas — ela envia o token configurado no
    painel Asaas de volta no header 'asaas-access-token' em toda
    notificação. Mais simples que o esquema Basic Auth que montamos pra
    Cielo, porque a própria Asaas suporta esse mecanismo nativamente."""
    @wraps(f)
    def decorated(*args, **kwargs):
        token_recebido = request.headers.get('asaas-access-token')
        token_esperado = current_app.config.get('ASAAS_WEBHOOK_TOKEN')

        autenticado = (
            token_recebido is not None
            and token_esperado is not None
            and hmac.compare_digest(token_recebido, token_esperado)
        )
        if not autenticado:
            _log('webhook_recebido', nivel='WARNING',
                 resultado=f'Tentativa de acesso não autenticada (Asaas). IP: {request.remote_addr}')
            return jsonify({'erro': 'não autorizado'}), 401
        return f(*args, **kwargs)
    return decorated


def webhook_auth_required(f):
    """Basic Auth própria do webhook — NÃO usa login_required de sessão,
    já que quem chama essa rota é a Cielo, não um usuário logado."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        user_esperado = current_app.config.get('CIELO_WEBHOOK_USER')
        pass_esperado = current_app.config.get('CIELO_WEBHOOK_PASS')

        autenticado = (
            auth is not None
            and user_esperado is not None
            and pass_esperado is not None
            and hmac.compare_digest(auth.username, user_esperado)
            and hmac.compare_digest(auth.password, pass_esperado)
        )
        if not autenticado:
            _log('webhook_recebido', nivel='WARNING',
                 resultado=f'Tentativa de acesso não autenticada. IP: {request.remote_addr}')
            return jsonify({'erro': 'não autorizado'}), 401
        return f(*args, **kwargs)
    return decorated


def _log(tipo_evento, nivel='INFO', resultado=None, payload=None,
         assinatura_recorrente_id=None, recurrent_payment_id=None, payment_id_gateway=None):
    try:
        log = LogIntegracaoPagamento(
            tipo_evento=tipo_evento, nivel=nivel, resultado=resultado,
            payload_bruto=payload, assinatura_recorrente_id=assinatura_recorrente_id,
            recurrent_payment_id=recurrent_payment_id, payment_id_gateway=payment_id_gateway,
            ip_origem=request.remote_addr if has_request_context() else None,
        )
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        # v1.7.2: sem rollback a sessão ficava inválida (PendingRollbackError)
        # e o resto da requisição falhava com 500
        try:
            db.session.rollback()
        except Exception:
            pass
        # v1.7.2: só o tipo do erro (a mensagem do banco pode trazer o payload)
        current_app.logger.error(f"Falha ao gravar log_integracao_pagamento: {type(e).__name__}")


def _avancar_data(data_base, meses=1):
    return data_base + relativedelta(months=meses)


def _novo_vencimento(venc_atual, data_cobranca):
    """v1.7.2 — vencimento do aluno após uma cobrança paga. Nunca regride.

    Regra: novo vencimento = o mais tarde entre o vencimento atual e a data
    da cobrança, + 1 mês.
      - duas cobranças nunca valem o mesmo mês (cada uma soma um mês);
      - quem paga adiantado não perde dias (conta a partir do vencimento atual);
      - aluno com vencimento já passado volta a contar da data da cobrança.
    Testes: tests/pagamentos_v172/teste_regra_vencimento.py."""
    base = max(venc_atual or data_cobranca, data_cobranca)
    candidato = _avancar_data(base, meses=1)
    return candidato if venc_atual is None or candidato > venc_atual else venc_atual


# ─────────────────────────────────────────────────────────────────────────
# Rotas operadas pela recepção
# ─────────────────────────────────────────────────────────────────────────

@bp.route('/assinaturas', methods=['POST'])
@login_required
def criar_assinatura():
    if not modulo_recorrencia_ativo():
        return jsonify({'erro': 'Módulo de cobrança recorrente está desativado.'}), 403

    aluno_id = request.form.get('aluno_id', type=int)
    if not aluno_id:
        return jsonify({'erro': 'aluno_id é obrigatório'}), 400

    aluno = Aluno.query.get(aluno_id)
    if not aluno:
        return jsonify({'erro': 'Aluno não encontrado'}), 404

    # Idempotência (ponto 5 da revisão) — barra se já existir link pendente
    # ou recorrência ativa para esse aluno
    existente = AssinaturaRecorrente.query.filter(
        AssinaturaRecorrente.aluno_id == aluno_id,
        AssinaturaRecorrente.status.in_(['link_enviado', 'ativa', 'aguardando_decisao_ciclo'])
    ).first()
    if existente:
        return jsonify({
            'erro': 'Já existe uma assinatura pendente ou ativa para este aluno.',
            'assinatura_id': existente.id,
            'status': existente.status,
        }), 409

    # Plano resolvido por nome (alunos.plano é texto livre — ver decisão [DBA])
    plano = Plano.query.filter_by(nome=aluno.plano, ativo=True).first()
    if not plano:
        return jsonify({'erro': f'Plano "{aluno.plano}" não encontrado ou inativo.'}), 400
    if not plano.permite_recorrencia:
        return jsonify({'erro': f'Plano "{plano.nome}" não está habilitado para cobrança recorrente.'}), 400

    # Ponto 1 da revisão: data de início alinhada ao vencimento ATUAL do
    # aluno, não uma data arbitrária — evita desalinhar o ciclo já pago
    data_inicio = aluno.vencimento or date.today()

    # Plano com duração > 31 dias (Trimestral, Semestral, Anual) ganha
    # data_fim_ciclo; Mensal (30 dias) fica sem prazo (renovação indefinida)
    data_fim_ciclo = None
    if plano.dias_validade and plano.dias_validade > 31:
        data_fim_ciclo = data_inicio + timedelta(days=plano.dias_validade)

    gateway = gateway_ativo()
    cliente = _cliente_do_gateway(gateway)

    try:
        resultado_gateway = cliente.criar_link_recorrencia(
            aluno=aluno,
            valor_mensal=plano.valor,
            data_inicio=data_inicio,
            data_fim_ciclo=data_fim_ciclo,
        )
    except (CieloClientError, AsaasClientError) as e:
        return jsonify({'erro': f'Falha ao gerar link na {gateway.capitalize()}: {e}'}), 502

    assinatura = AssinaturaRecorrente(
        aluno_id=aluno.id,
        plano_id=plano.id,
        gateway=gateway,
        link_pagamento_id=resultado_gateway.get('link_pagamento_id'),
        url_link_pagamento=resultado_gateway.get('url_link'),
        order_number=resultado_gateway.get('order_number'),
        status='link_enviado',
        valor_mensal=plano.valor,
        data_inicio=data_inicio,
        data_fim_ciclo=data_fim_ciclo,
    )
    db.session.add(assinatura)
    db.session.commit()

    registrar_log('criar_assinatura_recorrente',
                   f'Aluno {aluno.nome} (ID {aluno.id}) — plano {plano.nome} — gateway {gateway}')

    return jsonify({
        'assinatura_id': assinatura.id,
        'status': assinatura.status,
        'gateway': gateway,
        'url_link': resultado_gateway.get('url_link'),
    }), 201


@bp.route('/assinaturas/aluno/<int:aluno_id>', methods=['GET'])
@login_required
def obter_assinatura_aluno(aluno_id):
    """Retorna a assinatura mais recente do aluno (qualquer status),
    usada pela ficha do aluno para renderizar o card de recorrência."""
    assinatura = AssinaturaRecorrente.query.filter_by(aluno_id=aluno_id).order_by(
        AssinaturaRecorrente.criado_em.desc()
    ).first()

    if not assinatura:
        return jsonify({'existe': False}), 200

    ultimo_pagamento = Pagamento.query.filter_by(
        assinatura_recorrente_id=assinatura.id,
        origem='recorrente_gateway',
        status='pago',
    ).order_by(Pagamento.data_pagamento.desc(), Pagamento.id.desc()).first()

    ultimo_pagamento_info = None
    if ultimo_pagamento:
        ultimo_pagamento_info = {
            'id': ultimo_pagamento.id,
            'valor': float(ultimo_pagamento.valor),
            'data_pagamento': ultimo_pagamento.data_pagamento.isoformat() if ultimo_pagamento.data_pagamento else None,
            'pode_estornar': assinatura.gateway == 'asaas',
        }

    return jsonify({
        'existe': True,
        'id': assinatura.id,
        'status': assinatura.status,
        'gateway': assinatura.gateway,
        'url_link_pagamento': assinatura.url_link_pagamento,
        'valor_mensal': float(assinatura.valor_mensal),
        'data_inicio': assinatura.data_inicio.isoformat() if assinatura.data_inicio else None,
        'data_fim_ciclo': assinatura.data_fim_ciclo.isoformat() if assinatura.data_fim_ciclo else None,
        'proxima_cobranca': assinatura.proxima_cobranca.isoformat() if assinatura.proxima_cobranca else None,
        'tentativas_negadas': assinatura.tentativas_negadas,
        'ultimo_pagamento': ultimo_pagamento_info,
    }), 200


@bp.route('/assinaturas/<int:assinatura_id>', methods=['DELETE'])
@login_required
def cancelar_assinatura(assinatura_id):
    from flask import session

    assinatura = AssinaturaRecorrente.query.get_or_404(assinatura_id)

    if assinatura.status not in ('ativa', 'link_enviado', 'aguardando_decisao_ciclo'):
        return jsonify({'erro': f'Assinatura já está em status "{assinatura.status}", não pode ser cancelada.'}), 400

    if assinatura.link_pagamento_id or getattr(assinatura, 'recurrent_payment_id', None):
        cliente = _cliente_do_gateway(assinatura.gateway)
        try:
            if assinatura.gateway == 'asaas':
                if assinatura.status == 'link_enviado' and not assinatura.recurrent_payment_id:
                    # Aluno ainda não preencheu o link — não existe
                    # 'subscription' no Asaas ainda, só o link em si.
                    try:
                        cliente.desativar_link_pagamento(assinatura.link_pagamento_id)
                    except AsaasClientError as e:
                        if 'invalid_action' in str(e) and 'cobranças geradas' in str(e):
                            # O link já gerou cobrança de verdade (aluno
                            # aderiu, mas nosso webhook não tinha capturado
                            # isso ainda — provavelmente por falha de
                            # autenticação anterior). Não dá pra cancelar
                            # via link nesse caso; precisa ser feito
                            # manualmente no painel Asaas até sincronizarmos.
                            return jsonify({'erro': (
                                'Este link já gerou uma cobrança/assinatura real na Asaas, mas nosso '
                                'sistema ainda não tinha essa informação sincronizada. Cancele '
                                'manualmente pelo painel Asaas (Cobranças > Assinaturas) por enquanto, '
                                'e nos avise para investigarmos a sincronização.'
                            )}), 409
                        raise
                else:
                    # Aluno já aderiu — existe assinatura de verdade a cancelar.
                    cliente.desativar_assinatura(assinatura.recurrent_payment_id)
            else:
                if assinatura.status == 'link_enviado' and not assinatura.recurrent_payment_id:
                    # Aluno ainda não preencheu o link — exclui o link,
                    # não existe recorrência de verdade ainda pra desativar.
                    cliente.excluir_link_pagamento(assinatura.link_pagamento_id)
                else:
                    # Aluno já aderiu — desativa a recorrência (irreversível na Cielo).
                    try:
                        cliente.desativar_recorrencia(assinatura.recurrent_payment_id)
                    except CieloClientError as e:
                        if 'not activated' in str(e).lower():
                            # A Cielo já considera essa recorrência inativa
                            # (provavelmente já desativada antes, e nosso
                            # banco ficou dessincronizado por causa de um
                            # pagamento tardio que reativou por engano —
                            # ver correção em _processar_pagamento_confirmado).
                            # Tratamos como sucesso: só sincronizamos o status.
                            _log('cancelamento_solicitado', nivel='WARNING',
                                 assinatura_recorrente_id=assinatura.id,
                                 recurrent_payment_id=assinatura.recurrent_payment_id,
                                 resultado='Cielo já considerava esta recorrência inativa — banco sincronizado sem erro.')
                        else:
                            raise
        except (CieloClientError, AsaasClientError) as e:
            return jsonify({'erro': f'Falha ao cancelar na {assinatura.gateway.capitalize()}: {e}'}), 502

    # Efeito NÃO é imediato — aluno mantém acesso até o fim do ciclo já
    # pago (aluno.vencimento não é tocado aqui, só a assinatura é encerrada)
    assinatura.status = 'cancelada'
    assinatura.cancelado_em = db.func.now()
    assinatura.cancelado_por = session.get('usuario', 'sistema')
    db.session.commit()

    registrar_log('cancelar_assinatura_recorrente',
                   f'Assinatura {assinatura.id} — aluno {assinatura.aluno_id}')

    return jsonify({'status': 'cancelada'}), 200


@bp.route('/assinaturas/<int:assinatura_id>', methods=['PUT'])
@login_required
def atualizar_assinatura(assinatura_id):
    """Troca de plano — atualiza valor e/ou data fim na Cielo sem recriar a recorrência."""
    assinatura = AssinaturaRecorrente.query.get_or_404(assinatura_id)

    if assinatura.status != 'ativa':
        return jsonify({'erro': 'Só é possível alterar assinaturas ativas.'}), 400

    novo_plano_id = request.form.get('plano_id', type=int)
    if not novo_plano_id:
        return jsonify({'erro': 'plano_id é obrigatório'}), 400

    novo_plano = Plano.query.get_or_404(novo_plano_id)
    if not novo_plano.permite_recorrencia:
        return jsonify({'erro': f'Plano "{novo_plano.nome}" não está habilitado para cobrança recorrente.'}), 400

    identificador = assinatura.recurrent_payment_id or assinatura.link_pagamento_id

    try:
        if novo_plano.valor != assinatura.valor_mensal:
            if assinatura.gateway == 'asaas':
                asaas_client.atualizar_valor_link(identificador, novo_plano.valor)
            else:
                cielo_client.atualizar_valor_recorrencia(identificador, novo_plano.valor)

        nova_data_fim = None
        if novo_plano.dias_validade and novo_plano.dias_validade > 31:
            nova_data_fim = date.today() + timedelta(days=novo_plano.dias_validade)
        if nova_data_fim != assinatura.data_fim_ciclo:
            if assinatura.gateway == 'cielo':
                cielo_client.atualizar_data_fim_recorrencia(identificador, nova_data_fim)
            # TODO CONFIRMAR: endpoint equivalente de data fim no Asaas
            # ainda não validado — por enquanto só o valor é sincronizado
            # via API para assinaturas Asaas; data fim exige checagem manual.
    except (CieloClientError, AsaasClientError) as e:
        return jsonify({'erro': f'Falha ao atualizar na {assinatura.gateway.capitalize()}: {e}'}), 502

    assinatura.plano_id = novo_plano.id
    assinatura.valor_mensal = novo_plano.valor
    assinatura.data_fim_ciclo = nova_data_fim
    db.session.commit()

    registrar_log('atualizar_assinatura_recorrente',
                   f'Assinatura {assinatura.id} — novo plano {novo_plano.nome}')

    return jsonify({'status': 'atualizada'}), 200


# ─────────────────────────────────────────────────────────────────────────
# Telas HTML — Painel de Recorrências e Painel de Log/Auditoria
# ─────────────────────────────────────────────────────────────────────────

@bp.route('/recorrencias', methods=['GET'])
@login_required
def painel_recorrencias():
    status_filtro = request.args.get('status', 'todos')
    busca = request.args.get('f_busca', '').strip()
    pagina = request.args.get('pagina', 1, type=int)
    por_pagina = request.args.get('por_pagina', 20, type=int)

    query = AssinaturaRecorrente.query.join(Aluno, AssinaturaRecorrente.aluno_id == Aluno.id)

    if status_filtro != 'todos':
        query = query.filter(AssinaturaRecorrente.status == status_filtro)
    if busca:
        query = query.filter(Aluno.nome.ilike(f'%{busca}%'))

    query = query.order_by(AssinaturaRecorrente.criado_em.desc())
    pag = query.paginate(page=pagina, per_page=por_pagina, error_out=False)

    # Último pagamento confirmado de cada assinatura da página atual —
    # usado pra habilitar o botão de estorno na linha certa. Só considera
    # pagamentos dos últimos 30 dias — janela interna definida pela
    # academia (a Asaas permite até 365 dias, mas restringimos aqui por
    # segurança/simplicidade operacional).
    limite_estorno = date.today() - timedelta(days=30)
    ultimos_pagamentos = {}
    for a in pag.items:
        p = Pagamento.query.filter_by(
            assinatura_recorrente_id=a.id, origem='recorrente_gateway', status='pago'
        ).order_by(Pagamento.data_pagamento.desc(), Pagamento.id.desc()).first()
        if p and p.data_pagamento and p.data_pagamento >= limite_estorno:
            ultimos_pagamentos[a.id] = p

    contagens = {
        'todos':                      AssinaturaRecorrente.query.count(),
        'ativa':                      AssinaturaRecorrente.query.filter_by(status='ativa').count(),
        'bloqueada_inadimplencia':    AssinaturaRecorrente.query.filter_by(status='bloqueada_inadimplencia').count(),
        'aguardando_decisao_ciclo':   AssinaturaRecorrente.query.filter_by(status='aguardando_decisao_ciclo').count(),
        'link_enviado':               AssinaturaRecorrente.query.filter_by(status='link_enviado').count(),
    }

    return render_template(
        'pagamentos/recorrencias.html',
        pag=pag,
        status_filtro=status_filtro,
        contagens=contagens,
        ultimos_pagamentos=ultimos_pagamentos,
        opcoes_por_pagina=[10, 20, 50, 100],
    )


@bp.route('/recorrencias/logs', methods=['GET'])
@login_required
def painel_logs():
    nivel_filtro = request.args.get('nivel', 'todos')
    busca = request.args.get('f_busca', '').strip()
    pagina = request.args.get('pagina', 1, type=int)
    por_pagina = request.args.get('por_pagina', 30, type=int)

    query = LogIntegracaoPagamento.query

    if nivel_filtro != 'todos':
        query = query.filter(LogIntegracaoPagamento.nivel == nivel_filtro)
    if busca:
        # v1.7.2: busca também pelo texto do resultado (nome do aluno, pedido)
        # e pelo aluno da assinatura (nome ou número)
        query = query.outerjoin(
            AssinaturaRecorrente, LogIntegracaoPagamento.assinatura_recorrente_id == AssinaturaRecorrente.id
        ).outerjoin(Aluno, AssinaturaRecorrente.aluno_id == Aluno.id)
        condicoes = [
            LogIntegracaoPagamento.recurrent_payment_id.ilike(f'%{busca}%'),
            LogIntegracaoPagamento.payment_id_gateway.ilike(f'%{busca}%'),
            LogIntegracaoPagamento.resultado.ilike(f'%{busca}%'),
            Aluno.nome.ilike(f'%{busca}%'),
        ]
        numero = busca.lstrip('#')
        if numero.isdigit():
            condicoes.append(Aluno.id == int(numero))
        query = query.filter(db.or_(*condicoes))

    query = query.order_by(LogIntegracaoPagamento.criado_em.desc())
    pag = query.paginate(page=pagina, per_page=por_pagina, error_out=False)

    # Payload exibido com CPF, e-mail e telefone mascarados (o banco guarda o original)
    payloads = {log.id: _mascarar_payload(log.payload_bruto) for log in pag.items if log.payload_bruto}

    contagens = {
        'todos':   LogIntegracaoPagamento.query.count(),
        'ERROR':   LogIntegracaoPagamento.query.filter_by(nivel='ERROR').count(),
        'WARNING': LogIntegracaoPagamento.query.filter_by(nivel='WARNING').count(),
        'INFO':    LogIntegracaoPagamento.query.filter_by(nivel='INFO').count(),
    }

    return render_template(
        'pagamentos/logs.html',
        pag=pag,
        nivel_filtro=nivel_filtro,
        contagens=contagens,
        payloads=payloads,
        opcoes_por_pagina=[20, 30, 50, 100],
    )


# Chaves de payload com dado pessoal que não aparecem na tela (comparação sem
# '_' e sem maiúsculas, por trecho). '*_type' (ex.: customer_identity_type) fica.
_TRECHOS_PESSOAIS = ('identity', 'cpf', 'cnpj', 'document', 'email', 'phone', 'telefone',
                     'celular', 'mobile')
_RE_EMAIL = re.compile(r'[^@\s]+@[^@\s]+\.[^@\s]+')


def _mascarar_payload(valor, chave=''):
    """Cópia do payload para exibição, com CPF/documento, e-mail e telefone mascarados."""
    if isinstance(valor, dict):
        return {k: _mascarar_payload(v, str(k)) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_mascarar_payload(v, chave) for v in valor]
    nome = chave.lower().replace('_', '')
    if valor not in (None, '') and not nome.endswith('type') and any(t in nome for t in _TRECHOS_PESSOAIS):
        return '***'
    if isinstance(valor, str) and _RE_EMAIL.search(valor):
        return _RE_EMAIL.sub('***', valor)
    return valor


@bp.route('/pagamentos-estorno/<int:pagamento_id>/estornar', methods=['POST'])
@login_required
def estornar_pagamento(pagamento_id):
    """Estorno sempre disparado manualmente pela recepção — nunca automático.
    Só suportado para pagamentos de origem 'recorrente_gateway' via Asaas
    (a Cielo não teve esse recurso confirmado via API; nesse caso o
    estorno continua sendo feito manualmente fora do sistema, como
    definido na análise original)."""
    pagamento = Pagamento.query.get_or_404(pagamento_id)

    if pagamento.origem != 'recorrente_gateway':
        return jsonify({'erro': 'Estorno pelo sistema só disponível para pagamentos via cobrança recorrente.'}), 400

    if pagamento.status != 'pago':
        return jsonify({'erro': 'Só é possível estornar pagamentos já confirmados.'}), 400

    limite_estorno = date.today() - timedelta(days=30)
    if not pagamento.data_pagamento or pagamento.data_pagamento < limite_estorno:
        return jsonify({'erro': (
            'Este pagamento passou da janela de 30 dias permitida para estorno pelo sistema. '
            'Procure a administração da academia para avaliar o caso manualmente.'
        )}), 400

    assinatura = pagamento.assinatura_recorrente
    gateway = assinatura.gateway if assinatura else None

    if gateway != 'asaas':
        return jsonify({'erro': (
            'Este pagamento foi feito via Cielo, e o estorno não pode ser feito automaticamente '
            'pelo sistema. Procure a administração da academia para realizar o estorno manualmente '
            'junto à Cielo.'
        )}), 400

    if not pagamento.payment_id_gateway:
        return jsonify({'erro': 'Este pagamento não tem identificador do gateway registrado — não é possível estornar automaticamente.'}), 400

    try:
        asaas_client.estornar_pagamento(pagamento.payment_id_gateway)
    except AsaasClientError as e:
        return jsonify({'erro': f'Falha ao estornar na Asaas: {e}'}), 502

    pagamento.status = 'pendente'
    pagamento.observacao = (pagamento.observacao or '') + (
        f' [ESTORNADO na Asaas em {datetime.utcnow().strftime("%d/%m/%Y %H:%M")} — '
        f'valor devolvido ao aluno. Confirme e exclua este lançamento no Financeiro.]'
    )
    db.session.commit()

    registrar_log('estornou pagamento', f'Pagamento {pagamento.id} — aluno {pagamento.aluno_id} — R$ {pagamento.valor}')

    return jsonify({'status': 'estornado'}), 200


# ─────────────────────────────────────────────────────────────────────────
# v1.7.2 — Helpers dos webhooks: chave do pedido, trava, datas e frases
# legíveis para a tela de logs. No servico.log NUNCA vão dados pessoais
# (aluno só pelo id; nada de nome, e-mail, telefone, CPF ou cartão).
# ─────────────────────────────────────────────────────────────────────────

# Chave única de cada cobrança no aviso da Cielo. O nome do campo mudou:
# 'checkout_cielo_order_number' (até out/2026) e 'meucheckout_order_number'
# (reenvios desde nov/2026); mesmo valor/formato (20 ou 32 hex). É o mesmo
# 'orderNumber' da consulta GET /products/{id}/payments.
CAMPOS_CHAVE_CIELO = ('checkout_cielo_order_number', 'meucheckout_order_number')

STATUS_CIELO_CONFIRMADO = ('2',)
STATUS_CIELO_NEGADO = ('3', '10', '13')

# payment_status do Checkout/Link de Pagamento (só para exibir no log)
NOMES_STATUS_CIELO = {
    '1': 'Pendente', '2': 'Pago', '3': 'Negado', '4': 'Expirado', '5': 'Cancelado',
    '6': 'Não finalizado', '7': 'Autorizado', '8': 'Chargeback',
}

# payment_method_brand do Checkout Cielo (só para exibir no log)
BANDEIRAS_CIELO = {
    '1': 'Visa', '2': 'Mastercard', '3': 'American Express', '4': 'Diners', '5': 'Elo',
    '6': 'Aura', '7': 'JCB', '8': 'Discover', '9': 'Hipercard',
}

# Códigos de retorno ABECS mais comuns (errorCode da consulta GET /v2/orders/{id})
MOTIVOS_RECUSA = {
    '51': 'saldo/limite insuficiente',
    '05': 'não autorizada pelo banco',
    '54': 'cartão vencido',
    '57': 'transação não permitida para o cartão',
    '14': 'cartão inválido',
    '41': 'cartão perdido/roubado',
    '43': 'cartão perdido/roubado',
    '59': 'suspeita de fraude',
    '61': 'valor excede o limite',
    '62': 'cartão restrito',
    '78': 'cartão bloqueado',
    '91': 'emissor indisponível',
    '96': 'falha no sistema',
    'N7': 'código de segurança inválido',
}

# Chaves de motivo/retorno que, se algum dia vierem no aviso, são exibidas
# (comparação sem '_' e sem maiúsculas: return_code, returnMessage, ...)
CAMPOS_MOTIVO_AVISO = {'returncode', 'returnmessage', 'reason', 'paymentstatusdescription'}

TEXTO_SEM_MOTIVO = 'motivo não informado pela {gateway} no aviso'

# Reconciliação (item 9): janela de busca e de proteção contra lançamentos sem chave
RECONCILIACAO_DIAS = 40
RECONCILIACAO_JANELA_SEM_CHAVE = 25
STATUS_RECONCILIACAO = ('ativa', 'bloqueada_inadimplencia')

# Status de encerramento da assinatura (models.AssinaturaRecorrente.status):
# pagamento tardio não reativa; recusa não conta tentativa nem bloqueia.
STATUS_ENCERRADA = ('cancelada', 'expirada_cartao')
NOMES_STATUS_ENCERRADA = {'cancelada': 'cancelada', 'expirada_cartao': 'encerrada (cartão expirado)'}


def _hoje_local():
    """Data de hoje no fuso configurado (Fortaleza = -3), mesmo critério de acessos.py."""
    fuso = int(get_param('fuso_horario', '-3'))
    return (datetime.utcnow() + timedelta(hours=fuso)).date()


def _chave_pedido_cielo(dados):
    """Chave do pedido, só se estiver no formato da Cielo (20 ou 32 hex).
    Fora do padrão é tratada como aviso sem chave."""
    for campo in CAMPOS_CHAVE_CIELO:
        valor = dados.get(campo)
        if valor is not None and chave_pedido_valida(str(valor).strip()):
            return str(valor).strip()
    return None


def _chave_cielo_fora_do_padrao(dados):
    """True se o aviso traz o campo da chave preenchido, mas fora do padrão."""
    preenchida = any(dados.get(c) is not None and str(dados.get(c)).strip() for c in CAMPOS_CHAVE_CIELO)
    return preenchida and _chave_pedido_cielo(dados) is None


def _status_cielo_txt(payment_status):
    nome = NOMES_STATUS_CIELO.get(str(payment_status)) if payment_status is not None else None
    return f'{payment_status} ({nome})' if nome else str(payment_status)


def _travar_assinatura(assinatura_id):
    """SELECT ... FOR UPDATE da assinatura: avisos simultâneos da mesma
    assinatura (o completo e o curto chegam com 1-5 s de diferença, e a
    reconciliação pode rodar ao mesmo tempo) passam a ser processados um
    de cada vez. A trava dura até o próximo commit/rollback.
    populate_existing: relê a linha mesmo que já esteja na sessão."""
    return (AssinaturaRecorrente.query.filter_by(id=assinatura_id)
            .with_for_update().populate_existing().first())


def _data_cobranca_cielo(dados):
    """Data da cobrança na Cielo: 'created_date' do aviso completo, sempre
    DD/MM/AAAA HH:MM:SS (ex.: "11/11/2026 03:45:43"). start_date/end_date vêm
    em MM/DD e NÃO são usados. Sem created_date (aviso curto) ou fora do
    formato: hoje. Retorna (data, observação ou None)."""
    hoje = _hoje_local()
    bruto = dados.get('created_date')
    bruto = bruto.strip() if isinstance(bruto, str) else ''
    if not bruto:
        return hoje, 'aviso sem created_date; usada a data de hoje'
    for formato in ('%d/%m/%Y %H:%M:%S', '%d/%m/%Y'):
        try:
            data = datetime.strptime(bruto, formato).date()
        except ValueError:
            continue
        if data > hoje + timedelta(days=1):
            # Data no futuro = provável troca de dia/mês: não confia
            return hoje, f'created_date "{bruto}" no futuro; usada a data de hoje'
        return data, None
    return hoje, f'created_date "{bruto}" fora do formato DD/MM/AAAA; usada a data de hoje'


def _data_cobranca_asaas(pagamento):
    """Asaas manda datas AAAA-MM-DD. Ordem: confirmação, pagamento, pagamento
    informado pelo cliente, vencimento da cobrança; sem nenhuma: hoje."""
    for campo in ('confirmedDate', 'paymentDate', 'clientPaymentDate', 'dueDate'):
        bruto = pagamento.get(campo)
        if bruto:
            try:
                return datetime.strptime(str(bruto)[:10], '%Y-%m-%d').date(), None
            except ValueError:
                continue
    return _hoje_local(), 'aviso sem data de pagamento; usada a data de hoje'


def _data_iso(bruto):
    """'2026-11-11T03:45:43' ou '2026-09-11T11:28:30.317' (horário local) → date."""
    try:
        return datetime.strptime(str(bruto)[:19], '%Y-%m-%dT%H:%M:%S').date()
    except (TypeError, ValueError):
        return None


def _reais(valor):
    if valor is None:
        return 'R$ —'
    texto = f'{Decimal(valor):,.2f}'
    return 'R$ ' + texto.replace(',', 'X').replace('.', ',').replace('X', '.')


def _centavos(valor):
    try:
        return Decimal(str(valor)) / 100 if valor not in (None, '') else None
    except (InvalidOperation, ValueError):
        return None


def _dt(d):
    return d.strftime('%d/%m/%Y') if d else '—'


def _cartao_cielo(dados):
    mascarado = str(dados.get('payment_maskedcreditcard') or dados.get('cardMaskedNumber') or '')
    bandeira = BANDEIRAS_CIELO.get(str(dados.get('payment_method_brand') or ''))
    if not bandeira:
        b = dados.get('brand')
        bandeira = b if b and b != 'Undefined' else None
    final = mascarado[-4:] if len(mascarado) >= 4 and mascarado[-4:].isdigit() else None
    if final and bandeira:
        return f'cartão final {final} ({bandeira})'
    if final:
        return f'cartão final {final}'
    return f'bandeira {bandeira}' if bandeira else None


def _cartao_asaas(pagamento):
    cartao = pagamento.get('creditCard') or {}
    final = cartao.get('creditCardNumber')
    bandeira = cartao.get('creditCardBrand')
    if final and bandeira:
        return f'cartão final {final} ({bandeira})'
    return f'cartão final {final}' if final else None


def _motivo_no_aviso(dados):
    achados = [f'{k}={v}' for k, v in (dados or {}).items()
               if k.lower().replace('_', '') in CAMPOS_MOTIVO_AVISO and v not in (None, '')]
    return '; '.join(achados) if achados else None


def _aluno_txt(aluno):
    return f'{aluno.nome} (#{aluno.id})' if aluno else 'aluno não identificado'


def _ref(ctx):
    partes = []
    if ctx.get('order_number'):
        partes.append(f'pedido {ctx["order_number"]}')
    if ctx.get('chave'):
        partes.append(f'chave {ctx["chave"]}')
    if ctx.get('cartao'):
        partes.append(ctx['cartao'])
    return ', '.join(partes) if partes else 'sem identificação do pedido'


def _arq(gateway, mensagem, nivel='info'):
    """Linha no servico.log. NUNCA passar nome/e-mail/telefone/CPF/cartão."""
    getattr(current_app.logger, nivel)(f'[pagamentos] {gateway}: {mensagem}')


# ─────────────────────────────────────────────────────────────────────────
# Webhook Cielo — SEM autenticação (limitação do produto)
# ─────────────────────────────────────────────────────────────────────────

@bp.route('/webhooks/cielo', methods=['GET', 'POST'])
def webhook_cielo():
    # GET existe só para o botão "Testar" do painel Cielo confirmar que
    # a URL está viva — não processa nada, só responde 200 OK.
    if request.method == 'GET':
        return jsonify({'status': 'ok'}), 200

    # SEM autenticação — a Cielo não oferece mecanismo de auth pra essa
    # notificação (confirmado na doc: "não há implementação de
    # autenticação para o envio de notificações").
    #
    # Payload REAL (Checkout Cielo / Link de Pagamento, recorrência). Os
    # campos RecurrentPaymentId/PaymentId/ChangeType da doc genérica NÃO
    # existem nesta linha de produto (corrigido em 02/09/2026).
    #
    # v1.7.2 — confirmado com dados de produção: a Cielo manda DOIS avisos
    # por cobrança, no mesmo endpoint, com 1-5 s de intervalo:
    #   completo:
    #   {"nsu": "051002", "tid": "28769360595NP9TK55HE", "amount": "6000",
    #    "end_date": "08/10/2027 00:00:00", "interval": "Mensal",
    #    "product_id": "<link id>", "start_date": "09/11/2026 11:28:30",
    #    "created_date": "11/11/2026 03:45:43", "order_number": "assin3131789133860",
    #    "product_type": "5", "shipping_type": "5", "payment_status": "2",
    #    "shipping_price": "0", "recurrent_status": "Ativa", "test_transaction": "False",
    #    "authorization_code": "011173", "payment_method_type": "1",
    #    "product_description": "...", "payment_installments": "1",
    #    "payment_method_brand": "1", "customer_identity_type": "0",
    #    "meucheckout_order_number": "a02c1169a9e34c62882c37f2e3ba8c7f",
    #    "payment_maskedcreditcard": "415275******2128",
    #    "pagador_recurrent_payment_id": "ec6f46ce-5425-4df3-a9d3-60ac9d0e2c02"}
    #   curto (1-2 s depois):
    #   {"nsu": "051002", "brand": "Undefined", "amount": "6000",
    #    "order_number": "assin3131789133860", "payment_status": "2",
    #    "test_transaction": "False", "authorization_code": "011173",
    #    "meucheckout_order_number": "a02c1169a9e34c62882c37f2e3ba8c7f",
    #    "pagador_recurrent_payment_id": "ec6f46ce-5425-4df3-a9d3-60ac9d0e2c02"}
    # Formato antigo (até out/2026): a chave vinha em
    # "checkout_cielo_order_number" (ex.: "41f28923bd3040fe9cb1"); desde
    # nov/2026 vem em "meucheckout_order_number". Os dois são aceitos.
    # Recusa: mesmo formato com "payment_status": "3" e sem authorization_code;
    # o motivo NÃO vem no aviso (só na consulta GET /v2/orders/{chave}).
    # created_date é DD/MM/AAAA; start_date/end_date são MM/DD (não usados).
    #
    # A chave identifica a cobrança: sem ela o aviso não é processado, e um
    # aviso repetido com a mesma chave não lança nem conta recusa de novo.
    #
    # NOTA DE SEGURANÇA: confiamos no payload (sem autenticação). A
    # reconciliação horária (reconciliar_cielo) consulta a Cielo e lança
    # cobranças pagas cujo aviso não chegou.
    if request.is_json:
        dados = request.get_json(silent=True)
    else:
        dados = request.form.to_dict()
    erro = _payload_invalido(dados, 'Cielo')
    if erro:
        _arq('cielo', f'aviso com conteúdo inválido ({erro}); respondendo 400', 'error')
        return jsonify({'erro': 'payload inválido'}), 400

    order_number = dados.get('order_number')
    chave = _chave_pedido_cielo(dados)
    payment_status = dados.get('payment_status')

    _arq('cielo', f'aviso recebido: order_number={order_number} chave={chave} '
                  f'payment_status={_status_cielo_txt(payment_status)} '
                  f'recurrent_status={dados.get("recurrent_status") or "-"}')
    try:
        resumo, nivel = _webhook_cielo_processar(dados)
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        _arq('cielo', f'resultado: erro ({type(e).__name__}) order_number={order_number} chave={chave}; '
                      f'respondendo 500 para a Cielo reenviar', 'error')
        raise
    _arq('cielo', f'resultado: {resumo} (order_number={order_number} chave={chave})', nivel)
    return jsonify({'status': 'recebido'}), 200


def _payload_invalido(dados, gateway):
    """v1.7.2: aviso que não é um objeto JSON (lista, texto, vazio, JSON
    inválido) não é processado. Grava ERROR na tela e devolve o motivo curto
    (o chamador responde 400); None se o payload é válido."""
    if isinstance(dados, dict) and dados:
        return None
    if dados is None or (isinstance(dados, dict) and not dados):
        motivo = 'vazio ou JSON inválido'
    else:
        motivo = {list: 'lista', str: 'texto'}.get(type(dados), type(dados).__name__)
    _log('erro_parsing', nivel='ERROR',
         payload={'corpo_recebido': request.get_data(as_text=True)[:2000]},
         resultado=f'Aviso da {gateway} com conteúdo inválido (não é um objeto JSON: {motivo}) — '
                   f'não processado (resposta 400)')
    return motivo


def _webhook_cielo_processar(dados):
    """Processa o aviso da Cielo. Retorna (resumo sem dados pessoais, nível do servico.log)."""
    order_number = dados.get('order_number')
    pagador_recurrent_payment_id = dados.get('pagador_recurrent_payment_id')
    payment_status = dados.get('payment_status')
    chave = _chave_pedido_cielo(dados)
    status_txt = _status_cielo_txt(payment_status)

    _log('webhook_recebido', payload=dados,
         recurrent_payment_id=pagador_recurrent_payment_id, payment_id_gateway=chave,
         resultado=f'Aviso recebido da Cielo — status {status_txt}, pedido {order_number or "—"}, '
                   f'chave {chave or "—"}')

    if not order_number and not pagador_recurrent_payment_id:
        _log('erro_parsing', nivel='ERROR', payload=dados,
             resultado='Aviso da Cielo sem order_number nem pagador_recurrent_payment_id — não processado')
        return 'ignorado (sem order_number nem pagador_recurrent_payment_id)', 'warning'

    if payment_status in STATUS_CIELO_CONFIRMADO + STATUS_CIELO_NEGADO and not chave:
        if _chave_cielo_fora_do_padrao(dados):
            falta = ('chave do pedido fora do padrão da Cielo (20 ou 32 hexadecimais) em '
                     'checkout_cielo_order_number/meucheckout_order_number')
        else:
            falta = 'faltam checkout_cielo_order_number e meucheckout_order_number'
        _log('erro_parsing', nivel='ERROR', payload=dados,
             recurrent_payment_id=pagador_recurrent_payment_id,
             resultado=f'Aviso sem chave do pedido — não processado (status {status_txt}, pedido '
                       f'{order_number or "—"}; {falta})')
        return 'ignorado (aviso sem chave do pedido)', 'error'

    # Prioridade de busca: 1) recurrent_payment_id já vinculado (cobranças
    # subsequentes), 2) order_number (1ª confirmação, vincula pra sempre)
    assinatura = None
    if pagador_recurrent_payment_id:
        assinatura = AssinaturaRecorrente.query.filter_by(
            recurrent_payment_id=pagador_recurrent_payment_id, gateway='cielo'
        ).first()
    if not assinatura and order_number:
        assinatura = AssinaturaRecorrente.query.filter_by(
            order_number=order_number, gateway='cielo'
        ).first()

    if not assinatura:
        _log('erro_parsing', nivel='WARNING', payload=dados,
             recurrent_payment_id=pagador_recurrent_payment_id, payment_id_gateway=chave,
             resultado=f'Assinatura não encontrada para order_number {order_number}')
        return 'ignorado (assinatura não encontrada)', 'warning'

    # Trava ANTES da checagem de idempotência. Nada pendente na sessão aqui
    # (o vínculo do recurrent_payment_id é feito depois), então o autoflush
    # não grava nada antes da trava.
    assinatura = _travar_assinatura(assinatura.id)
    if pagador_recurrent_payment_id and not assinatura.recurrent_payment_id:
        assinatura.recurrent_payment_id = pagador_recurrent_payment_id

    data_cobranca, obs_data = _data_cobranca_cielo(dados)
    ctx = {
        'gateway': 'Cielo',
        'order_number': order_number,
        'chave': chave,
        'valor': _centavos(dados.get('amount')),
        'data_cobranca': data_cobranca,
        'obs_data': obs_data,
        'cartao': _cartao_cielo(dados),
        'motivo': _motivo_no_aviso(dados),
        'rotulo_negado': 'Cartão recusado',
        'criado_por': 'cielo_webhook',
    }

    if payment_status in STATUS_CIELO_CONFIRMADO:
        codigo, resumo, nivel = _processar_pagamento_confirmado(assinatura, ctx)
    elif payment_status in STATUS_CIELO_NEGADO:
        codigo, resumo, nivel = _processar_pagamento_negado(assinatura, ctx)
        if codigo == 'negado' and payment_status == '3':
            _completar_motivo_recusa(ctx)
    else:
        aluno = assinatura.aluno
        _log('webhook_recebido', nivel='WARNING', assinatura_recorrente_id=assinatura.id,
             recurrent_payment_id=pagador_recurrent_payment_id, payment_id_gateway=chave, payload=dados,
             resultado=f'Aviso com status {status_txt} não tratado automaticamente — '
                       f'{_aluno_txt(aluno)} — {_ref(ctx)}. Nada foi alterado.')
        resumo, nivel = f'ignorado (payment_status não mapeado: {payment_status})', 'warning'

    # Recorrência desativada/cancelada pela Cielo: só registra (decisão manual)
    recurrent_status = dados.get('recurrent_status')
    if recurrent_status and str(recurrent_status).strip().lower() != 'ativa':
        assinatura_id = assinatura.id
        aluno = assinatura.aluno
        _log('recorrencia_cielo', nivel='WARNING', assinatura_recorrente_id=assinatura_id,
             recurrent_payment_id=pagador_recurrent_payment_id, payment_id_gateway=chave,
             resultado=f'Recorrência {recurrent_status} na Cielo — {_aluno_txt(aluno)}, '
                       f'pedido {order_number or "—"}. O status da assinatura no GymFlow NÃO foi '
                       f'alterado (decisão manual).')
        _arq('cielo', f'recorrência {recurrent_status} na Cielo: assinatura_id={assinatura_id} '
                      f'aluno_id={aluno.id if aluno else "-"} order_number={order_number}', 'warning')

    return resumo, nivel


def _processar_pagamento_confirmado(assinatura, ctx):
    """Lança o pagamento e ajusta o vencimento. Chamado com a assinatura
    TRAVADA (webhook Cielo/Asaas e reconciliação).
    Retorna (codigo, resumo sem dados pessoais, nível do servico.log);
    codigo: 'lancado' | 'lancado_cancelada' | 'duplicado'."""
    payment_id = ctx.get('chave')
    assinatura_id = assinatura.id

    # Idempotência: essa cobrança já foi lançada (sob a trava, então o
    # outro aviso da mesma cobrança já terminou)
    if payment_id and Pagamento.query.filter_by(payment_id_gateway=payment_id).first():
        _log('pagamento_lancado', nivel='INFO', assinatura_recorrente_id=assinatura_id,
             payment_id_gateway=payment_id,
             resultado=f'Aviso repetido ignorado (já processado) — {_ref(ctx)}')
        return 'duplicado', f'duplicado (já processado) assinatura_id={assinatura_id}', 'info'

    aluno = assinatura.aluno
    aluno_id, aluno_txt = aluno.id, _aluno_txt(aluno)
    hoje = date.today()
    valor_txt = _reais(ctx.get('valor') if ctx.get('valor') is not None else assinatura.valor_mensal)
    data_cobranca = ctx['data_cobranca']

    # Proteção crítica: a Cielo confirma que desativar uma recorrência NÃO
    # cancela uma cobrança que já estava em trânsito naquele momento — ela
    # pode confirmar alguns instantes/dias depois, mesmo já cancelada.
    # Sem essa checagem, esse pagamento "atrasado" reativava a assinatura
    # e avançava o vencimento de novo, mesmo já tendo sido cancelada.
    ja_cancelada = assinatura.status in STATUS_ENCERRADA

    pagamento = Pagamento(
        aluno_id=aluno_id,
        valor=assinatura.valor_mensal,
        forma_pagamento='cartao_recorrente',
        status='pago',
        data_vencimento=aluno.vencimento or hoje,
        # v1.7.2: data da cobrança (a mesma da regra do vencimento); sem ela, hoje
        data_pagamento=data_cobranca or hoje,
        referencia=assinatura.recurrent_payment_id,
        criado_por=ctx.get('criado_por', 'cielo_webhook'),
        origem='recorrente_gateway',
        assinatura_recorrente_id=assinatura_id,
        payment_id_gateway=payment_id,
        observacao='Cobrança já autorizada antes do cancelamento — processada tardiamente.' if ja_cancelada else None,
    )

    venc_anterior = aluno.vencimento
    reativou = False
    if not ja_cancelada:
        # v1.7.2 — regra do vencimento isolada em _novo_vencimento (nunca regride).
        aluno.vencimento = _novo_vencimento(venc_anterior, data_cobranca)
        # Reativa (ativo, status 'ativa', zera recusas) só se a data desta
        # cobrança for posterior à última recusa. Com a regra do vencimento
        # da 1.7.2 o vencimento sempre avança, então "avançou o vencimento"
        # não serve de critério: o que decide é data da cobrança x última
        # recusa. Um pagamento antigo (ex.: recuperado pela reconciliação)
        # é lançado, mas não desbloqueia o aluno.
        # Webhook Cielo, webhook Asaas e reconciliação passam todos por aqui.
        ultima_negativa = assinatura.data_ultima_negativa
        posterior_a_recusa = (ultima_negativa is None
                              or data_cobranca is None
                              or data_cobranca > ultima_negativa.date())
        if posterior_a_recusa:
            aluno.ativo = True
            assinatura.tentativas_negadas = 0
            assinatura.status = 'ativa'
            assinatura.proxima_cobranca = aluno.vencimento
            reativou = True
    venc_novo = aluno.vencimento
    # Estado antes do commit (para a frase do log, sem nova consulta depois)
    st_assinatura, tent_assinatura, aluno_ativo = assinatura.status, assinatura.tentativas_negadas, aluno.ativo
    ultima_negativa_txt = _dt(assinatura.data_ultima_negativa)

    db.session.add(pagamento)
    try:
        db.session.commit()
    except IntegrityError as e:
        # Índice único uq_pagamentos_payment_id_gateway: outro processo
        # lançou a mesma cobrança (ex.: mesma chave em outra assinatura)
        if 'uq_pagamentos_payment_id_gateway' not in str(e):
            raise
        db.session.rollback()
        _log('pagamento_lancado', nivel='INFO', assinatura_recorrente_id=assinatura_id,
             payment_id_gateway=payment_id,
             resultado=f'Aviso repetido ignorado (já processado) — {_ref(ctx)}')
        return 'duplicado', f'duplicado (já processado, índice único) assinatura_id={assinatura_id}', 'info'

    prefixo = ctx.get('prefixo') or 'Pagamento confirmado'
    obs = f'; data da cobrança: {ctx["obs_data"]}' if ctx.get('obs_data') else ''

    if ja_cancelada:
        # Dinheiro é real e precisa aparecer no Financeiro, mas NÃO
        # reativamos a assinatura nem mexemos no vencimento automaticamente
        # — quem decide o que fazer com esse pagamento tardio é a recepção.
        _log('pagamento_lancado', nivel='WARNING', assinatura_recorrente_id=assinatura_id,
             payment_id_gateway=payment_id,
             resultado=f'{prefixo} numa assinatura JÁ CANCELADA — {aluno_txt}, {valor_txt}, cobrado em '
                       f'{_dt(data_cobranca)}. Lançado no Financeiro, mas status/vencimento NÃO foram '
                       f'alterados automaticamente. Revisar manualmente — {_ref(ctx)}{obs}')
        return ('lancado_cancelada',
                f'lançado em assinatura cancelada (vencimento não alterado) assinatura_id={assinatura_id} '
                f'aluno_id={aluno_id}', 'warning')

    if venc_novo != venc_anterior:
        venc_txt = f'Novo vencimento: {_dt(venc_novo)} (antes {_dt(venc_anterior)})'
    else:
        venc_txt = f'vencimento mantido em {_dt(venc_anterior)} (já cobria esta cobrança)'

    pendente = st_assinatura != 'ativa' or not aluno_ativo or tent_assinatura > 0
    if not reativou and pendente:
        if st_assinatura == 'bloqueada_inadimplencia' or not aluno_ativo:
            situacao = 'aluno continua bloqueado'
        else:
            situacao = f'assinatura continua "{st_assinatura}" com {tent_assinatura} recusa(s)'
        _log('pagamento_lancado', nivel='WARNING', assinatura_recorrente_id=assinatura_id,
             payment_id_gateway=payment_id,
             resultado=f'Pagamento antigo lançado; {situacao} — conferir. {prefixo} — {aluno_txt}, '
                       f'{valor_txt}, cobrado em {_dt(data_cobranca)} (antes da última recusa, '
                       f'{ultima_negativa_txt}); {venc_txt}; status, recusas e acesso NÃO foram '
                       f'alterados — {_ref(ctx)}{obs}')
        return ('lancado',
                f'lançado sem reativar (cobrança anterior à última recusa) assinatura_id={assinatura_id} '
                f'aluno_id={aluno_id} vencimento {venc_anterior} -> {venc_novo}', 'warning')

    _log('pagamento_lancado', assinatura_recorrente_id=assinatura_id, payment_id_gateway=payment_id,
         resultado=f'{prefixo} — {aluno_txt}, {valor_txt}, cobrado em {_dt(data_cobranca)}; '
                   f'{venc_txt} — {_ref(ctx)}{obs}')
    return ('lancado',
            f'lançado assinatura_id={assinatura_id} aluno_id={aluno_id} vencimento {venc_anterior} -> {venc_novo}',
            'info')


def _processar_pagamento_negado(assinatura, ctx):
    """Conta a tentativa negada UMA vez por chave do pedido. Chamado com a
    assinatura TRAVADA. A recusa contada fica registrada no log com
    tipo_evento 'tentativa_negada' e payment_id_gateway = chave; um aviso
    repetido da mesma cobrança encontra esse registro e não conta de novo.
    Retorna (codigo, resumo, nível); codigo: 'negado' | 'duplicado' | 'ignorado'."""
    payment_id = ctx.get('chave')
    assinatura_id = assinatura.id

    # v1.7.2: assinatura já encerrada — a recusa não conta tentativa nem bloqueia
    if assinatura.status in STATUS_ENCERRADA:
        status = assinatura.status
        status_txt = NOMES_STATUS_ENCERRADA.get(status, status)
        aluno_txt = _aluno_txt(assinatura.aluno)
        valor_txt = _reais(ctx.get('valor') if ctx.get('valor') is not None else assinatura.valor_mensal)
        _log('webhook_recebido', nivel='INFO', assinatura_recorrente_id=assinatura_id,
             payment_id_gateway=payment_id,
             resultado=f'Recusa ignorada: assinatura {status_txt} — {aluno_txt}, {valor_txt}, cobrança de '
                       f'{_dt(ctx.get("data_cobranca"))} ({ctx.get("rotulo_negado", "Cartão recusado")}); '
                       f'tentativa não contada e aluno não bloqueado — {_ref(ctx)}')
        return 'ignorado', f'ignorado (recusa em assinatura {status}) assinatura_id={assinatura_id}', 'info'

    if not payment_id:
        _log('erro_parsing', nivel='ERROR', assinatura_recorrente_id=assinatura_id,
             resultado=f'Aviso sem chave do pedido — não processado (recusa não contada) — {_ref(ctx)}')
        return 'ignorado', 'ignorado (recusa sem chave do pedido)', 'error'

    if LogIntegracaoPagamento.query.filter_by(tipo_evento='tentativa_negada',
                                              payment_id_gateway=payment_id).first():
        _log('pagamento_lancado', nivel='INFO', assinatura_recorrente_id=assinatura_id,
             payment_id_gateway=payment_id,
             resultado=f'Aviso repetido ignorado (recusa já contada) — {_ref(ctx)}')
        return 'duplicado', f'duplicado (recusa já contada) assinatura_id={assinatura_id}', 'info'

    aluno = assinatura.aluno
    aluno_txt = _aluno_txt(aluno)
    valor_txt = _reais(ctx.get('valor') if ctx.get('valor') is not None else assinatura.valor_mensal)

    assinatura.tentativas_negadas += 1
    assinatura.data_ultima_negativa = db.func.now()
    tentativas = assinatura.tentativas_negadas

    max_tentativas = current_app.config.get('RECORRENCIA_MAX_TENTATIVAS_NEGADAS', 4)

    motivo = ctx.get('motivo') or TEXTO_SEM_MOTIVO.format(gateway=ctx.get('gateway', 'Cielo'))
    frase = (f'{ctx.get("rotulo_negado", "Cartão recusado")} — {aluno_txt}, {valor_txt}, cobrança de '
             f'{_dt(ctx.get("data_cobranca"))}; tentativa {tentativas}/{max_tentativas}; {motivo} — {_ref(ctx)}')

    bloqueado = tentativas >= max_tentativas
    if bloqueado:
        assinatura.status = 'bloqueada_inadimplencia'
        aluno.ativo = False
        frase = f'Aluno bloqueado após {max_tentativas}ª recusa — {aluno_txt}. {frase}'

    marcador = LogIntegracaoPagamento(
        tipo_evento='tentativa_negada', nivel='WARNING', resultado=frase,
        assinatura_recorrente_id=assinatura_id,
        recurrent_payment_id=assinatura.recurrent_payment_id,
        payment_id_gateway=payment_id,
        ip_origem=request.remote_addr if has_request_context() else None,
    )
    db.session.add(marcador)
    db.session.commit()   # contagem + registro da recusa juntos

    ctx['log_recusa_id'] = marcador.id
    ctx['frase_recusa'] = frase
    resumo = (f'tentativa negada {tentativas}/{max_tentativas}' + (' — aluno bloqueado' if bloqueado else '')
              + f' assinatura_id={assinatura_id} aluno_id={aluno.id}')
    return 'negado', resumo, 'warning'


def _completar_motivo_recusa(ctx):
    """Depois do commit da recusa: consulta GET /v2/orders/{chave} (timeout
    5 s) e troca 'motivo não informado' pelo motivo do errorCode. Qualquer
    falha mantém o texto original. Nada aqui altera regra de negócio."""
    log_id, chave = ctx.get('log_recusa_id'), ctx.get('chave')
    if not log_id or not chave or ctx.get('motivo'):
        return
    if not (current_app.config.get('CIELO_CLIENT_ID') and current_app.config.get('CIELO_CLIENT_SECRET')):
        return
    try:
        pedido = cielo_client.consultar_pedido(chave, timeout=5)
        codigo = str(((pedido or {}).get('payment') or {}).get('errorCode') or '').strip()
        if not codigo:
            return
        descricao = MOTIVOS_RECUSA.get(codigo.upper())
        motivo = f'motivo: {descricao} (código {codigo})' if descricao else f'motivo: código {codigo}'
        log = db.session.get(LogIntegracaoPagamento, log_id)
        if log and log.resultado:
            log.resultado = log.resultado.replace(TEXTO_SEM_MOTIVO.format(gateway='Cielo'), motivo)
            db.session.commit()
        _arq('cielo', f'motivo da recusa (chave={chave}): código {codigo}')
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        _arq('cielo', f'motivo da recusa indisponível (chave={chave}): {type(e).__name__}', 'warning')


# ─────────────────────────────────────────────────────────────────────────
# Reconciliação com a Cielo (job horário em backup_scheduler.py)
# Lança cobranças PAGAS na Cielo cujo aviso não chegou (ex.: falha na
# madrugada), pelo mesmo caminho do webhook (trava + índice único + regra
# de vencimento).
# ─────────────────────────────────────────────────────────────────────────

def reconciliar_cielo():
    """Uma rodada. Precisa de app context (o job do APScheduler abre um).
    Retorna o dict de contagens."""
    cont = {'consultadas': 0, 'lancadas': 0, 'avisos': 0, 'erros': 0}
    if not (current_app.config.get('CIELO_CLIENT_ID') and current_app.config.get('CIELO_CLIENT_SECRET')):
        return cont

    current_app.logger.info('[pagamentos] reconciliação Cielo: início da rodada')
    limite = _hoje_local() - timedelta(days=RECONCILIACAO_DIAS)
    ids = [a.id for a in AssinaturaRecorrente.query.filter(
        AssinaturaRecorrente.gateway == 'cielo',
        AssinaturaRecorrente.status.in_(STATUS_RECONCILIACAO),
        AssinaturaRecorrente.link_pagamento_id.isnot(None),
    ).order_by(AssinaturaRecorrente.id).all()]
    db.session.rollback()   # não segura transação durante as chamadas HTTP

    for assinatura_id in ids:
        try:
            assinatura = db.session.get(AssinaturaRecorrente, assinatura_id)
            link_id = assinatura.link_pagamento_id
            db.session.rollback()
            try:
                resposta = cielo_client.listar_pagamentos_link(link_id, timeout=15)
            except (CieloAuthError, CieloComunicacaoError) as e:
                # v1.7.2: credencial recusada ou rede fora valem para todas as
                # assinaturas: interrompe a rodada com uma linha só (sem um POST
                # de token e dois ERROR por assinatura a cada hora)
                cont['erros'] += 1
                motivo = getattr(e, 'motivo', None) or type(e).__name__
                _log('erro_autenticacao' if isinstance(e, CieloAuthError) else 'erro_comunicacao',
                     nivel='ERROR',
                     resultado=f'Reconciliação interrompida: falha de autenticação/comunicação com a Cielo '
                               f'— {motivo}')
                current_app.logger.error(f'[pagamentos] reconciliação Cielo: interrompida — falha de '
                                         f'autenticação/comunicação com a Cielo ({type(e).__name__}: {motivo})')
                break
            except Exception as e:
                cont['erros'] += 1
                _log('erro_comunicacao', nivel='ERROR', assinatura_recorrente_id=assinatura_id,
                     resultado=f'Reconciliação: falha ao consultar as cobranças do link na Cielo '
                               f'({type(e).__name__}: {str(e)[:200]})')
                current_app.logger.error(f'[pagamentos] reconciliação Cielo: erro ao consultar '
                                         f'assinatura_id={assinatura_id}: {type(e).__name__}')
                continue
            cont['consultadas'] += 1

            for order in (resposta or {}).get('orders') or []:
                # v1.7.2: try por cobrança — um item malformado não pula as demais
                chave = None
                try:
                    pagamento_cielo = order.get('payment') or {}
                    chave = order.get('orderNumber')
                    criado = _data_iso(pagamento_cielo.get('createdDate') or order.get('createdDate'))
                    if not chave or not criado or criado < limite:
                        continue
                    if pagamento_cielo.get('status') != 'Paid':
                        continue   # Denied, Pending, Expired, Voided...: não lança
                    codigo = _reconciliar_cobranca_paga(assinatura_id, str(chave), pagamento_cielo, criado)
                    if codigo == 'lancado' or codigo == 'lancado_cancelada':
                        cont['lancadas'] += 1
                    elif codigo == 'aviso':
                        cont['avisos'] += 1
                except Exception as e:
                    cont['erros'] += 1
                    _erro_reconciliacao(assinatura_id, e, chave, na_cobranca=True)
        except Exception as e:
            cont['erros'] += 1
            _erro_reconciliacao(assinatura_id, e)

    current_app.logger.info(
        f'[pagamentos] reconciliação Cielo: fim da rodada — consultadas={cont["consultadas"]} '
        f'lançadas={cont["lancadas"]} avisos={cont["avisos"]} erros={cont["erros"]}')
    return cont


def _erro_reconciliacao(assinatura_id, e, chave=None, na_cobranca=False):
    """Erro inesperado numa assinatura ou cobrança da reconciliação: ERROR na
    tela de logs e no servico.log (só o tipo do erro), e a rodada segue."""
    try:
        db.session.rollback()
    except Exception:
        pass
    chave_txt = str(chave)[:50] if isinstance(chave, (str, int)) else None
    if chave_txt:
        onde = f'a cobrança {chave_txt}'
    else:
        onde = 'uma cobrança sem identificação' if na_cobranca else 'as cobranças'
    _log('erro_parsing', nivel='ERROR', assinatura_recorrente_id=assinatura_id,
         payment_id_gateway=chave_txt,
         resultado=f'Reconciliação: erro inesperado ao processar {onde} da assinatura #{assinatura_id} '
                   f'({type(e).__name__}). Nada foi lançado para esse item; a rodada seguiu.')
    current_app.logger.error(f'[pagamentos] reconciliação Cielo: erro na assinatura_id={assinatura_id}'
                             + (f' chave={chave_txt}' if chave_txt else '') + f': {type(e).__name__}')


def _reconciliar_cobranca_paga(assinatura_id, chave, pagamento_cielo, data_cobranca):
    """Retorna 'existe' | 'aviso' | 'aviso_repetido' | código de _processar_pagamento_confirmado."""
    if Pagamento.query.filter_by(payment_id_gateway=chave).first():
        db.session.rollback()
        return 'existe'

    assinatura = _travar_assinatura(assinatura_id)
    # De novo, sob a trava: o webhook pode ter lançado enquanto esperávamos
    if Pagamento.query.filter_by(payment_id_gateway=chave).first():
        db.session.rollback()
        return 'existe'

    # Proteção: pagamento já lançado SEM chave (versões antigas) ou lançamento
    # manual pago do mesmo aluno perto da data da cobrança → não lança
    janela = timedelta(days=RECONCILIACAO_JANELA_SEM_CHAVE)
    inicio, fim = data_cobranca - janela, data_cobranca + janela
    suspeito = Pagamento.query.filter(
        db.or_(
            db.and_(Pagamento.assinatura_recorrente_id == assinatura.id,
                    Pagamento.origem == 'recorrente_gateway',
                    Pagamento.payment_id_gateway.is_(None)),
            db.and_(Pagamento.aluno_id == assinatura.aluno_id,
                    Pagamento.origem != 'recorrente_gateway',
                    Pagamento.status == 'pago'),
        ),
        db.or_(Pagamento.data_pagamento.between(inicio, fim),
               db.func.date(Pagamento.criado_em).between(inicio, fim)),
    ).order_by(Pagamento.id).first()

    ctx = {
        'gateway': 'Cielo',
        'order_number': assinatura.order_number,
        'chave': chave,
        'valor': _centavos(pagamento_cielo.get('price')),
        'data_cobranca': data_cobranca,
        'obs_data': None,
        'cartao': None,
        'motivo': None,
        'criado_por': 'cielo_reconciliacao',
        'prefixo': 'Pagamento recuperado pela consulta à Cielo (o aviso não chegou)',
    }

    if suspeito:
        aluno = assinatura.aluno
        aluno_txt = _aluno_txt(aluno)
        existente = (f'lançamento #{suspeito.id} de {_dt(suspeito.data_pagamento or suspeito.criado_em)}, '
                     f'{_reais(suspeito.valor)}, origem {suspeito.origem}')
        db.session.rollback()   # libera a trava
        if LogIntegracaoPagamento.query.filter_by(tipo_evento='reconciliacao_conferir',
                                                  payment_id_gateway=chave).first():
            db.session.rollback()
            return 'aviso_repetido'
        _log('reconciliacao_conferir', nivel='WARNING', assinatura_recorrente_id=assinatura_id,
             payment_id_gateway=chave,
             resultado=f'Cobrança paga na Cielo sem chave correspondente no GymFlow — possível lançamento '
                       f'já existente sem chave; conferir (pedido {chave}, aluno {aluno_txt}, '
                       f'{_reais(ctx["valor"])}, cobrado em {_dt(data_cobranca)}; {existente}). '
                       f'Nada foi lançado.')
        current_app.logger.warning(f'[pagamentos] reconciliação Cielo: conferir chave={chave} '
                                   f'assinatura_id={assinatura_id} (possível lançamento sem chave)')
        return 'aviso'

    codigo, resumo, nivel = _processar_pagamento_confirmado(assinatura, ctx)
    _arq('cielo', f'reconciliação: {resumo} chave={chave}', nivel)
    return codigo


# ─────────────────────────────────────────────────────────────────────────
# Webhook Asaas — autenticação nativa via header asaas-access-token
# ─────────────────────────────────────────────────────────────────────────

@bp.route('/webhooks/asaas', methods=['POST'])
@asaas_webhook_auth_required
def webhook_asaas():
    dados = request.get_json(silent=True)
    erro = _payload_invalido(dados, 'Asaas')
    if erro:
        _arq('asaas', f'aviso com conteúdo inválido ({erro}); respondendo 400', 'error')
        return jsonify({'erro': 'payload inválido'}), 400
    evento = dados.get('event')
    payment_id = (dados.get('payment') or {}).get('id')

    _arq('asaas', f'aviso recebido: evento={evento} payment_id={payment_id} '
                  f'subscription={(dados.get("payment") or {}).get("subscription")}')
    try:
        resumo, nivel = _webhook_asaas_processar(dados)
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        _arq('asaas', f'resultado: erro ({type(e).__name__}) payment_id={payment_id}; respondendo 500', 'error')
        raise
    _arq('asaas', f'resultado: {resumo} (payment_id={payment_id})', nivel)
    return jsonify({'status': 'recebido'}), 200


def _webhook_asaas_processar(dados):
    evento = dados.get('event')
    pagamento = dados.get('payment') or {}
    payment_id = pagamento.get('id')
    subscription_id = pagamento.get('subscription')   # ID da assinatura (só existe após 1ª cobrança confirmada)
    payment_link_id = pagamento.get('paymentLink')     # ID do link original — é o que salvamos em link_pagamento_id

    _log('webhook_recebido', payload=dados,
         recurrent_payment_id=subscription_id, payment_id_gateway=payment_id,
         resultado=f'Aviso recebido da Asaas — evento {evento}, pagamento {payment_id or "—"}')

    if not payment_id:
        _log('erro_parsing', nivel='ERROR', payload=dados,
             resultado='Aviso da Asaas sem payment.id identificável — não processado')
        return 'ignorado (sem payment.id)', 'error'

    # Diferente da Cielo, a Asaas já manda o payload completo — não é
    # necessário consultar ativamente antes de processar. Localizamos a
    # assinatura em ordem de prioridade:
    #   1. Por subscription_id, se já tivermos essa referência salva
    #      (confirmações subsequentes, depois da primeira)
    #   2. Por payment_link_id (primeira confirmação — é isso que
    #      salvamos como link_pagamento_id na criação)
    #   3. Por payment_id já registrado em Pagamento (fallback)
    assinatura = None
    vincular_subscription = False
    if subscription_id:
        assinatura = AssinaturaRecorrente.query.filter_by(
            recurrent_payment_id=subscription_id, gateway='asaas'
        ).first()
    if not assinatura and payment_link_id:
        assinatura = AssinaturaRecorrente.query.filter_by(
            link_pagamento_id=payment_link_id, gateway='asaas'
        ).first()
        vincular_subscription = True
    if not assinatura:
        pagamento_existente = Pagamento.query.filter_by(payment_id_gateway=payment_id).first()
        if pagamento_existente:
            assinatura = pagamento_existente.assinatura_recorrente

    if not assinatura:
        _log('erro_parsing', nivel='WARNING', payment_id_gateway=payment_id, payload=dados,
             resultado=f'Assinatura não encontrada para o pagamento Asaas {payment_id} (evento {evento})')
        return 'ignorado (assinatura não encontrada)', 'warning'

    # Trava antes da idempotência (ver webhook Cielo)
    assinatura = _travar_assinatura(assinatura.id)
    # Primeira confirmação dessa assinatura — grava o subscription_id
    # de verdade, pra buscas futuras (e pro cancelamento saber que já
    # existe assinatura real, não só um link pendente).
    if vincular_subscription and subscription_id and not assinatura.recurrent_payment_id:
        assinatura.recurrent_payment_id = subscription_id

    data_cobranca, obs_data = _data_cobranca_asaas(pagamento)
    valor = pagamento.get('value')
    try:
        valor = Decimal(str(valor)) if valor is not None else None
    except (InvalidOperation, ValueError):
        valor = None
    ctx = {
        'gateway': 'Asaas',
        'order_number': None,
        'chave': payment_id,
        'valor': valor,
        'data_cobranca': data_cobranca,
        'obs_data': obs_data,
        'cartao': _cartao_asaas(pagamento),
        'motivo': _motivo_no_aviso(pagamento),
        'rotulo_negado': f'Cobrança não paga ({evento})',
        'criado_por': 'cielo_webhook',   # valor histórico mantido (era o mesmo para a Asaas)
    }

    eventos_confirmados = ('PAYMENT_CONFIRMED', 'PAYMENT_RECEIVED')
    eventos_negados = ('PAYMENT_OVERDUE', 'PAYMENT_DELETED', 'PAYMENT_REFUSED')

    if evento in eventos_confirmados:
        _, resumo, nivel = _processar_pagamento_confirmado(assinatura, ctx)
    elif evento in eventos_negados:
        _, resumo, nivel = _processar_pagamento_negado(assinatura, ctx)
    else:
        _log('webhook_recebido', nivel='WARNING', assinatura_recorrente_id=assinatura.id,
             payment_id_gateway=payment_id, payload=dados,
             resultado=f'Evento Asaas não mapeado: {evento} — {_aluno_txt(assinatura.aluno)}, '
                       f'pagamento {payment_id}. Nada foi alterado.')
        resumo, nivel = f'ignorado (evento não mapeado: {evento})', 'warning'
    return resumo, nivel
