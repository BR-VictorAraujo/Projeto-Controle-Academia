# -*- coding: utf-8 -*-
"""
config_inicial.py — Configuracao gerada pelo instalador do GymFlow.

Uso (pelo instalador, com o venv do sistema):
  python config_inicial.py --novo   instalacao nova: gera o .env e depois sincroniza o token
  python config_inicial.py          atualizacao: so sincroniza o token

Na instalacao nova, os dados do banco chegam SO por variaveis de ambiente
(GYMFLOW_DB_HOST, GYMFLOW_DB_PORT, GYMFLOW_DB_NAME, GYMFLOW_DB_USER,
GYMFLOW_DB_PASS), definidas pelo instalador: a senha nunca vai em argumento,
arquivo temporario ou log.

Token do FingerPoint (obrigatorio desde a v1.7.0):
  BIOMETRIA_API_TOKEN no .env precisa ser igual ao api_token do
  biometria/bio_config.json. Se faltar em um dos dois, copia o do outro;
  se faltar nos dois, gera um novo e grava nos dois. Nunca troca um token
  existente nem altera os outros campos/linhas.

Saida: 0 = ok; 3 = tokens diferentes nos dois arquivos (nada alterado,
o instalador so avisa); outro = erro.

Nunca sobrescreve um .env existente. O bio_config.json so e criado (a partir
do bio_config.example.json, com os dados do DATABASE_URL) se nao existir.
"""

import json
import os
import secrets
import sys
from urllib.parse import quote, unquote, urlparse

PASTA_SISTEMA = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAMINHO_ENV = os.path.join(PASTA_SISTEMA, '.env')
PASTA_BIO = os.path.join(PASTA_SISTEMA, 'biometria')
CAMINHO_BIO = os.path.join(PASTA_BIO, 'bio_config.json')
CAMINHO_BIO_EXEMPLO = os.path.join(PASTA_BIO, 'bio_config.example.json')
CHAVE_TOKEN = 'BIOMETRIA_API_TOKEN'

MODELO_ENV = """\
# GymFlow - gerado pelo instalador. Nao versionar este arquivo.
SECRET_KEY={secret_key}
DATABASE_URL={database_url}

# Token do FingerPoint (header X-GymFlow-Token). Igual ao api_token do bio_config.json.
BIOMETRIA_API_TOKEN=

# Pagamentos (preencher quando for usar)
CIELO_CLIENT_ID=
CIELO_CLIENT_SECRET=
CIELO_AMBIENTE=sandbox
CIELO_WEBHOOK_USER=
CIELO_WEBHOOK_PASS=
ASAAS_API_KEY=
ASAAS_AMBIENTE=sandbox
ASAAS_WEBHOOK_TOKEN=
"""


def dados_do_ambiente():
    d = {chave: os.environ.get('GYMFLOW_DB_' + var, '') for chave, var in
         (('host', 'HOST'), ('porta', 'PORT'), ('banco', 'NAME'), ('usuario', 'USER'), ('senha', 'PASS'))}
    faltando = [k for k in ('host', 'porta', 'banco', 'usuario') if not d[k].strip()]
    if faltando:
        raise RuntimeError('Dados do banco ausentes no ambiente: ' + ', '.join(faltando))
    return d


def token_valido(valor):
    """Vazio ou o texto de exemplo (TROQUE_AQUI...) contam como ausentes."""
    valor = (valor or '').strip()
    return bool(valor) and not valor.upper().startswith('TROQUE_AQUI')


# ── .env ──────────────────────────────────────────────────────────────────────

def ler_env(chave):
    if not os.path.exists(CAMINHO_ENV):
        return ''
    with open(CAMINHO_ENV, encoding='utf-8-sig') as f:
        for linha in f.read().splitlines():
            if linha.strip().startswith(chave + '='):
                return linha.split('=', 1)[1].strip().strip('"\'')
    return ''


def gravar_token_env(token):
    """Preenche a linha BIOMETRIA_API_TOKEN (vazia) ou acrescenta no fim. O resto fica igual."""
    with open(CAMINHO_ENV, 'rb') as f:
        bruto = f.read()
    texto = bruto.decode('utf-8-sig')
    nl = '\r\n' if '\r\n' in texto else '\n'
    linhas = texto.splitlines()
    for i, linha in enumerate(linhas):
        if linha.strip().startswith(CHAVE_TOKEN + '='):
            linhas[i] = f'{CHAVE_TOKEN}={token}'
            break
    else:
        if linhas and linhas[-1].strip():
            linhas.append('')
        linhas += ['# Token do FingerPoint (header X-GymFlow-Token). Igual ao api_token do bio_config.json.',
                   f'{CHAVE_TOKEN}={token}']
    saida = nl.join(linhas) + nl
    with open(CAMINHO_ENV, 'wb') as f:
        f.write((b'\xef\xbb\xbf' if bruto.startswith(b'\xef\xbb\xbf') else b'') + saida.encode('utf-8'))


def criar_env(d):
    url = 'postgresql://{}:{}@{}:{}/{}'.format(
        quote(d['usuario'], safe=''), quote(d['senha'], safe=''),
        d['host'], d['porta'], quote(d['banco'], safe=''))
    with open(CAMINHO_ENV, 'w', encoding='utf-8', newline='\n') as f:
        f.write(MODELO_ENV.format(secret_key=secrets.token_hex(32), database_url=url))
    print('.env criado.')


# ── bio_config.json ───────────────────────────────────────────────────────────

def dados_do_database_url():
    u = urlparse(ler_env('DATABASE_URL'))
    return {
        'host':    u.hostname or 'localhost',
        'porta':   str(u.port or 5432),
        'banco':   u.path.lstrip('/'),
        'usuario': unquote(u.username or 'postgres'),
        'senha':   unquote(u.password or ''),
    }


def criar_bio_config():
    """Cria o bio_config.json a partir do exemplo, com o banco do .env. So se nao existir."""
    if os.path.exists(CAMINHO_BIO) or not os.path.exists(CAMINHO_BIO_EXEMPLO):
        return
    with open(CAMINHO_BIO_EXEMPLO, encoding='utf-8') as f:
        bio = json.load(f)
    d = dados_do_database_url()
    bio.update({'host': d['host'], 'port': d['porta'], 'dbname': d['banco'],
                'user': d['usuario'], 'password': d['senha']})
    with open(CAMINHO_BIO, 'w', encoding='utf-8') as f:
        json.dump(bio, f, indent=2, ensure_ascii=False)
    print('biometria/bio_config.json criado a partir do exemplo.')


def ler_bio():
    with open(CAMINHO_BIO, encoding='utf-8-sig') as f:
        return json.load(f)


def gravar_token_bio(token):
    bio = ler_bio()
    bio['api_token'] = token  # demais campos ficam como estao
    with open(CAMINHO_BIO, 'w', encoding='utf-8') as f:
        json.dump(bio, f, indent=2, ensure_ascii=False)


# ── Token ─────────────────────────────────────────────────────────────────────

def sincronizar_token():
    if not os.path.exists(CAMINHO_ENV):
        raise RuntimeError('.env nao encontrado.')
    tem_bio = os.path.exists(CAMINHO_BIO)

    token_env = ler_env(CHAVE_TOKEN)
    token_bio = ler_bio().get('api_token', '') if tem_bio else ''
    ok_env, ok_bio = token_valido(token_env), token_valido(token_bio)

    if ok_env and (ok_bio or not tem_bio):
        if tem_bio and token_env != token_bio:
            print(f'ATENCAO: {CHAVE_TOKEN} do .env e api_token do bio_config.json sao diferentes. '
                  'Nada foi alterado: o FingerPoint vai receber 401 ate os dois ficarem iguais.')
            return False
        print('Token do FingerPoint ja configurado.')
        return True

    token = token_env if ok_env else token_bio if ok_bio else secrets.token_hex(32)
    if not ok_env:
        gravar_token_env(token)
        print(f'{CHAVE_TOKEN} gravado no .env.')
    if tem_bio and not ok_bio:
        gravar_token_bio(token)
        print('api_token gravado no biometria/bio_config.json.')
    return True


def main():
    if sys.argv[1:2] == ['--novo']:
        if os.path.exists(CAMINHO_ENV):
            print('.env ja existe: mantido sem alteracao.')
        else:
            criar_env(dados_do_ambiente())

    if os.path.isdir(PASTA_BIO):
        criar_bio_config()
    if not sincronizar_token():
        sys.exit(3)  # tokens diferentes: o instalador avisa e segue


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f'ERRO: {e}')
        sys.exit(1)
