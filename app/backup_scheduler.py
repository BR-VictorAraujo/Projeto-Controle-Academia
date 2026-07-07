# -*- coding: utf-8 -*-
"""
backup_scheduler.py — Agendamento de backup automatico dentro do Flask.

Usa APScheduler (BackgroundScheduler) para rodar o backup periodicamente
em uma thread separada, sem bloquear as requisicoes web.

Le a configuracao do banco (Configuracao) a cada execucao — assim, se o
usuario mudar o horario/intervalo/pasta nas Configuracoes, a proxima
execucao ja usa os valores novos, sem precisar reiniciar o servidor.
"""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
import logging

logger = logging.getLogger('backup_scheduler')

JOB_ID_BACKUP     = 'backup_automatico'
JOB_ID_INATIVACAO = 'inativacao_automatica'

_scheduler = None  # singleton — um unico scheduler por processo Flask


def _executar_job_backup(app):
    with app.app_context():
        from app.models import Configuracao
        from app import backup as backup_mod

        def get_param(chave, padrao=''):
            c = Configuracao.query.filter_by(chave=chave).first()
            return c.valor if c else padrao

        pasta_destino = get_param('backup_pasta_destino', '')
        if not pasta_destino:
            logger.warning('Backup automatico ignorado: pasta de destino nao configurada.')
            return

        manter_qtd   = int(get_param('backup_manter_qtd', '7') or 7)
        pg_dump_path = get_param('backup_pg_dump_path', '') or None
        extensao     = get_param('backup_extensao', 'dump') or 'dump'

        db_config = backup_mod.extrair_db_config(app.config['SQLALCHEMY_DATABASE_URI'])

        try:
            resultado = backup_mod.executar_backup_completo(
                db_config, pasta_destino, manter_qtd, pg_dump_path, extensao)
            logger.info(
                f"Backup automatico OK: {resultado['arquivo']} "
                f"({resultado['tamanho_mb']}MB)")
        except backup_mod.BackupError as e:
            logger.error(f"Backup automatico FALHOU: {e}")


def _executar_job_inativacao(app):
    """
    Job diário de inativação automática por inatividade.
    Roda em thread separada via APScheduler — requer app_context.
    Só executa se semanas_inatividade > 0 nas configurações.
    """
    with app.app_context():
        from app.routes.financeiro import inativar_alunos_inativos
        inativados = inativar_alunos_inativos()
        if inativados:
            logger.info(f'Inativacao automatica: {inativados} aluno(s) inativado(s).')
        else:
            logger.debug('Inativacao automatica: nenhum aluno inativado.')


def _construir_trigger(modo, horario_str, intervalo_horas):
    if modo == 'intervalo':
        try:
            horas = max(1, int(intervalo_horas or 6))
        except (ValueError, TypeError):
            horas = 6
        return IntervalTrigger(hours=horas)

    try:
        hora, minuto = map(int, (horario_str or '03:00').split(':'))
        if not (0 <= hora <= 23 and 0 <= minuto <= 59):
            raise ValueError("Hora ou minuto fora do range valido")
    except (ValueError, AttributeError):
        hora, minuto = 3, 0
    return CronTrigger(hour=hora, minute=minuto)


def iniciar_agendador(app):
    """
    Inicia (ou reconfigura) o agendador de backup automatico e inativacao.
    Deve ser chamado uma vez na criacao do app (create_app), e tambem
    pode ser chamado de novo apos o usuario salvar novas configuracoes,
    para o agendamento ja refletir a mudanca sem reiniciar.
    """
    global _scheduler

    with app.app_context():
        from app.models import Configuracao
        from datetime import date

        def get_param(chave, padrao=''):
            c = Configuracao.query.filter_by(chave=chave).first()
            return c.valor if c else padrao

        ativado   = get_param('backup_automatico_ativo', '0') == '1'
        modo      = get_param('backup_modo',             'diario')
        horario   = get_param('backup_horario',          '03:00')
        intervalo = get_param('backup_intervalo_horas',  '6')
        ultima_inativacao = get_param('inativacao_ultima_execucao', '')
        hoje_str  = date.today().isoformat()

    if _scheduler is None:
        _scheduler = BackgroundScheduler(daemon=True)
        _scheduler.start()

    # ── Backup ────────────────────────────────────────────────────────────────
    if _scheduler.get_job(JOB_ID_BACKUP):
        _scheduler.remove_job(JOB_ID_BACKUP)

    if ativado:
        trigger = _construir_trigger(modo, horario, intervalo)
        _scheduler.add_job(
            _executar_job_backup, trigger=trigger, id=JOB_ID_BACKUP,
            args=[app], replace_existing=True,
            misfire_grace_time=3600,
        )
        logger.info(f"Backup automatico agendado: modo={modo} horario={horario} intervalo={intervalo}h")
    else:
        logger.info('Backup automatico desativado nas configuracoes.')

    # ── Inativação automática — roda diariamente às 04:00 ────────────────────
    if _scheduler.get_job(JOB_ID_INATIVACAO):
        _scheduler.remove_job(JOB_ID_INATIVACAO)

    _scheduler.add_job(
        _executar_job_inativacao,
        trigger=CronTrigger(hour=4, minute=0),
        id=JOB_ID_INATIVACAO,
        args=[app],
        replace_existing=True,
        misfire_grace_time=3600,
    )
    logger.info('Inativacao automatica agendada: diariamente as 04:00.')

    # ── Recuperação de execução perdida ───────────────────────────────────────
    # Se o servidor estava offline às 04:00 ou a rotina não rodou hoje por
    # qualquer motivo, dispara imediatamente no startup para cobrir o gap.
    if ultima_inativacao != hoje_str:
        logger.info('Inativacao atrasada detectada — executando agora no startup.')
        _executar_job_inativacao(app)


def reconfigurar_agendador(app):
    """Alias explicito para reagendar apos mudanca de configuracao pelo usuario."""
    iniciar_agendador(app)


def proxima_execucao():
    """Retorna o datetime da proxima execucao agendada, ou None se nao houver job ativo."""
    if _scheduler is None:
        return None
    job = _scheduler.get_job(JOB_ID_BACKUP)
    return job.next_run_time if job else None