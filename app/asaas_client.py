"""
Wrapper isolado para toda a comunicação com a API Asaas (Link de Pagamento
com recorrência).

Diferente da Cielo, a Asaas usa autenticação simples por API Key fixa no
header (sem OAuth/token com expiração), e o webhook já manda o payload
completo do evento — não exige consulta ativa obrigatória depois, mas
mantemos a mesma prática de log e tratamento de erro do cielo_client.py
por consistência.

Nenhuma rota do blueprint 'pagamentos' deve chamar `requests` direto pra
Asaas — sempre passar por aqui.
"""

import requests
from flask import current_app
from app import db
from app.models import LogIntegracaoPagamento


# ─────────────────────────────────────────────────────────────────────────
# URLs base — TODO CONFIRMAR o valor exato de produção/sandbox contra a
# documentação oficial (docs.asaas.com) antes do primeiro teste real.
# ─────────────────────────────────────────────────────────────────────────
URLS = {
    'sandbox': 'https://api-sandbox.asaas.com/v3',
    'producao': 'https://api.asaas.com/v3',
}


class AsaasClientError(Exception):
    """Erro genérico de comunicação com a Asaas — sempre já logado no banco quando levantado."""
    pass


def _log(tipo_evento, nivel='INFO', resultado=None, payload=None,
         assinatura_recorrente_id=None, recurrent_payment_id=None, payment_id_gateway=None):
    """Grava evento no log_integracao_pagamento — mesma tabela usada pela Cielo,
    já que o campo 'gateway' na assinatura permite diferenciar a origem depois."""
    try:
        log = LogIntegracaoPagamento(
            tipo_evento=tipo_evento,
            nivel=nivel,
            resultado=resultado,
            payload_bruto=payload,
            assinatura_recorrente_id=assinatura_recorrente_id,
            recurrent_payment_id=recurrent_payment_id,
            payment_id_gateway=payment_id_gateway,
        )
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        current_app.logger.error(f"Falha ao gravar log_integracao_pagamento (Asaas): {e}")


def _url_base():
    ambiente = current_app.config.get('ASAAS_AMBIENTE', 'sandbox')
    return URLS.get(ambiente, URLS['sandbox'])


def _headers():
    api_key = current_app.config.get('ASAAS_API_KEY')
    if not api_key:
        raise AsaasClientError("ASAAS_API_KEY não configurada no .env")
    return {
        "access_token": api_key,
        "Content-Type": "application/json",
    }


def _request(metodo, url, assinatura_recorrente_id=None, recurrent_payment_id=None, **kwargs):
    try:
        resp = requests.request(metodo, url, headers=_headers(), timeout=20, **kwargs)
    except requests.exceptions.RequestException as e:
        _log('erro_comunicacao', nivel='ERROR', resultado=str(e),
             assinatura_recorrente_id=assinatura_recorrente_id,
             recurrent_payment_id=recurrent_payment_id)
        raise AsaasClientError(f"Falha de comunicação com a Asaas: {e}")

    if resp.status_code >= 400:
        _log('erro_comunicacao', nivel='ERROR',
             resultado=f"HTTP {resp.status_code}: {resp.text[:500]}",
             assinatura_recorrente_id=assinatura_recorrente_id,
             recurrent_payment_id=recurrent_payment_id)
        raise AsaasClientError(f"Asaas retornou HTTP {resp.status_code}: {resp.text[:300]}")

    return resp


def criar_link_recorrencia(aluno, valor_mensal, data_inicio, data_fim_ciclo=None):
    """
    Cria um Link de Pagamento tipo assinatura (chargeType=RECURRENT) para o aluno.
    Retorna dict com {'link_pagamento_id': ..., 'url_link': ...}

    TODO CONFIRMAR payload exato contra docs.asaas.com/reference/criar-um-link-de-pagamentos
    antes do primeiro teste em sandbox — campos abaixo seguem a doc pública,
    mas nomes podem ter pequenas variações de versão.
    """
    payload = {
        "name": f"Mensalidade recorrente — {aluno.nome}",
        "description": "Cobrança recorrente de mensalidade — GymFlow",
        "billingType": "CREDIT_CARD",
        "chargeType": "RECURRENT",
        "subscriptionCycle": "MONTHLY",
        "value": float(valor_mensal),
        "dueDateLimitDays": 1,
        "notificationEnabled": True,
    }

    resp = _request('POST', f"{_url_base()}/paymentLinks", json=payload)
    dados = resp.json()

    _log('link_criado', resultado=f"Link criado para aluno {aluno.id}", payload=dados)

    return {
        'link_pagamento_id': dados.get('id'),   # TODO CONFIRMAR chave real
        'url_link': dados.get('url'),           # TODO CONFIRMAR chave real
    }


def consultar_cobranca(payment_id):
    """Consulta uma cobrança específica pelo ID — usado, se necessário,
    pra reconciliar manualmente. O webhook da Asaas já manda o payload
    completo, então esse método é mais um recurso de apoio do que uma
    exigência do fluxo principal (diferente da Cielo)."""
    url = f"{_url_base()}/payments/{payment_id}"
    resp = _request('GET', url, payment_id_gateway=payment_id)
    dados = resp.json()
    _log('consulta_respondida', payment_id_gateway=payment_id, resultado='Consulta OK', payload=dados)
    return dados


def atualizar_valor_link(link_pagamento_id, novo_valor):
    """TODO CONFIRMAR endpoint exato de atualização de link — a doc pública
    até o momento desta implementação não detalha claramente PUT para
    paymentLinks; validar contra docs.asaas.com antes de usar em produção."""
    url = f"{_url_base()}/paymentLinks/{link_pagamento_id}"
    resp = _request('PUT', url, json={'value': float(novo_valor)},
                     recurrent_payment_id=link_pagamento_id)
    _log('recorrencia_atualizada', recurrent_payment_id=link_pagamento_id,
         resultado=f'Valor atualizado para {novo_valor}')
    return resp.json()


def desativar_link_pagamento(link_pagamento_id):
    """Desativa um Link de Pagamento que ainda não foi preenchido pelo
    aluno (status 'link_enviado' — nenhuma assinatura foi criada ainda,
    então não há 'subscription' para cancelar, só o link em si)."""
    url = f"{_url_base()}/paymentLinks/{link_pagamento_id}"
    resp = _request('DELETE', url, recurrent_payment_id=link_pagamento_id)
    _log('cancelamento_solicitado', recurrent_payment_id=link_pagamento_id,
         resultado='Link de pagamento desativado na Asaas (aluno não havia aderido ainda)')
    return resp.json() if resp.text else {}


def estornar_pagamento(payment_id, valor=None):
    """Estorna uma cobrança de cartão de crédito já confirmada/recebida.
    Se 'valor' não for informado, estorna o valor total. Ação sempre
    disparada manualmente pela recepção, nunca automática pelo sistema."""
    url = f"{_url_base()}/payments/{payment_id}/refund"
    payload = {}
    if valor is not None:
        payload['value'] = float(valor)

    resp = _request('POST', url, json=payload, payment_id_gateway=payment_id)
    dados = resp.json()
    _log('estorno_solicitado', payment_id_gateway=payment_id,
         resultado=f'Estorno solicitado na Asaas' + (f' — valor: {valor}' if valor else ' — valor total'))
    return dados


def desativar_assinatura(subscription_id):
    """Remove/cancela a assinatura na Asaas — a doc recomenda usar remoção
    definitiva (DELETE) já que não há um estado de 'pausa' documentado
    publicamente para esse fluxo via API (diferente da Cielo, que tem
    Desativar/Reativar). TODO CONFIRMAR se existe endpoint de suspensão
    antes de expor isso como 'cancelamento reversível' pro usuário."""
    url = f"{_url_base()}/subscriptions/{subscription_id}"
    resp = _request('DELETE', url, recurrent_payment_id=subscription_id)
    _log('cancelamento_solicitado', recurrent_payment_id=subscription_id,
         resultado='Assinatura removida na Asaas')
    return resp.json()