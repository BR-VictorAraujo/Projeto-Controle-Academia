from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from datetime import timedelta


db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message = 'Faça login para acessar o sistema.'

@login_manager.user_loader
def load_user(user_id):
    return None

def create_app():
    app = Flask(__name__)
    from app.config import Config
    app.config.from_object(Config)
    db.init_app(app)
    login_manager.init_app(app)

    # Registra todos os blueprints
    from app.routes.auth       import bp as auth_bp
    from app.routes.alunos     import bp as alunos_bp
    from app.routes.acessos    import bp as acessos_bp
    from app.routes.financeiro import bp as financeiro_bp
    from app.routes.relatorios import bp as relatorios_bp
    from app.routes.config     import bp as config_bp
    from app.routes.pagamentos import bp as pagamentos_bp
    from app.routes.lgpd       import bp as lgpd_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(alunos_bp)
    app.register_blueprint(acessos_bp)
    app.register_blueprint(financeiro_bp)
    app.register_blueprint(relatorios_bp)
    app.register_blueprint(config_bp)
    app.register_blueprint(pagamentos_bp)
    app.register_blueprint(lgpd_bp)

    # Fotos e documentos de alunos ficam em app/static/, mas so podem ser
    # acessados logado, via /arquivos/... (ver app/routes/alunos.py). O
    # static e servido pelo proprio Flask (inclusive em producao), entao o
    # bloqueio do acesso direto e feito aqui, antes de qualquer rota.
    # Nao basta comparar request.path: o static do Flask normaliza o
    # caminho (ex.: /static/img/../fotos/x.jpg ou /static/./fotos/x.jpg
    # chegam no arquivo) e no Windows o disco nao diferencia maiusculas
    # (/static/FOTOS/x.jpg, /static/fotos./x.jpg, nome curto 8.3). Por
    # isso, alem do prefixo, o arquivo pedido e resolvido no disco
    # (realpath + normcase + lower — o bind mount do Docker Desktop
    # tambem nao diferencia maiusculas) e comparado com as pastas
    # protegidas.
    from flask import request, abort
    import os
    pastas_privadas = [
        os.path.normcase(os.path.realpath(os.path.join(app.static_folder, p))).lower()
        for p in ('fotos', 'documentos')
    ]
    @app.before_request
    def bloquear_static_privado():
        if request.path.lower().startswith(('/static/fotos/', '/static/documentos/')):
            abort(404)
        if request.endpoint == 'static':
            filename = (request.view_args or {}).get('filename', '')
            alvo = os.path.normcase(os.path.realpath(os.path.join(app.static_folder, filename))).lower()
            for pasta in pastas_privadas:
                if alvo == pasta or alvo.startswith(pasta + os.sep):
                    abort(404)

    from app.routes.alunos import url_arquivo
    app.add_template_filter(url_arquivo, 'arquivo_url')

    # Gate LGPD (v1.7.0): apos o login, exige o aceite da versao vigente do
    # termo (modal no dashboard). Sem efeito enquanto a migracao 1.7.0 nao
    # estiver aplicada ou sem versao publicada.
    # Endpoints liberados: app/lgpd.py (ENDPOINTS_LIBERADOS + dashboard GET).
    from app.lgpd import verificar_gate
    app.before_request(verificar_gate)

    @app.template_filter('local_time')
    def local_time_filter(dt):
        if dt is None:
            return '—'
        try:
            from app.models import Configuracao
            c = Configuracao.query.filter_by(chave='fuso_horario').first()
            fuso = int(c.valor) if c else -3
        except:
            fuso = -3
        return (dt + timedelta(hours=fuso)).strftime('%d/%m/%Y %H:%M')

    @app.template_filter('local_date')
    def local_date_filter(dt):
        if dt is None:
            return '—'
        try:
            from app.models import Configuracao
            c = Configuracao.query.filter_by(chave='fuso_horario').first()
            fuso = int(c.valor) if c else -3
        except:
            fuso = -3
        return (dt + timedelta(hours=fuso)).strftime('%d/%m/%Y')

    @app.context_processor
    def inject_config():
        from app.models import Configuracao
        from app.version import APP_NOME, APP_VERSAO, APP_BUILD
        from app.routes.auth import tecnologia_ativa

        class SiteConfig:
            nome_academia       = 'Academia'
            cor_principal       = '#FF6B00'
            cor_sidebar         = '#1a1a2e'
            logo_url            = ''
            telefone            = ''
            email               = ''
            endereco            = ''
            timeout_sessao      = '60'
            # Padrao 'ativado': popup de novas passagens visivel em
            # qualquer tela do sistema, nao so no Monitoramento.
            popup_acessos_ativo = '1'
        try:
            configs = {c.chave: c.valor for c in Configuracao.query.all()}
            SiteConfig.nome_academia       = configs.get('nome_academia',       'Academia')
            SiteConfig.cor_principal       = configs.get('cor_principal',       '#FF6B00')
            SiteConfig.cor_sidebar         = configs.get('cor_sidebar',         '#1a1a2e')
            SiteConfig.logo_url            = configs.get('logo_url',            '')
            SiteConfig.telefone            = configs.get('telefone',            '')
            SiteConfig.email               = configs.get('email',              '')
            SiteConfig.endereco            = configs.get('endereco',            '')
            SiteConfig.timeout_sessao      = configs.get('timeout_sessao',      '60')
            SiteConfig.popup_acessos_ativo = configs.get('popup_acessos_ativo', '1')
        except:
            pass

        # Tecnologias de acesso — fonte unica de verdade via
        # tecnologia_ativa() (app/routes/auth.py). Antes isso vivia
        # solto dentro do SiteConfig (tecnologia_biometria), o que
        # so controlava exibicao visual; nenhuma rota de fato respeitava
        # a configuracao. Templates agora usam {% if tecnologias.biometria %}
        # em vez de {% if config.tecnologia_biometria == '1' %}.
        try:
            tecnologias = {
                'biometria': tecnologia_ativa('biometria'),
                'rfid':      tecnologia_ativa('rfid'),
                'facial':    tecnologia_ativa('facial'),
            }
        except:
            # Fallback seguro: nao impede o sistema de subir, mas tambem
            # nao assume "tudo ativado" por padrao em caso de erro de banco.
            tecnologias = {'biometria': True, 'rfid': False, 'facial': False}

        # Versao do sistema — injetada em todas as templates sem precisar
        # passar manualmente em cada render_template().
        return dict(
            config=SiteConfig(),
            tecnologias=tecnologias,
            app_nome=APP_NOME,
            app_versao=APP_VERSAO,
            app_build=APP_BUILD,
        )

    # Inicia o agendador de backup automatico. Roda em thread de
    # background dentro do proprio processo Flask — nao depende de
    # Task Scheduler externo. A configuracao (ativo/horario/intervalo)
    # e lida do banco a cada execucao, entao mudancas feitas pelo
    # usuario em Parametros sao aplicadas sem reiniciar o servidor
    # (ver reconfigurar_agendador, chamado pela rota /parametros).
    from app import backup_scheduler
    with app.app_context():
        try:
            backup_scheduler.iniciar_agendador(app)
        except Exception as e:
            # Nao deixa uma falha no agendamento de backup impedir o
            # sistema de subir — registra e segue.
            app.logger.warning(f"Nao foi possivel iniciar o agendador de backup: {e}")

    return app