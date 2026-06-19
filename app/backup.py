# -*- coding: utf-8 -*-
"""
backup.py — Backup automatico e manual do banco PostgreSQL.

Estrategia:
  - pg_dump em formato custom (-Fc), compactado e restauravel com pg_restore.
  - Agendamento via APScheduler, rodando em thread de background dentro
    do proprio processo Flask (BackgroundScheduler).
  - Rotatividade: mantem apenas os N backups mais recentes na pasta
    configurada, apagando os mais antigos automaticamente.

Esse modulo nao depende do Flask diretamente (so de Configuracao via
parametro), entao pode ser testado isoladamente.
"""

import os
import glob
import shutil
import subprocess
from datetime import datetime
from urllib.parse import urlparse, unquote

# ── Localizacao do pg_dump ────────────────────────────────────────────────────
CAMINHOS_PG_DUMP_PADRAO = [
    r'C:\Program Files\PostgreSQL\16\bin\pg_dump.exe',
    r'C:\Program Files\PostgreSQL\15\bin\pg_dump.exe',
    r'C:\Program Files\PostgreSQL\14\bin\pg_dump.exe',
]


def localizar_pg_dump(caminho_configurado=None):
    """
    Retorna o caminho do pg_dump.exe a usar.
    Prioridade: caminho configurado pelo usuario > deteccao automatica >
    'pg_dump' assumindo que esta no PATH do sistema.
    """
    if caminho_configurado and os.path.exists(caminho_configurado):
        return caminho_configurado
    for caminho in CAMINHOS_PG_DUMP_PADRAO:
        if os.path.exists(caminho):
            return caminho
    return 'pg_dump'  # ultimo recurso: confia no PATH


def extrair_db_config(database_url):
    """
    Extrai host/port/database/user/password de uma DATABASE_URL no
    formato usado pelo SQLAlchemy/psycopg, ex:
        postgresql://usuario:senha@localhost:5432/nome_banco
        postgresql+psycopg://usuario:senha@localhost:5432/nome_banco

    Esse projeto usa DATABASE_URL como unica fonte de configuracao do
    banco (ver app/config.py), entao o backup precisa fazer esse parse
    em vez de assumir campos separados (DB_HOST, DB_USER, etc) que nao
    existem na config do projeto.
    """
    if not database_url:
        raise BackupError("DATABASE_URL nao configurada — nao e possivel fazer backup.")

    parsed = urlparse(database_url)

    if not parsed.hostname or not parsed.path.lstrip('/'):
        raise BackupError(f"DATABASE_URL em formato inesperado: '{database_url}'")

    return {
        'host':     parsed.hostname,
        'port':     parsed.port or 5432,
        'database': parsed.path.lstrip('/'),
        'user':     unquote(parsed.username) if parsed.username else 'postgres',
        'password': unquote(parsed.password) if parsed.password else '',
    }


class BackupError(Exception):
    """Erro especifico de operacoes de backup, para diferenciar de erros genericos."""
    pass


def fazer_backup(db_config, pasta_destino, pg_dump_path=None, extensao='dump'):
    """
    Executa pg_dump e salva o arquivo na pasta de destino.

    db_config: dict com host, port, database, user, password
    pasta_destino: caminho da pasta onde salvar (criada se nao existir)
    pg_dump_path: caminho explicito do pg_dump.exe (opcional)
    extensao: 'dump' ou 'backup' — apenas a extensao do arquivo muda,
        o formato interno e sempre o mesmo (pg_dump -Fc, custom format)

    Retorna o caminho completo do arquivo gerado.
    Lanca BackupError em caso de falha.
    """
    if extensao not in ('dump', 'backup'):
        extensao = 'dump'  # fallback seguro para valor invalido
    if not pasta_destino:
        raise BackupError("Pasta de destino do backup nao configurada.")

    os.makedirs(pasta_destino, exist_ok=True)

    # Verifica espaco em disco antes de tentar (evita backup corrompido
    # por falta de espaco no meio da operacao)
    try:
        livre = shutil.disk_usage(pasta_destino).free
        if livre < 100 * 1024 * 1024:  # menos de 100MB livre
            raise BackupError(
                f"Espaco em disco insuficiente na pasta de destino "
                f"({livre / 1024 / 1024:.0f}MB livres).")
    except OSError as e:
        raise BackupError(f"Nao foi possivel verificar espaco em disco: {e}")

    # Inclui milissegundos para evitar colisao de nome se dois backups
    # forem disparados no mesmo segundo (ex: clique duplo no botao manual
    # ou backup manual coincidindo com o automatico) — sem isso, o
    # segundo backup sobrescreveria o primeiro silenciosamente.
    timestamp   = datetime.now().strftime('%Y-%m-%d_%H-%M-%S_%f')[:-3]
    nome_arquivo = f"backup_{db_config['database']}_{timestamp}.{extensao}"
    caminho_completo = os.path.join(pasta_destino, nome_arquivo)

    pg_dump = localizar_pg_dump(pg_dump_path)

    comando = [
        pg_dump,
        '-h', db_config.get('host', 'localhost'),
        '-p', str(db_config.get('port', 5432)),
        '-U', db_config.get('user', 'postgres'),
        '-Fc',  # formato custom: compactado, restauravel com pg_restore
        '-f', caminho_completo,
        db_config['database'],
    ]

    # Senha via variavel de ambiente (PGPASSWORD), nunca em argv —
    # argumentos de processo podem ficar visiveis em listas de tarefas.
    env = os.environ.copy()
    if db_config.get('password'):
        env['PGPASSWORD'] = db_config['password']

    try:
        resultado = subprocess.run(
            comando, env=env, capture_output=True, text=True,
            timeout=600,  # 10 min: generoso para bancos pequenos/medios
        )
    except FileNotFoundError:
        raise BackupError(
            f"pg_dump nao encontrado em '{pg_dump}'. Configure o caminho "
            f"correto ou verifique a instalacao do PostgreSQL.")
    except subprocess.TimeoutExpired:
        raise BackupError("Backup excedeu o tempo limite de 10 minutos.")

    if resultado.returncode != 0:
        # Remove arquivo parcial/corrompido em caso de falha
        if os.path.exists(caminho_completo):
            try:
                os.remove(caminho_completo)
            except OSError:
                pass
        raise BackupError(f"pg_dump falhou: {resultado.stderr.strip()}")

    if not os.path.exists(caminho_completo) or os.path.getsize(caminho_completo) == 0:
        raise BackupError("Backup gerado esta vazio ou nao foi criado.")

    return caminho_completo


def aplicar_rotatividade(pasta_destino, manter_qtd, prefixo='backup_'):
    """
    Mantem apenas os 'manter_qtd' backups mais recentes na pasta,
    apagando os mais antigos. Ordena por data de modificacao do
    arquivo (nao pelo nome), para ser robusto a qualquer formato
    de timestamp usado no nome.

    Pega tanto .dump quanto .backup — o usuario pode ter trocado o
    formato configurado entre execucoes, e a rotatividade deve
    continuar enxergando backups antigos gerados com a extensao
    anterior.

    Retorna lista de arquivos removidos.
    """
    if manter_qtd <= 0:
        return []

    arquivos = []
    for ext in ('dump', 'backup'):
        arquivos.extend(glob.glob(os.path.join(pasta_destino, f'{prefixo}*.{ext}')))
    arquivos.sort(key=os.path.getmtime, reverse=True)  # mais recente primeiro

    removidos = []
    for arquivo in arquivos[manter_qtd:]:
        try:
            os.remove(arquivo)
            removidos.append(arquivo)
        except OSError:
            continue  # nao trava a rotina por um arquivo travado/sem permissao

    return removidos


def listar_backups(pasta_destino, prefixo='backup_'):
    """
    Retorna lista de dicts com info de cada backup existente na pasta,
    ordenados do mais recente para o mais antigo. Inclui tanto .dump
    quanto .backup, ja que o usuario pode trocar o formato configurado
    e backups antigos com a extensao anterior continuam validos.
    """
    if not pasta_destino or not os.path.isdir(pasta_destino):
        return []

    arquivos = []
    for ext in ('dump', 'backup'):
        arquivos.extend(glob.glob(os.path.join(pasta_destino, f'{prefixo}*.{ext}')))
    arquivos.sort(key=os.path.getmtime, reverse=True)

    resultado = []
    for caminho in arquivos:
        try:
            stat = os.stat(caminho)
            resultado.append({
                'nome':          os.path.basename(caminho),
                'caminho':       caminho,
                'tamanho_bytes': stat.st_size,
                'tamanho_mb':    round(stat.st_size / 1024 / 1024, 2),
                'criado_em':     datetime.fromtimestamp(stat.st_mtime),
            })
        except OSError:
            continue

    return resultado


def excluir_backup(caminho_arquivo, pasta_destino):
    """
    Exclui um backup especifico. Valida que o arquivo esta de fato
    dentro da pasta de destino configurada, para evitar que um caminho
    manipulado exclua arquivos arbitrarios do sistema.
    """
    caminho_abs = os.path.abspath(caminho_arquivo)
    pasta_abs   = os.path.abspath(pasta_destino)

    if not caminho_abs.startswith(pasta_abs):
        raise BackupError("Caminho de arquivo invalido (fora da pasta de backups).")

    if not os.path.exists(caminho_abs):
        raise BackupError("Arquivo de backup nao encontrado.")

    os.remove(caminho_abs)


def executar_backup_completo(db_config, pasta_destino, manter_qtd, pg_dump_path=None, extensao='dump'):
    """
    Funcao de alto nivel: faz o backup E aplica rotatividade em sequencia.
    E essa que o agendador e a rota manual devem chamar.

    Retorna dict com resultado da operacao para log/feedback ao usuario.
    """
    caminho = fazer_backup(db_config, pasta_destino, pg_dump_path, extensao)
    removidos = aplicar_rotatividade(pasta_destino, manter_qtd)
    tamanho_mb = round(os.path.getsize(caminho) / 1024 / 1024, 2)

    return {
        'sucesso':          True,
        'arquivo':          os.path.basename(caminho),
        'tamanho_mb':       tamanho_mb,
        'removidos_antigos': [os.path.basename(r) for r in removidos],
        'executado_em':     datetime.now(),
    }