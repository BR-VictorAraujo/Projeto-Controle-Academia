# -*- coding: utf-8 -*-
"""
atualizar_banco.py — Prepara o banco na instalacao/atualizacao do GymFlow.

Executado pelo instalador (venv do sistema, pasta do sistema como diretorio
de trabalho), com o servico parado:

  1. Cria o banco, se ainda nao existir (instalacao nova).
  2. inicializar_banco() do run.py: db.create_all() + registros padrao
     (planos, configuracoes, usuario admin). Idempotente.
  3. Aplica as migracoes de migrations/ em ordem, com psql -v ON_ERROR_STOP=1.

Migracoes (ver migrations/README.md):
  - Arquivos NNN_*.sql, em ordem de nome. Ficam de fora os *_down.sql e os
    que nao comecam com numero (ex.: opcional_role_app.sql).
  - Cada migracao e idempotente e roda numa transacao propria.
  - Pula a migracao cujo nome (sem .sql e sem _up) ja esta registrado em
    schema_migracao.versao. As demais rodam de novo sem efeito (idempotentes).

Saida: codigo 0 = ok; diferente de 0 = falha (o instalador avisa e para).
"""

import glob
import os
import re
import subprocess
import sys

PASTA_SISTEMA = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PASTA_SISTEMA)
os.chdir(PASTA_SISTEMA)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(PASTA_SISTEMA, '.env'))

from app.backup import extrair_db_config, localizar_pg_dump  # noqa: E402

RE_MIGRACAO = re.compile(r'^\d{3}_.*\.sql$')


def localizar_psql():
    """psql.exe: o detectado pelo instalador (GYMFLOW_PG_BIN) ou o da pasta do pg_dump.exe."""
    pasta_instalador = os.environ.get('GYMFLOW_PG_BIN', '')
    if pasta_instalador and os.path.exists(os.path.join(pasta_instalador, 'psql.exe')):
        return os.path.join(pasta_instalador, 'psql.exe')
    pg_dump = localizar_pg_dump()
    if pg_dump == 'pg_dump':
        # Fora dos caminhos padrao do backup.py: tenta qualquer versao instalada
        candidatos = sorted(glob.glob(r'C:\Program Files\PostgreSQL\*\bin\psql.exe'), reverse=True)
        return candidatos[0] if candidatos else 'psql'
    psql = os.path.join(os.path.dirname(pg_dump), 'psql.exe')
    return psql if os.path.exists(psql) else 'psql'


def conectar(cfg, banco):
    import psycopg2
    return psycopg2.connect(host=cfg['host'], port=cfg['port'], user=cfg['user'],
                            password=cfg['password'], dbname=banco)


def garantir_banco(cfg):
    """CREATE DATABASE se o banco do .env ainda nao existir."""
    con = conectar(cfg, 'postgres')
    con.autocommit = True
    try:
        with con.cursor() as cur:
            cur.execute('SELECT 1 FROM pg_database WHERE datname = %s', (cfg['database'],))
            if cur.fetchone():
                return
            print(f"Banco '{cfg['database']}' nao existe. Criando...")
            cur.execute('CREATE DATABASE "%s"' % cfg['database'].replace('"', '""'))
    finally:
        con.close()


def migracoes_registradas(cfg):
    con = conectar(cfg, cfg['database'])
    try:
        with con.cursor() as cur:
            cur.execute("SELECT to_regclass('schema_migracao') IS NOT NULL")
            if not cur.fetchone()[0]:
                return set()
            cur.execute('SELECT versao FROM schema_migracao')
            return {linha[0] for linha in cur.fetchall()}
    finally:
        con.close()


def aplicar_migracoes(cfg):
    pasta = os.path.join(PASTA_SISTEMA, 'migrations')
    arquivos = sorted(f for f in os.listdir(pasta) if RE_MIGRACAO.match(f)
                      and not f.endswith('_down.sql')) if os.path.isdir(pasta) else []
    if not arquivos:
        print('Nenhuma migracao em migrations/.')
        return

    psql = localizar_psql()
    env = os.environ.copy()
    env['PGPASSWORD'] = cfg['password']  # senha fora do argv
    env['PGCLIENTENCODING'] = 'UTF8'

    for arquivo in arquivos:
        nome = re.sub(r'(_up)?\.sql$', '', arquivo)
        if nome in migracoes_registradas(cfg):
            print(f'[ja aplicada] {arquivo}')
            continue
        print(f'[aplicando]   {arquivo}')
        resultado = subprocess.run(
            [psql, '-h', cfg['host'], '-p', str(cfg['port']), '-U', cfg['user'],
             '-d', cfg['database'], '-v', 'ON_ERROR_STOP=1', '-q', '-f', os.path.join(pasta, arquivo)],
            env=env, capture_output=True, text=True, encoding='utf-8', errors='replace',
        )
        if resultado.stdout.strip():
            print(resultado.stdout.strip())
        if resultado.stderr.strip():
            print(resultado.stderr.strip())
        if resultado.returncode != 0:
            raise RuntimeError(f'Migracao {arquivo} falhou (psql codigo {resultado.returncode}). '
                               f'Nada dela foi gravado (transacao desfeita).')


def main():
    cfg = extrair_db_config(os.getenv('DATABASE_URL'))
    print(f"Banco: {cfg['user']}@{cfg['host']}:{cfg['port']}/{cfg['database']}")
    garantir_banco(cfg)

    from run import inicializar_banco
    inicializar_banco()

    aplicar_migracoes(cfg)
    print('Banco atualizado.')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f'ERRO: {e}')
        sys.exit(1)
