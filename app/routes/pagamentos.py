"""
Módulo API para Pagamentos — Cobrança Recorrente via Cartão (Cielo).

Namespace: /api/v1/pagamentos/*

Duas famílias de rota aqui dentro:
  - Rotas operadas pela recepção (atrás de login_required, sessão normal)
  - Webhook da Cielo (Basic Auth própria, NUNCA login_required de sessão,
    porque quem chama é a Cielo, não um usuário logado)
"""

from flask import Blueprint, request, jsonify, current_app, render_template
from functools import wraps
from datetime import date, timedelta, datetime
from dateutil.relativedelta import relativedelta
import hmac

from app import db
from app.models import Aluno, Plano, AssinaturaRecorrente, Pagamento, LogIntegracaoPagamento, Configuracao
from app.routes.auth import login_required, registrar_log, get_param
from app import cielo_client
from app.cielo_client import CieloClientError
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
            ip_origem=request.remote_addr,
        )
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        current_app.logger.error(f"Falha ao gravar log_integracao_pagamento: {e}")


def _avancar_data(data_base, meses=1):
    return data_base + relativedelta(months=meses)


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
                    cliente.desativar_recorrencia(assinatura.recurrent_payment_id)
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
        query = query.filter(
            db.or_(
                LogIntegracaoPagamento.recurrent_payment_id.ilike(f'%{busca}%'),
                LogIntegracaoPagamento.payment_id_gateway.ilike(f'%{busca}%'),
            )
        )

    query = query.order_by(LogIntegracaoPagamento.criado_em.desc())
    pag = query.paginate(page=pagina, per_page=por_pagina, error_out=False)

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
        opcoes_por_pagina=[20, 30, 50, 100],
    )


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
# Webhook Cielo — SEM login_required de sessão, com Basic Auth própria
# ─────────────────────────────────────────────────────────────────────────

@bp.route('/webhooks/cielo', methods=['GET', 'POST'])
def webhook_cielo():
    # GET existe só para o botão "Testar" do painel Cielo confirmar que
    # a URL está viva — não processa nada, só responde 200 OK.
    if request.method == 'GET':
        return jsonify({'status': 'ok'}), 200

    # SEM autenticação — a Cielo não oferece mecanismo de auth pra essa
    # notificação (confirmado na doc: "não há implementação de
    # autenticação para o envio de notificações"). A mitigação real é
    # nunca confiar no payload como fonte de verdade: sempre consultamos
    # a Cielo ativamente antes de processar qualquer atualização abaixo.
    # Aceita tanto form-urlencoded quanto JSON, conforme a doc da Cielo
    if request.is_json:
        dados = request.get_json(silent=True) or {}
    else:
        dados = request.form.to_dict()

    recurrent_payment_id = dados.get('RecurrentPaymentId') or dados.get('recurrentPaymentId')
    payment_id = dados.get('PaymentId') or dados.get('paymentId')
    change_type = dados.get('ChangeType') or dados.get('changeType')

    _log('webhook_recebido', payload=dados,
         recurrent_payment_id=recurrent_payment_id, payment_id_gateway=payment_id)

    if not recurrent_payment_id:
        # Payload não reconhecido — grava bruto (é exatamente o cenário que
        # você pediu para nunca perder) e responde 200 para a Cielo não
        # ficar reenviando indefinidamente algo que nunca vamos conseguir tratar
        _log('erro_parsing', nivel='ERROR', payload=dados,
             resultado='Webhook sem RecurrentPaymentId identificável')
        return jsonify({'status': 'recebido'}), 200

    assinatura = AssinaturaRecorrente.query.filter_by(
        recurrent_payment_id=recurrent_payment_id
    ).first()

    # Nunca confiamos no payload como fonte de verdade — sempre consultamos
    # a Cielo antes de processar qualquer atualização
    try:
        detalhe = cielo_client.consultar_recorrencia(recurrent_payment_id)
    except CieloClientError as e:
        _log('erro_parsing', nivel='ERROR', recurrent_payment_id=recurrent_payment_id,
             resultado=f'Falha ao consultar detalhe: {e}')
        return jsonify({'status': 'recebido'}), 200

    status_real = detalhe.get('status')  # TODO CONFIRMAR nome/valores exatos do campo na resposta real

    if not assinatura:
        _log('erro_parsing', nivel='WARNING', recurrent_payment_id=recurrent_payment_id,
             resultado='RecurrentPaymentId não corresponde a nenhuma assinatura conhecida', payload=detalhe)
        return jsonify({'status': 'recebido'}), 200

    if status_real in ('Paid', 'Authorized', 'Confirmed'):  # TODO CONFIRMAR valores reais
        _processar_pagamento_confirmado(assinatura, payment_id, detalhe)
    elif status_real in ('Denied', 'NotAuthorized', 'Voided'):  # TODO CONFIRMAR valores reais
        _processar_pagamento_negado(assinatura, payment_id, detalhe)
    else:
        _log('webhook_recebido', nivel='WARNING', recurrent_payment_id=recurrent_payment_id,
             resultado=f'Status não mapeado: {status_real}', payload=detalhe)

    return jsonify({'status': 'recebido'}), 200


def _processar_pagamento_confirmado(assinatura, payment_id, detalhe):
    # Deduplicação: se esse payment_id já foi lançado, não faz nada de novo
    if payment_id and Pagamento.query.filter_by(payment_id_gateway=payment_id).first():
        _log('pagamento_lancado', nivel='INFO', assinatura_recorrente_id=assinatura.id,
             payment_id_gateway=payment_id, resultado='Já processado anteriormente (idempotência)')
        return

    aluno = assinatura.aluno
    hoje = date.today()

    pagamento = Pagamento(
        aluno_id=aluno.id,
        valor=assinatura.valor_mensal,
        forma_pagamento='cartao_recorrente',
        status='pago',
        data_vencimento=aluno.vencimento or hoje,
        data_pagamento=hoje,
        referencia=assinatura.recurrent_payment_id,
        criado_por='cielo_webhook',
        origem='recorrente_gateway',
        assinatura_recorrente_id=assinatura.id,
        payment_id_gateway=payment_id,
    )
    db.session.add(pagamento)

    # Avança o vencimento do aluno automaticamente — mesma fonte de verdade
    # que o fluxo manual já usa para controle de acesso
    base = aluno.vencimento if aluno.vencimento and aluno.vencimento >= hoje else hoje
    aluno.vencimento = _avancar_data(base, meses=1)
    aluno.ativo = True

    assinatura.tentativas_negadas = 0
    assinatura.status = 'ativa'
    assinatura.proxima_cobranca = aluno.vencimento

    db.session.commit()

    _log('pagamento_lancado', assinatura_recorrente_id=assinatura.id,
         payment_id_gateway=payment_id,
         resultado=f'Pagamento confirmado. Novo vencimento: {aluno.vencimento}')


def _processar_pagamento_negado(assinatura, payment_id, detalhe):
    assinatura.tentativas_negadas += 1
    assinatura.data_ultima_negativa = db.func.now()

    max_tentativas = current_app.config.get('RECORRENCIA_MAX_TENTATIVAS_NEGADAS', 4)

    if assinatura.tentativas_negadas >= max_tentativas:
        assinatura.status = 'bloqueada_inadimplencia'
        assinatura.aluno.ativo = False
        _log('pagamento_lancado', nivel='WARNING', assinatura_recorrente_id=assinatura.id,
             payment_id_gateway=payment_id,
             resultado=f'{max_tentativas}ª tentativa negada — aluno bloqueado')
    else:
        _log('pagamento_lancado', nivel='WARNING', assinatura_recorrente_id=assinatura.id,
             payment_id_gateway=payment_id,
             resultado=f'Tentativa {assinatura.tentativas_negadas}/{max_tentativas} negada')

    db.session.commit()


# ─────────────────────────────────────────────────────────────────────────
# Webhook Asaas — autenticação nativa via header asaas-access-token
# ─────────────────────────────────────────────────────────────────────────

@bp.route('/webhooks/asaas', methods=['POST'])
@asaas_webhook_auth_required
def webhook_asaas():
    dados = request.get_json(silent=True) or {}

    evento = dados.get('event')
    pagamento = dados.get('payment', {})
    payment_id = pagamento.get('id')
    subscription_id = pagamento.get('subscription')   # ID da assinatura (só existe após 1ª cobrança confirmada)
    payment_link_id = pagamento.get('paymentLink')     # ID do link original — é o que salvamos em link_pagamento_id

    _log('webhook_recebido', payload=dados,
         recurrent_payment_id=subscription_id, payment_id_gateway=payment_id)

    if not payment_id:
        _log('erro_parsing', nivel='ERROR', payload=dados,
             resultado='Webhook Asaas sem payment.id identificável')
        return jsonify({'status': 'recebido'}), 200

    # Diferente da Cielo, a Asaas já manda o payload completo — não é
    # necessário consultar ativamente antes de processar. Localizamos a
    # assinatura em ordem de prioridade:
    #   1. Por subscription_id, se já tivermos essa referência salva
    #      (confirmações subsequentes, depois da primeira)
    #   2. Por payment_link_id (primeira confirmação — é isso que
    #      salvamos como link_pagamento_id na criação)
    #   3. Por payment_id já registrado em Pagamento (fallback)
    assinatura = None
    if subscription_id:
        assinatura = AssinaturaRecorrente.query.filter_by(
            recurrent_payment_id=subscription_id, gateway='asaas'
        ).first()
    if not assinatura and payment_link_id:
        assinatura = AssinaturaRecorrente.query.filter_by(
            link_pagamento_id=payment_link_id, gateway='asaas'
        ).first()
        # Primeira confirmação dessa assinatura — grava o subscription_id
        # de verdade, pra buscas futuras (e pro cancelamento saber que já
        # existe assinatura real, não só um link pendente).
        if assinatura and subscription_id and not assinatura.recurrent_payment_id:
            assinatura.recurrent_payment_id = subscription_id
    if not assinatura:
        pagamento_existente = Pagamento.query.filter_by(payment_id_gateway=payment_id).first()
        if pagamento_existente:
            assinatura = pagamento_existente.assinatura_recorrente

    if not assinatura:
        _log('erro_parsing', nivel='WARNING', payment_id_gateway=payment_id,
             resultado='Evento Asaas não corresponde a nenhuma assinatura conhecida', payload=dados)
        return jsonify({'status': 'recebido'}), 200

    eventos_confirmados = ('PAYMENT_CONFIRMED', 'PAYMENT_RECEIVED')
    eventos_negados = ('PAYMENT_OVERDUE', 'PAYMENT_DELETED', 'PAYMENT_REFUSED')

    if evento in eventos_confirmados:
        _processar_pagamento_confirmado(assinatura, payment_id, pagamento)
    elif evento in eventos_negados:
        _processar_pagamento_negado(assinatura, payment_id, pagamento)
    else:
        _log('webhook_recebido', nivel='WARNING', payment_id_gateway=payment_id,
             resultado=f'Evento Asaas não mapeado: {evento}', payload=dados)

    return jsonify({'status': 'recebido'}), 200