#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gera a migração SQL que publica uma nova versão do termo LGPD do GymFlow.

Uso:
    python tools/gerar_migracao_termo.py <arquivo.txt> <versao> [--saida DIR]

Exemplo:
    python tools/gerar_migracao_termo.py docs/lgpd/termo/termo_v1.0.txt 1.0

- Recusa o texto se ainda houver trechos "[PREENCHER".
- Canonicaliza o texto com a mesma regra de app/lgpd.py (canonicalizar):
  CRLF e CR viram LF, remove espaços/tabs no fim de cada linha, remove linhas
  vazias no fim, Unicode NFC.
- Calcula o SHA-256 (hex) do texto canônico em UTF-8.
- Gera migrations/NNN_termo_v<versao>.sql (NNN = próximo número livre em
  migrations/). Com --saida, grava o arquivo em outro diretório (para testes),
  mantendo a numeração calculada a partir de migrations/.

Só usa a biblioteca padrão. Não acessa banco de dados.
"""
import argparse
import datetime
import hashlib
import re
import sys
import unicodedata
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DIR_MIGRACOES = RAIZ / 'migrations'
RE_VERSAO = re.compile(r'^[0-9A-Za-z][0-9A-Za-z._-]{0,19}$')
RE_NUMERADA = re.compile(r'^(\d{3})_.*[.]sql$')

D = chr(36)  # cifrão (dollar-quoting do PostgreSQL)
BS = chr(92)  # barra invertida

# Mesma normalização no SQL (robusto se o .sql for salvo com CRLF). O hash
# literal é conferido pelo trigger lgpd_termo_versao_valida.
_CR, _LF, _TAB = "E'" + BS + "r'", "E'" + BS + "n'", BS + 't'
SQL_NORMALIZA = (
    'normalize(rtrim(regexp_replace(regexp_replace('
    + 'replace(replace(s.bruto, ' + "E'" + BS + 'r' + BS + "n'" + ', ' + _LF + '), ' + _CR + ', ' + _LF + '), '
    + "E'[ " + _TAB + ']+' + BS + "n', " + _LF + ", 'g'), "
    + "E'[ " + _TAB + ']+' + D + "', ''), "
    + _LF + '), NFC)'
)


def canonicalizar(texto):
    """Igual a app/lgpd.py: CRLF/CR -> LF; remove espaços e tabs no fim de cada linha;
    remove linhas vazias no fim; Unicode NFC."""
    if texto is None:
        texto = ''
    t = texto.replace('\r\n', '\n').replace('\r', '\n')
    linhas = [linha.rstrip(' \t') for linha in t.split('\n')]
    while linhas and linhas[-1] == '':
        linhas.pop()
    return unicodedata.normalize('NFC', '\n'.join(linhas))


def hash_conteudo(texto):
    return hashlib.sha256(canonicalizar(texto).encode('utf-8')).hexdigest()


def erro(msg):
    print('ERRO: ' + msg, file=sys.stderr)
    sys.exit(1)


def proximo_numero():
    numeros = []
    for p in DIR_MIGRACOES.glob('*.sql'):
        m = RE_NUMERADA.match(p.name)
        if m:
            numeros.append(int(m.group(1)))
    return max(numeros, default=0) + 1


def tag_dollar(texto):
    tag = 'termo'
    n = 0
    while (D + tag + D) in texto:
        n += 1
        tag = 'termo_%d' % n
    return D + tag + D


def sql_literal(valor):
    return "'" + valor.replace("'", "''") + "'"


def gerar_sql(numero, versao, texto, hash_hex, origem):
    id_migracao = '%03d_termo_v%s' % (numero, versao)
    tag = tag_dollar(texto)
    agora = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    v = sql_literal(versao)
    h = sql_literal(hash_hex)
    pre, pos = D + 'pre' + D, D + 'pos' + D
    partes = [
        '-- =============================================================================',
        '-- GymFlow - Migração %03d: publica a versão %s do termo LGPD' % (numero, versao),
        '-- Gerado por tools/gerar_migracao_termo.py em %s' % agora,
        '-- Fonte : %s' % origem,
        '-- SHA-256 do texto canônico: %s' % hash_hex,
        '--',
        '-- Publicar uma versão faz TODOS os usuários aceitarem o termo de novo.',
        '-- Versões são imutáveis: para corrigir o texto, gere uma nova versão.',
        '-- Idempotente: se esta versão já existir com o mesmo hash, nada muda.',
        '--',
        '-- Aplicar (depois da 001):',
        '--   psql -U <usuario> -d <banco> -v ON_ERROR_STOP=1 -f %s.sql' % id_migracao,
        '-- Rollback: não há. Versão publicada não pode ser alterada nem excluída.',
        '-- =============================================================================',
        '',
        "SET client_encoding = 'UTF8';",
        '',
        'BEGIN;',
        '',
        'DO ' + pre,
        'BEGIN',
        "    IF to_regclass('schema_migracao') IS NULL",
        "       OR NOT EXISTS (SELECT 1 FROM schema_migracao WHERE versao = '1.7.0_001_lgpd') THEN",
        "        RAISE EXCEPTION 'Migração 001_v1.7.0_lgpd não aplicada: aplique-a antes desta.';",
        '    END IF;',
        'END;',
        pre + ';',
        '',
        'INSERT INTO lgpd_termo_versao (versao, conteudo, conteudo_hash)',
        'SELECT %s, c.conteudo, %s' % (v, h),
        '  FROM (SELECT %s AS conteudo' % SQL_NORMALIZA,
        '          FROM (SELECT ' + tag + texto + tag + '::text AS bruto) s) c',
        'ON CONFLICT (versao) DO NOTHING;',
        '',
        '-- Se a versão já existia com outro texto, aborta (versões são imutáveis).',
        'DO ' + pos,
        'BEGIN',
        '    IF NOT EXISTS (SELECT 1 FROM lgpd_termo_versao',
        '                    WHERE versao = %s AND conteudo_hash = %s) THEN' % (v, h),
        "        RAISE EXCEPTION 'Versão % do termo já existe com outro conteúdo (hash diferente).', " + v + ';',
        '    END IF;',
        'END;',
        pos + ';',
        '',
        'INSERT INTO schema_migracao (versao, descricao)',
        'VALUES (%s, %s)' % (sql_literal(id_migracao),
                             sql_literal('Termo LGPD versão %s (sha256 %s)' % (versao, hash_hex))),
        'ON CONFLICT (versao) DO NOTHING;',
        '',
        'COMMIT;',
        '',
    ]
    return id_migracao, '\n'.join(partes)


def main():
    ap = argparse.ArgumentParser(description='Gera a migração SQL de uma nova versão do termo LGPD.')
    ap.add_argument('arquivo', help='arquivo .txt (UTF-8) com o texto final do termo')
    ap.add_argument('versao', help='identificador da versão, ex.: 1.0 (até 20 caracteres)')
    ap.add_argument('--saida', help='diretório de saída (padrão: migrations/)')
    args = ap.parse_args()

    versao = args.versao.strip()
    if not RE_VERSAO.match(versao):
        erro('versão inválida: use até 20 caracteres entre letras, números, ponto, hífen e sublinhado.')

    origem = Path(args.arquivo)
    try:
        bruto = origem.read_text(encoding='utf-8-sig')
    except FileNotFoundError:
        erro('arquivo não encontrado: %s' % origem)
    except UnicodeDecodeError:
        erro('o arquivo não está em UTF-8: %s' % origem)

    if '[PREENCHER' in bruto.upper():
        linhas = [str(i) for i, l in enumerate(bruto.splitlines(), 1) if '[PREENCHER' in l.upper()]
        erro('o texto ainda contém trechos [PREENCHER ...] (linhas: %s). Complete o texto antes de publicar.'
             % ', '.join(linhas))
    if chr(0) in bruto:
        erro('o texto contém caractere nulo.')

    texto = canonicalizar(bruto)
    if not texto.strip():
        erro('o texto está vazio.')
    hash_hex = hashlib.sha256(texto.encode('utf-8')).hexdigest()

    existentes = sorted(p.name for p in DIR_MIGRACOES.glob('*_termo_v%s.sql' % versao))
    if existentes:
        erro('já existe migração para a versão %s: %s' % (versao, ', '.join(existentes)))

    numero = proximo_numero()
    id_migracao, sql = gerar_sql(numero, versao, texto, hash_hex, origem.as_posix())

    destino_dir = Path(args.saida) if args.saida else DIR_MIGRACOES
    destino_dir.mkdir(parents=True, exist_ok=True)
    destino = destino_dir / (id_migracao + '.sql')
    if destino.exists():
        erro('arquivo de saída já existe: %s' % destino)
    with open(destino, 'w', encoding='utf-8', newline='\n') as f:
        f.write(sql)

    print('Versao  : %s' % versao)
    print('SHA-256 : %s' % hash_hex)
    print('Gerado  : %s' % destino.resolve())


if __name__ == '__main__':
    main()
