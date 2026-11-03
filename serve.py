# -*- coding: utf-8 -*-
"""
serve.py — Entrypoint de producao do GymFlow Web (waitress) e servico Windows.

Uso (sempre com o Python do venv, pela pasta do sistema):
  python serve.py                      roda em primeiro plano (console), para teste
  python serve.py --instalar-servico   registra (ou atualiza) o servico "GymFlow"
  python serve.py --iniciar-servico    inicia o servico e espera ficar em execucao
  python serve.py --parar-servico      para o servico (ok se ja estiver parado)
  python serve.py --remover-servico    para e remove o servico
  (--servico e usado so pelo Windows ao iniciar o servico)

Servico (pywin32, sem NSSM):
  - O servico roda o python.exe BASE do venv (o venv\\Scripts\\python.exe e so
    um lancador que abre outro processo, e o Windows nao conseguiria falar com
    ele). Ao iniciar, este arquivo poe o site-packages do venv na frente do
    sys.path e tira o site-packages global, entao valem as versoes do venv.
    O Python base precisa continuar instalado no mesmo caminho.
  - Conta LocalSystem, inicio automatico, reinicio em falha (sc failure).
  - Log em logs\\servico.log (rotacao 10 MB x 5).
  - No Windows, SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    impede a suspensao/hibernacao por inatividade enquanto o servico roda.
    Nao altera o powercfg. Conferir com `powercfg /requests` (secao SYSTEM).

Variaveis opcionais no .env: GYMFLOW_HOST (0.0.0.0), GYMFLOW_PORT (5000),
GYMFLOW_THREADS (8).

Codigos de saida dos comandos: 0 = ok; 1 = erro (o motivo vai para a saida e,
se GYMFLOW_RESULT_FILE estiver definida, para esse arquivo como DETALHE=...).
"""

import os
import sys

PASTA_SISTEMA = os.path.dirname(os.path.abspath(__file__))
PASTA_VENV = os.path.join(PASTA_SISTEMA, 'venv')


def _usar_site_packages_do_venv():
    """Quando iniciado pelo python.exe base (servico), usa os pacotes do venv."""
    site_venv = os.path.join(PASTA_VENV, 'Lib', 'site-packages')
    if not os.path.isdir(site_venv):
        return
    if os.path.normcase(os.path.abspath(sys.prefix)) == os.path.normcase(PASTA_VENV):
        return  # ja e o venv
    import site
    # No Windows, getsitepackages() inclui o proprio sys.prefix: fica so o que e site-packages
    globais = [os.path.normcase(p) for p in site.getsitepackages() + [site.getusersitepackages()]
               if 'site-packages' in p.lower()]

    def global_(p):  # site-packages global ou pasta que um .pth dele incluiu (ex.: ...\win32)
        p = os.path.normcase(os.path.abspath(p))
        return any(p == g or p.startswith(g + os.sep) for g in globais)

    antes = [p for p in sys.path if not global_(p)]
    site.addsitedir(site_venv)  # processa os .pth do venv (ex.: pywin32)
    novos = [p for p in sys.path if p not in antes and not global_(p)]
    sys.path[:] = novos + antes


_usar_site_packages_do_venv()
os.chdir(PASTA_SISTEMA)

import ctypes      # noqa: E402
import logging     # noqa: E402
import logging.handlers  # noqa: E402
import subprocess  # noqa: E402
import threading   # noqa: E402

NOME_SERVICO = 'GymFlow'
NOME_EXIBICAO = 'GymFlow Web'
DESCRICAO = 'GymFlow - sistema web da academia (waitress, porta 5000).'

log = logging.getLogger('gymflow.serve')

# Flags da API SetThreadExecutionState (winbase.h)
ES_CONTINUOUS      = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


# ── Log ───────────────────────────────────────────────────────────────────────

class _SaidaParaLog:
    """stdout/stderr do servico (que nao tem console) vao para o log."""
    def __init__(self, nivel):
        self.nivel = nivel

    def write(self, texto):
        for linha in texto.rstrip().splitlines():
            if linha.strip():
                logging.getLogger('gymflow.saida').log(self.nivel, linha)

    def flush(self):
        pass


def configurar_log(no_arquivo):
    raiz = logging.getLogger()
    raiz.setLevel(logging.INFO)
    formato = logging.Formatter('%(asctime)s %(levelname)s [%(name)s] %(message)s')
    if no_arquivo:
        pasta_logs = os.path.join(PASTA_SISTEMA, 'logs')
        os.makedirs(pasta_logs, exist_ok=True)
        h = logging.handlers.RotatingFileHandler(os.path.join(pasta_logs, 'servico.log'),
                                                 maxBytes=10 * 1024 * 1024, backupCount=5,
                                                 encoding='utf-8')
        sys.stdout = _SaidaParaLog(logging.INFO)
        sys.stderr = _SaidaParaLog(logging.ERROR)
    else:
        h = logging.StreamHandler(sys.stdout)
    h.setFormatter(formato)
    raiz.addHandler(h)


# ── Aplicacao ─────────────────────────────────────────────────────────────────

def impedir_suspensao():
    """Impede a suspensao/hibernacao por inatividade enquanto o processo roda.

    ES_CONTINUOUS vale ate o fim da thread que chamou: por isso e chamado na
    thread que vive enquanto o servico roda. Nao bloqueia a suspensao pedida
    pelo usuario (menu Iniciar, botao, tampa).
    """
    if sys.platform != 'win32':
        log.info('Fora do Windows: controle de suspensao ignorado.')
        return
    try:
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.SetThreadExecutionState.restype = ctypes.c_uint32
        kernel32.SetThreadExecutionState.argtypes = [ctypes.c_uint32]
        anterior = kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        if anterior == 0:
            log.warning('SetThreadExecutionState falhou (erro %s): a maquina pode suspender.',
                        ctypes.get_last_error())
        else:
            log.info('Suspensao/hibernacao por inatividade bloqueada enquanto o servico roda.')
    except Exception as e:
        log.warning(f'Nao foi possivel bloquear a suspensao: {e}')


def rodar_waitress():
    """Inicializa o banco e serve a aplicacao (bloqueia)."""
    from waitress import serve
    from app.version import APP_NOME, APP_VERSAO
    from run import app, inicializar_banco

    host    = os.getenv('GYMFLOW_HOST', '0.0.0.0')
    porta   = int(os.getenv('GYMFLOW_PORT', '5000'))
    threads = int(os.getenv('GYMFLOW_THREADS', '8'))

    log.info(f'Iniciando {APP_NOME} {APP_VERSAO} (waitress) em {host}:{porta}, {threads} threads '
             f'| Python {sys.version.split()[0]} em {sys.executable}')
    inicializar_banco()
    serve(app, host=host, port=porta, threads=threads)


# ── Servico Windows (pywin32) ─────────────────────────────────────────────────

def _classe_servico():
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil

    class GymFlowServico(win32serviceutil.ServiceFramework):
        _svc_name_ = NOME_SERVICO
        _svc_display_name_ = NOME_EXIBICAO
        _svc_description_ = DESCRICAO

        def __init__(self, args):
            super().__init__(args)
            self.evento_parar = win32event.CreateEvent(None, 0, 0, None)

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            win32event.SetEvent(self.evento_parar)

        def SvcDoRun(self):
            servicemanager.LogInfoMsg(f'{NOME_EXIBICAO}: iniciando')
            # Esta thread vive ate o servico parar: o pedido de "nao suspender" vale ate la
            impedir_suspensao()

            def alvo():
                try:
                    rodar_waitress()
                except Exception:
                    log.exception('Falha no servidor web')

            servidor = threading.Thread(target=alvo, name='waitress', daemon=True)
            servidor.start()
            while win32event.WaitForSingleObject(self.evento_parar, 5000) == win32event.WAIT_TIMEOUT:
                if not servidor.is_alive():
                    # Sai sem avisar "parado": o Windows trata como falha e reinicia (sc failure)
                    log.error('O servidor web parou. Encerrando para o Windows reiniciar o servico.')
                    logging.shutdown()
                    os._exit(1)
            log.info('Servico parado.')
            # Threads daemon (waitress, agendador) terminam com o processo

    return GymFlowServico


def _python_base():
    """python.exe real (fora do venv): e ele que o servico executa."""
    base = getattr(sys, '_base_executable', '') or sys.executable
    if os.path.basename(base).lower() == 'pythonw.exe':
        base = os.path.join(os.path.dirname(base), 'python.exe')
    return base


def instalar_servico():
    import pywintypes
    import win32service

    comando = f'"{_python_base()}" -S "{os.path.abspath(__file__)}" --servico'
    scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_ALL_ACCESS)
    try:
        try:
            hs = win32service.OpenService(scm, NOME_SERVICO, win32service.SERVICE_ALL_ACCESS)
            # Ja existe (ex.: servico antigo do NSSM com o mesmo nome): troca o executavel
            win32service.ChangeServiceConfig(
                hs, win32service.SERVICE_WIN32_OWN_PROCESS, win32service.SERVICE_AUTO_START,
                win32service.SERVICE_ERROR_NORMAL, comando, None, 0, None, 'LocalSystem', '',
                NOME_EXIBICAO)
            print(f'Servico {NOME_SERVICO} atualizado.')
        except pywintypes.error as e:
            if e.winerror != 1060:  # ERROR_SERVICE_DOES_NOT_EXIST
                raise
            hs = win32service.CreateService(
                scm, NOME_SERVICO, NOME_EXIBICAO, win32service.SERVICE_ALL_ACCESS,
                win32service.SERVICE_WIN32_OWN_PROCESS, win32service.SERVICE_AUTO_START,
                win32service.SERVICE_ERROR_NORMAL, comando, None, 0, None, None, None)
            print(f'Servico {NOME_SERVICO} criado.')
        win32service.ChangeServiceConfig2(hs, win32service.SERVICE_CONFIG_DESCRIPTION, DESCRICAO)
        win32service.CloseServiceHandle(hs)
    finally:
        win32service.CloseServiceHandle(scm)

    # Reinicio em falha (inclusive se o processo for morto) e tambem em saida com erro
    sc = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'sc.exe')
    subprocess.run([sc, 'failure', NOME_SERVICO, 'reset=', '86400',
                    'actions=', 'restart/5000/restart/5000/restart/30000'], check=True,
                   capture_output=True)
    subprocess.run([sc, 'failureflag', NOME_SERVICO, '1'], check=True, capture_output=True)
    print(f'Comando: {comando}')


def iniciar_servico():
    import win32service
    import win32serviceutil
    status = win32serviceutil.QueryServiceStatus(NOME_SERVICO)[1]
    if status != win32service.SERVICE_RUNNING:
        win32serviceutil.StartService(NOME_SERVICO)
        win32serviceutil.WaitForServiceStatus(NOME_SERVICO, win32service.SERVICE_RUNNING, 60)
    print(f'Servico {NOME_SERVICO} em execucao.')


def parar_servico():
    import pywintypes
    import win32service
    import win32serviceutil
    try:
        status = win32serviceutil.QueryServiceStatus(NOME_SERVICO)[1]
    except pywintypes.error as e:
        if e.winerror == 1060:
            print(f'Servico {NOME_SERVICO} nao existe.')
            return
        raise
    if status != win32service.SERVICE_STOPPED:
        if status != win32service.SERVICE_STOP_PENDING:
            win32serviceutil.StopService(NOME_SERVICO)
        win32serviceutil.WaitForServiceStatus(NOME_SERVICO, win32service.SERVICE_STOPPED, 60)
    print(f'Servico {NOME_SERVICO} parado.')


def remover_servico():
    import pywintypes
    import win32serviceutil
    parar_servico()
    try:
        win32serviceutil.RemoveService(NOME_SERVICO)
        print(f'Servico {NOME_SERVICO} removido.')
    except pywintypes.error as e:
        if e.winerror != 1060:
            raise


def _registrar_detalhe(texto):
    arquivo = os.environ.get('GYMFLOW_RESULT_FILE')
    if arquivo:
        with open(arquivo, 'w', encoding='utf-8') as f:
            f.write('DETALHE=' + texto.replace('\n', ' ') + '\n')


COMANDOS = {
    '--instalar-servico': instalar_servico,
    '--iniciar-servico':  iniciar_servico,
    '--parar-servico':    parar_servico,
    '--remover-servico':  remover_servico,
}


def main():
    args = sys.argv[1:]

    if args[:1] == ['--servico']:
        # Iniciado pelo Windows (Gerenciador de Servicos)
        configurar_log(no_arquivo=True)
        import servicemanager
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(_classe_servico())
        servicemanager.StartServiceCtrlDispatcher()
        return

    if args and args[0] in COMANDOS:
        try:
            COMANDOS[args[0]]()
        except Exception as e:
            detalhe = f'{type(e).__name__}: {e}'
            print(f'ERRO: {detalhe}')
            _registrar_detalhe(detalhe)
            sys.exit(1)
        return

    if args:
        print(__doc__)
        sys.exit(2)

    # Primeiro plano (console)
    configurar_log(no_arquivo=False)
    impedir_suspensao()
    rodar_waitress()


if __name__ == '__main__':
    main()
