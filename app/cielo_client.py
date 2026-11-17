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
import re
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
# v1.7.2 -- validadas com chamadas reais em producao (nov/2026):
#   LINK_PAYMENTS: todas as cobrancas do link, inclusive as mensalidades da
#     recorrencia ("orders": orderNumber = chave do aviso, payment.status
#     "Paid"/"Denied", payment.price em centavos, createdDate ISO local).
#   ORDER: detalhe de uma cobranca (payment.errorCode = motivo da recusa).
#     A resposta traz dados pessoais do cliente: NUNCA gravar em log.
URL_LINK_PAYMENTS = "https://cieloecommerce.cielo.com.br/api/public/v1/products/{id}/payments"
URL_ORDER = "https://cieloecommerce.cielo.com.br/api/public/v2/orders/{id}"
# TODO CONFIRMAR -- nao localizadas na documentacao consultada ate agora:
URL_RECURRENT_QUERY = "https://cieloecommerce.cielo.com.br/api/public/v1/RecurrentPayment/{id}"
URL_RECURRENT_UPDATE_VALUE = "https://cieloecommerce.cielo.com.br/api/public/v1/RecurrentPayment/{id}/Value"

_token_cache = {
    'access_token': None,
    'expira_em': 0,
}


class CieloClientError(Exception):
    def __init__(self, mensagem='', motivo=None):
        super().__init__(mensagem)
        # v1.7.2: motivo curto, sem payload, para log (ex.: "credencial recusada (HTTP 401)")
        self.motivo = motivo or mensagem


class CieloAuthError(CieloClientError):
    """v1.7.2: credencial recusada ou resposta de token invalida."""


class CieloComunicacaoError(CieloClientError):
    """v1.7.2: rede fora ou timeout (sem resposta da Cielo)."""


# v1.7.2: chave do pedido (checkout_cielo_order_number / meucheckout_order_number /
# orderNumber) = 20 ou 32 hexadecimais. Padrao ^[0-9a-fA-F]{20}$|^[0-9a-fA-F]{32}$
# aplicado com fullmatch ('$' com re.match aceitaria quebra de linha no fim).
_RE_CHAVE_PEDIDO = re.compile(r'[0-9a-fA-F]{20}|[0-9a-fA-F]{32}')


def chave_pedido_valida(chave):
    return isinstance(chave, str) and _RE_CHAVE_PEDIDO.fullmatch(chave) is not None


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
        # v1.7.2: sem rollback a sessão ficava inválida (PendingRollbackError)
        try:
            db.session.rollback()
        except Exception:
            pass
        # v1.7.2: so o tipo do erro (a mensagem do banco pode trazer o payload)
        current_app.logger.error(f"Falha ao gravar log_integracao_pagamento: {type(e).__name__}")


def _obter_token(registrar_falha=True):
    """registrar_falha=False: nao grava 'erro_autenticacao' (a reconciliacao
    grava uma unica linha ao interromper a rodada)."""
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
        if registrar_falha:
            _log('erro_autenticacao', nivel='ERROR', resultado=str(e))
        raise CieloComunicacaoError(f"Falha ao autenticar na Cielo: {e}",
                                    motivo=f'sem resposta da Cielo ({type(e).__name__})')

    if resp.status_code >= 400:
        if registrar_falha:
            _log('erro_autenticacao', nivel='ERROR',
                 resultado=f"HTTP {resp.status_code}: {resp.text[:500]}")
        raise CieloAuthError(f"Falha ao autenticar na Cielo: HTTP {resp.status_code} - {resp.text[:400]}",
                             motivo=f'credencial recusada (HTTP {resp.status_code})')

    try:
        dados = resp.json()
    except ValueError:
        dados = {}
    access_token = dados.get('access_token') if isinstance(dados, dict) else None

    if not access_token:
        if registrar_falha:
            _log('erro_autenticacao', nivel='ERROR', resultado='Resposta sem access_token',
                 payload=dados if isinstance(dados, dict) else None)
        raise CieloAuthError("Cielo nao retornou access_token", motivo='resposta do token sem access_token')
    expira_em_segundos = int(dados.get('expires_in', 1200))

    _token_cache['access_token'] = access_token
    _token_cache['expira_em'] = agora + expira_em_segundos - 60

    return access_token


def _headers(registrar_falha=True):
    return {
        'Authorization': f'Bearer {_obter_token(registrar_falha)}',
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


def _get_simples(url, timeout, tentativas, registrar_falha_token=True):
    """GET com Bearer, sem gravar payload em log (respostas com dados
    pessoais). Retentativa so em erro de rede. Erros viram CieloClientError:
    CieloAuthError (token recusado), CieloComunicacaoError (rede/timeout)."""
    ultimo_erro = None
    for _ in range(tentativas):
        try:
            resp = requests.get(url, headers=_headers(registrar_falha_token), timeout=timeout)
        except requests.exceptions.RequestException as e:
            ultimo_erro = e
            continue
        if resp.status_code in (401, 403):
            # token recusado: descarta o cache para a proxima chamada pedir outro
            _token_cache['access_token'] = None
            raise CieloAuthError(f"Cielo retornou HTTP {resp.status_code}",
                                 motivo=f'token recusado (HTTP {resp.status_code})')
        if resp.status_code >= 400:
            raise CieloClientError(f"Cielo retornou HTTP {resp.status_code}")
        return resp.json()
    raise CieloComunicacaoError(f"Falha de comunicacao com a Cielo: {type(ultimo_erro).__name__}",
                                motivo=f'sem resposta da Cielo ({type(ultimo_erro).__name__})')


def listar_pagamentos_link(link_pagamento_id, timeout=15):
    """v1.7.2 -- cobrancas do link (reconciliacao). Uma retentativa em erro de rede.
    Falha do token nao grava 'erro_autenticacao': a reconciliacao grava uma linha so."""
    return _get_simples(URL_LINK_PAYMENTS.format(id=link_pagamento_id), timeout, tentativas=2,
                        registrar_falha_token=False)


def consultar_pedido(order_number, timeout=5):
    """v1.7.2 -- detalhe da cobranca (motivo da recusa em payment.errorCode).
    So aceita chave no formato da Cielo (evita montar URL com caminho arbitrario)."""
    if not chave_pedido_valida(order_number):
        raise CieloClientError("Chave do pedido fora do padrao (20 ou 32 hexadecimais)")
    return _get_simples(URL_ORDER.format(id=order_number), timeout, tentativas=1)
