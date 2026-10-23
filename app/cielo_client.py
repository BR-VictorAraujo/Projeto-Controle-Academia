"""
Wrapper isolado para toda a comunicação com a API Cielo (Link de Pagamento).

URLs e payload confirmados contra docs.cielo.com.br/link/reference
(consultado em 17/08/2026). A API do Link de Pagamento NAO tem ambiente
de sandbox separado -- testes sao feitos habilitando "Modo Teste" na
propria conta, no site Cielo (nao muda a URL usada).

Centraliza aqui: autenticacao OAuth (com cache de token em memoria),
criacao de link de pagamento recorrente, exclusao de link pendente,
desativacao de recorrencia ja ativa.

Nenhuma rota do blueprint 'pagamentos' deve chamar `requests` direto --
sempre passar por aqui, para manter log e tratamento de erro consistentes.
"""

import base64
import time
import requests
from flask import current_app
from app import db
from app.models import LogIntegracaoPagamento


# ---------------------------------------------------------------------------
# URLs -- confirmadas contra docs.cielo.com.br/link/reference.
# Nao existe sandbox separado para o Link de Pagamento; "Modo Teste" e
# uma configuracao de conta no site Cielo, nao uma URL diferente.
# ---------------------------------------------------------------------------
URL_AUTH = "https://cieloecommerce.cielo.com.br/api/public/v2/token"
URL_PRODUCTS = "https://cieloecommerce.cielo.com.br/api/public/v1/products/"
URL_RECURRENT_DEACTIVATE = "https://cieloecommerce.cielo.com.br/api/public/v1/RecurrentPayment/Deactivate/{id}"
# TODO CONFIRMAR -- nao localizadas na documentacao consultada ate agora:
URL_RECURRENT_QUERY = "https://cieloecommerce.cielo.com.br/api/public/v1/RecurrentPayment/{id}"
URL_RECURRENT_UPDATE_VALUE = "https://cieloecommerce.cielo.com.br/api/public/v1/RecurrentPayment/{id}/Value"

_token_cache = {
    'access_token': None,
    'expira_em': 0,
}


class CieloClientError(Exception):
    pass


def _log(tipo_evento, nivel='INFO', resultado=None, payload=None,
         assinatura_recorrente_id=None, recurrent_payment_id=None, payment_id_gateway=None):
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
        current_app.logger.error(f"Falha ao gravar log_integracao_pagamento: {e}")


def _obter_token():
    agora = time.time()

    if _token_cache['access_token'] and agora < _token_cache['expira_em']:
        return _token_cache['access_token']

    client_id = current_app.config.get('CIELO_CLIENT_ID')
    client_secret = current_app.config.get('CIELO_CLIENT_SECRET')

    if not client_id or not client_secret:
        raise CieloClientError(
            "CIELO_CLIENT_ID / CIELO_CLIENT_SECRET nao configurados no .env"
        )

    credenciais = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()

    try:
        resp = requests.post(
            URL_AUTH,
            headers={
                'Authorization': f'Basic {credenciais}',
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            data={'grant_type': 'client_credentials'},
            timeout=15,
        )
    except requests.exceptions.RequestException as e:
        _log('erro_autenticacao', nivel='ERROR', resultado=str(e))
        raise CieloClientError(f"Falha ao autenticar na Cielo: {e}")

    if resp.status_code >= 400:
        _log('erro_autenticacao', nivel='ERROR',
             resultado=f"HTTP {resp.status_code}: {resp.text[:500]}")
        raise CieloClientError(f"Falha ao autenticar na Cielo: HTTP {resp.status_code} - {resp.text[:400]}")

    dados = resp.json()
    access_token = dados.get('access_token')
    expira_em_segundos = int(dados.get('expires_in', 1200))

    if not access_token:
        _log('erro_autenticacao', nivel='ERROR', resultado='Resposta sem access_token', payload=dados)
        raise CieloClientError("Cielo nao retornou access_token")

    _token_cache['access_token'] = access_token
    _token_cache['expira_em'] = agora + expira_em_segundos - 60

    return access_token


def _headers():
    return {
        'Authorization': f'Bearer {_obter_token()}',
        'Content-Type': 'application/json',
    }


def _request(metodo, url, assinatura_recorrente_id=None, recurrent_payment_id=None, **kwargs):
    try:
        resp = requests.request(metodo, url, headers=_headers(), timeout=20, **kwargs)
    except requests.exceptions.RequestException as e:
        _log('erro_comunicacao', nivel='ERROR', resultado=str(e),
             assinatura_recorrente_id=assinatura_recorrente_id,
             recurrent_payment_id=recurrent_payment_id)
        raise CieloClientError(f"Falha de comunicacao com a Cielo: {e}")

    if resp.status_code >= 400:
        _log('erro_comunicacao', nivel='ERROR',
             resultado=f"HTTP {resp.status_code}: {resp.text[:500]}",
             assinatura_recorrente_id=assinatura_recorrente_id,
             recurrent_payment_id=recurrent_payment_id)
        raise CieloClientError(f"Cielo retornou HTTP {resp.status_code}: {resp.text[:300]}")

    return resp


def criar_link_recorrencia(aluno, valor_mensal, data_inicio, data_fim_ciclo=None):
    order_number = f"assin{aluno.id}{int(time.time())}"[:20]

    payload = {
        "OrderNumber": order_number,
        "type": "Recurrent",
        "name": f"Mensalidade {aluno.nome}"[:128],
        "description": "Cobranca recorrente de mensalidade - GymFlow",
        "showDescription": True,
        "price": int(round(float(valor_mensal) * 100)),
        "softDescriptor": "GYMFLOW",
        "shipping": {
            "type": "WithoutShipping",
        },
        "recurrent": {
            "interval": "Monthly",
        }
    }
    if data_fim_ciclo:
        payload["recurrent"]["endDate"] = data_fim_ciclo.strftime('%Y-%m-%d')

    resp = _request('POST', URL_PRODUCTS, json=payload)
    dados = resp.json()

    _log('link_criado', resultado=f"Link criado para aluno {aluno.id}", payload=dados)

    return {
        'link_pagamento_id': dados.get('id') or dados.get('productId'),
        'url_link': dados.get('url') or dados.get('shortUrl') or dados.get('paymentUrl'),
        'order_number': order_number,
    }


def excluir_link_pagamento(link_pagamento_id):
    url = URL_PRODUCTS.rstrip('/') + f'/{link_pagamento_id}'
    resp = _request('DELETE', url, recurrent_payment_id=link_pagamento_id)
    _log('cancelamento_solicitado', recurrent_payment_id=link_pagamento_id,
         resultado='Link de pagamento excluido na Cielo')
    return {} if resp.status_code == 204 else resp.json()


def consultar_recorrencia(recurrent_payment_id):
    url = URL_RECURRENT_QUERY.format(id=recurrent_payment_id)
    resp = _request('GET', url, recurrent_payment_id=recurrent_payment_id)
    dados = resp.json()
    _log('consulta_respondida', recurrent_payment_id=recurrent_payment_id,
         resultado='Consulta OK', payload=dados)
    return dados


def atualizar_valor_recorrencia(recurrent_payment_id, novo_valor):
    url = URL_RECURRENT_UPDATE_VALUE.format(id=recurrent_payment_id)
    resp = _request('PUT', url, json={'value': int(round(float(novo_valor) * 100))},
                     recurrent_payment_id=recurrent_payment_id)
    _log('recorrencia_atualizada', recurrent_payment_id=recurrent_payment_id,
         resultado=f'Valor atualizado para {novo_valor}')
    return resp.json() if resp.text else {}


def atualizar_data_fim_recorrencia(recurrent_payment_id, nova_data_fim):
    raise CieloClientError(
        "Atualizacao de data fim da recorrencia ainda nao confirmada contra a "
        "documentacao da Cielo -- implementar quando o endpoint for validado."
    )


def desativar_recorrencia(pagador_recurrent_payment_id):
    url = URL_RECURRENT_DEACTIVATE.format(id=pagador_recurrent_payment_id)
    resp = _request('DELETE', url, recurrent_payment_id=pagador_recurrent_payment_id)
    _log('cancelamento_solicitado', recurrent_payment_id=pagador_recurrent_payment_id,
         resultado='Recorrencia desativada na Cielo (irreversivel)')
    return resp.json() if resp.text else {}