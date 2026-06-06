from app import create_app, db
from app.models import Usuario, Aluno, Biometria, RegistroAcesso, Configuracao, LogAuditoria, Plano
from werkzeug.security import generate_password_hash

app = create_app()

if __name__ == '__main__':
    with app.app_context():
        db.create_all()

        # Planos padrão
        planos_padrao = [
            ('Mensal',     30,  'Plano mensal'),
            ('Trimestral', 90,  'Plano trimestral'),
            ('Semestral',  180, 'Plano semestral'),
            ('Anual',      365, 'Plano anual'),
        ]
        for nome, dias, descricao in planos_padrao:
            if not Plano.query.filter_by(nome=nome).first():
                db.session.add(Plano(nome=nome, dias_validade=dias, descricao=descricao, valor=0))
        db.session.commit()

        # Configurações padrão
        configs_padrao = [
            ('nome_academia',  'Academia',  'Nome da academia'),
            ('cor_principal',  '#FF6B00',   'Cor principal do sistema'),
            ('cor_sidebar',    '#1a1a2e',   'Cor do menu lateral'),
            ('logo_url',       '',          'Caminho do logo'),
            ('endereco',       '',          'Endereço da academia'),
            ('telefone',       '',          'Telefone da academia'),
            ('email',          '',          'E-mail da academia'),
            ('timeout_sessao', '60',        'Timeout da sessão em minutos'),
        ]
        for chave, valor, descricao in configs_padrao:
            if not Configuracao.query.filter_by(chave=chave).first():
                db.session.add(Configuracao(chave=chave, valor=valor, descricao=descricao))

        # Usuário admin padrão
        if not Usuario.query.filter_by(usuario='admin').first():
            admin = Usuario(
                nome       = 'Administrador',
                usuario    = 'admin',
                senha_hash = generate_password_hash('admin123'),
                perfil     = 'admin',
                ativo      = True
            )
            db.session.add(admin)
            print("Usuário admin criado! Login: admin / Senha: admin123")

        db.session.commit()
        print("Banco de dados inicializado!")

    app.run(debug=True, host='0.0.0.0', port=5000)