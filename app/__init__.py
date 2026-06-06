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
    from app.routes import auth
    app.register_blueprint(auth)

    # Filtros de fuso horário
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

    # Injeta configurações globais em todos os templates
    @app.context_processor
    def inject_config():
        from app.models import Configuracao
        class SiteConfig:
            nome_academia  = 'Academia'
            cor_principal  = '#FF6B00'
            cor_sidebar    = '#1a1a2e'
            logo_url       = ''
            telefone       = ''
            email          = ''
            endereco       = ''
            timeout_sessao = '60'
        try:
            configs = {c.chave: c.valor for c in Configuracao.query.all()}
            SiteConfig.nome_academia  = configs.get('nome_academia',  'Academia')
            SiteConfig.cor_principal  = configs.get('cor_principal',  '#FF6B00')
            SiteConfig.cor_sidebar    = configs.get('cor_sidebar',    '#1a1a2e')
            SiteConfig.logo_url       = configs.get('logo_url',       '')
            SiteConfig.telefone       = configs.get('telefone',       '')
            SiteConfig.email          = configs.get('email',          '')
            SiteConfig.endereco       = configs.get('endereco',       '')
            SiteConfig.timeout_sessao = configs.get('timeout_sessao', '60')
        except:
            pass
        return dict(config=SiteConfig())

    return app