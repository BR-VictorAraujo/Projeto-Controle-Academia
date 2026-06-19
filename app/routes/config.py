from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify
from app import db
from datetime import datetime
from app.routes.auth import login_required, registrar_log, get_param, validar_senha

bp = Blueprint('config', __name__)

@bp.route('/configuracoes', methods=['GET', 'POST'])
@login_required
def configuracoes():
    import os
    from app.models import Configuracao

    def set_config(chave, valor):
        c = Configuracao.query.filter_by(chave=chave).first()
        if c: c.valor = valor
        else: db.session.add(Configuracao(chave=chave, valor=valor))

    if request.method == 'POST':
        set_config('nome_academia',  request.form.get('nome_academia', 'Academia'))
        set_config('cor_principal',  request.form.get('cor_principal', '#FF6B00'))
        set_config('cor_sidebar',    request.form.get('cor_sidebar',   '#1a1a2e'))
        set_config('telefone',       request.form.get('telefone', ''))
        set_config('email',          request.form.get('email', ''))
        set_config('endereco',       request.form.get('endereco', ''))
        set_config('timeout_sessao', request.form.get('timeout_sessao', '60'))
        logo = request.files.get('logo')
        if logo and logo.filename:
            import uuid
            ext      = logo.filename.rsplit('.', 1)[-1].lower()
            filename = f'logo_{uuid.uuid4().hex[:8]}.{ext}'
            pasta    = os.path.join('app', 'static', 'img')
            os.makedirs(pasta, exist_ok=True)
            logo.save(os.path.join(pasta, filename))
            set_config('logo_url', f'/static/img/{filename}')
        db.session.commit()
        registrar_log('alterou configuracoes', 'Configurações do sistema atualizadas')
        flash('Configurações salvas com sucesso!', 'success')
        return redirect(url_for('config.configuracoes'))

    configs = {c.chave: c.valor for c in __import__('app.models', fromlist=['Configuracao']).Configuracao.query.all()}

    class Cfg:
        nome_academia  = configs.get('nome_academia',  'Academia')
        cor_principal  = configs.get('cor_principal',  '#FF6B00')
        cor_sidebar    = configs.get('cor_sidebar',    '#1a1a2e')
        logo_url       = configs.get('logo_url',       '')
        telefone       = configs.get('telefone',       '')
        email          = configs.get('email',          '')
        endereco       = configs.get('endereco',       '')
        timeout_sessao = configs.get('timeout_sessao', '60')

    return render_template('configuracoes.html', config=Cfg(), pagina_ativa='configuracoes')

@bp.route('/usuarios')
@login_required
def usuarios():
    from app.models import Usuario
    return render_template('usuarios.html', usuarios=Usuario.query.order_by(Usuario.nome).all())

@bp.route('/usuarios/novo', methods=['POST'])
@login_required
def novo_usuario():
    from app.models import Usuario
    from werkzeug.security import generate_password_hash
    nome            = request.form.get('nome')
    usuario_str     = request.form.get('usuario')
    senha           = request.form.get('senha')
    confirmar_senha = request.form.get('confirmar_senha')
    perfil          = request.form.get('perfil')

    if senha != confirmar_senha:
        flash('As senhas não coincidem.', 'danger')
        return redirect(url_for('config.usuarios'))
    valido, erro = validar_senha(senha)
    if not valido:
        flash(erro, 'danger')
        return redirect(url_for('config.usuarios'))
    if Usuario.query.filter_by(usuario=usuario_str).first():
        flash('Esse nome de usuário já existe.', 'danger')
        return redirect(url_for('config.usuarios'))

    db.session.add(Usuario(nome=nome, usuario=usuario_str,
                           senha_hash=generate_password_hash(senha),
                           perfil=perfil, ativo=True,
                           senha_alterada_em=datetime.utcnow()))
    db.session.commit()
    registrar_log('criou usuário', f'Usuário: {usuario_str} | Perfil: {perfil}')
    flash(f'Usuário {nome} criado com sucesso!', 'success')
    return redirect(url_for('config.usuarios'))

@bp.route('/usuarios/<int:id>/editar', methods=['POST'])
@login_required
def editar_usuario(id):
    from app.models import Usuario
    from werkzeug.security import generate_password_hash
    usuario     = Usuario.query.get_or_404(id)
    usuario_str = request.form.get('usuario')
    existente   = Usuario.query.filter_by(usuario=usuario_str).first()
    if existente and existente.id != id:
        flash('Esse nome de usuário já existe.', 'danger')
        return redirect(url_for('config.usuarios'))
    usuario.nome    = request.form.get('nome')
    usuario.usuario = usuario_str
    usuario.perfil  = request.form.get('perfil')
    usuario.ativo   = request.form.get('ativo') == '1'
    senha = request.form.get('senha')
    if senha:
        valido, erro = validar_senha(senha)
        if not valido:
            flash(erro, 'danger')
            return redirect(url_for('config.usuarios'))
        usuario.senha_hash        = generate_password_hash(senha)
        usuario.senha_alterada_em = datetime.utcnow()
    db.session.commit()
    registrar_log('editou usuário', f'ID: {id} | Usuário: {usuario_str}')
    flash(f'Usuário {usuario.nome} atualizado com sucesso!', 'success')
    return redirect(url_for('config.usuarios'))

@bp.route('/usuarios/<int:id>/toggle')
@login_required
def toggle_usuario(id):
    from app.models import Usuario
    usuario = Usuario.query.get_or_404(id)
    if usuario.usuario == 'admin':
        flash('O usuário admin não pode ser desativado.', 'danger')
        return redirect(url_for('config.usuarios'))
    usuario.ativo = not usuario.ativo
    db.session.commit()
    status = 'ativado' if usuario.ativo else 'desativado'
    registrar_log(f'{status} usuário', f'Usuário: {usuario.usuario}')
    flash(f'Usuário {usuario.nome} {status} com sucesso!', 'success')
    return redirect(url_for('config.usuarios'))

@bp.route('/parametros', methods=['GET', 'POST'])
@login_required
def parametros():
    from app.models import Configuracao

    def set_config(chave, valor):
        c = Configuracao.query.filter_by(chave=chave).first()
        if c: c.valor = valor
        else: db.session.add(Configuracao(chave=chave, valor=valor))

    if request.method == 'POST':
        for chave, padrao in [
            ('senha_tamanho_minimo','6'), ('senha_validade_dias','0'),
            ('senha_aviso_dias','7'), ('dias_alerta_vencimento','7'),
            ('timeout_sessao','60'), ('fuso_horario','-3'),
            # Backup do banco de dados
            ('backup_pasta_destino', ''), ('backup_modo', 'diario'),
            ('backup_horario', '03:00'), ('backup_intervalo_horas', '6'),
            ('backup_manter_qtd', '7'), ('backup_extensao', 'dump'),
        ]:
            set_config(chave, request.form.get(chave, padrao))
        for chave in ['senha_exigir_numero','senha_exigir_maiuscula','senha_exigir_especial',
                      'alertar_senha_vencimento','permitir_doc_duplicado',
                      'bloquear_plano_vencido','alertar_plano_vencimento',
                      'forma_dinheiro','forma_pix','forma_credito','forma_debito','forma_boleto',
                      'backup_automatico_ativo']:
            set_config(chave, '1' if request.form.get(chave) else '0')

        # popup_acessos_ativo usa um campo hidden + checkbox (ver template),
        # entao SEMPRE vem no POST com valor '1' ou '0' explicito — diferente
        # dos checkboxes acima que ficam ausentes quando desmarcados. Isso
        # permite o padrao ser "ativado" sem o primeiro salvamento de
        # parametros desativar o popup por causa da ausencia do campo.
        set_config('popup_acessos_ativo', request.form.get('popup_acessos_ativo', '1'))

        for chave in ['taxa_credito', 'taxa_debito']:
            set_config(chave, request.form.get(chave, '0'))
        db.session.commit()
        registrar_log('alterou parametros', 'Parâmetros do sistema atualizados')
        flash('Parâmetros salvos com sucesso!', 'success')

        # Reagenda o backup automatico imediatamente com a config nova,
        # sem precisar reiniciar o servidor.
        from app import backup_scheduler
        from flask import current_app
        backup_scheduler.reconfigurar_agendador(current_app._get_current_object())

        return redirect(url_for('config.parametros'))

    class Params:
        senha_tamanho_minimo    = get_param('senha_tamanho_minimo',    '6')
        senha_validade_dias     = get_param('senha_validade_dias',     '0')
        senha_aviso_dias        = get_param('senha_aviso_dias',        '7')
        senha_exigir_numero     = get_param('senha_exigir_numero',     '0')
        senha_exigir_maiuscula  = get_param('senha_exigir_maiuscula',  '0')
        senha_exigir_especial   = get_param('senha_exigir_especial',   '0')
        alertar_senha_vencimento= get_param('alertar_senha_vencimento','1')
        permitir_doc_duplicado  = get_param('permitir_doc_duplicado',  '0')
        bloquear_plano_vencido  = get_param('bloquear_plano_vencido',  '0')
        alertar_plano_vencimento= get_param('alertar_plano_vencimento','1')
        dias_alerta_vencimento  = get_param('dias_alerta_vencimento',  '7')
        timeout_sessao          = get_param('timeout_sessao',          '60')
        fuso_horario            = get_param('fuso_horario',            '-3')
        forma_dinheiro          = get_param('forma_dinheiro',          '1')
        forma_pix               = get_param('forma_pix',               '1')
        forma_credito           = get_param('forma_credito',           '0')
        forma_debito            = get_param('forma_debito',            '0')
        forma_boleto            = get_param('forma_boleto',            '0')
        taxa_credito            = get_param('taxa_credito',            '0')
        taxa_debito             = get_param('taxa_debito',             '0')
        # Backup do banco de dados
        backup_pasta_destino    = get_param('backup_pasta_destino',    '')
        backup_automatico_ativo = get_param('backup_automatico_ativo', '0')
        backup_modo             = get_param('backup_modo',             'diario')
        backup_horario          = get_param('backup_horario',          '03:00')
        backup_intervalo_horas  = get_param('backup_intervalo_horas',  '6')
        backup_manter_qtd       = get_param('backup_manter_qtd',       '7')
        backup_extensao         = get_param('backup_extensao',         'dump')
        # Popup de novas passagens — ativado por padrao (ver nota no POST acima)
        popup_acessos_ativo     = get_param('popup_acessos_ativo',     '1')

    from app.models import Plano
    planos_ativos = Plano.query.filter_by(ativo=True).order_by(Plano.nome).all()
    return render_template('parametros.html', params=Params(), planos_ativos=planos_ativos)

@bp.route('/planos')
@login_required
def planos():
    from app.models import Plano
    return render_template('planos.html', planos=Plano.query.order_by(Plano.id).all())

@bp.route('/planos/<int:id>/editar', methods=['POST'])
@login_required
def editar_plano(id):
    from app.models import Plano
    plano = Plano.query.get_or_404(id)
    plano.descricao     = request.form.get('descricao', '')
    plano.dias_validade = int(request.form.get('dias_validade', 30))
    plano.valor         = float(request.form.get('valor', 0))
    plano.ativo         = request.form.get('ativo') == '1'
    db.session.commit()
    registrar_log('editou plano', f'Plano: {plano.nome} | Valor: R$ {plano.valor}')
    flash(f'Plano {plano.nome} atualizado com sucesso!', 'success')
    return redirect(url_for('config.planos'))

@bp.route('/planos/novo', methods=['POST'])
@login_required
def novo_plano():
    from app.models import Plano
    nome          = request.form.get('nome', '').strip()
    descricao     = request.form.get('descricao', '').strip()
    dias_validade = int(request.form.get('dias_validade', 1))
    valor         = float(request.form.get('valor', 0))

    if not nome:
        flash('O nome do plano é obrigatório.', 'danger')
        return redirect(url_for('config.planos'))
    if Plano.query.filter_by(nome=nome).first():
        flash(f'Já existe um plano com o nome "{nome}".', 'danger')
        return redirect(url_for('config.planos'))

    db.session.add(Plano(nome=nome, descricao=descricao, dias_validade=dias_validade, valor=valor, ativo=True))
    db.session.commit()
    registrar_log('criou plano', f'Plano: {nome} | Dias: {dias_validade} | Valor: R$ {valor}')
    flash(f'Plano "{nome}" criado com sucesso!', 'success')
    return redirect(url_for('config.planos'))


# ─────────────────────────────────────────────────────────────────────────────
# Backup do banco de dados
# ─────────────────────────────────────────────────────────────────────────────

@bp.route('/backup/listar')
@login_required
def backup_listar():
    """Retorna a lista de backups existentes na pasta configurada, em JSON."""
    from app import backup as backup_mod

    pasta = get_param('backup_pasta_destino', '')
    if not pasta:
        return jsonify({'backups': [], 'erro': 'Pasta de destino não configurada.'})

    try:
        itens = backup_mod.listar_backups(pasta)
    except Exception as e:
        return jsonify({'backups': [], 'erro': str(e)}), 500

    # Serializa datetime para string ISO (JSON nao aceita datetime nativo)
    resultado = [
        {
            'nome':       b['nome'],
            'tamanho_mb': b['tamanho_mb'],
            'criado_em':  b['criado_em'].isoformat(),
        }
        for b in itens
    ]
    return jsonify({'backups': resultado})


@bp.route('/backup/executar', methods=['POST'])
@login_required
def backup_executar():
    """Dispara um backup manual imediatamente. Usado pelo botão 'Fazer backup agora'."""
    from app import backup as backup_mod
    from flask import current_app

    pasta        = get_param('backup_pasta_destino', '')
    manter_qtd   = int(get_param('backup_manter_qtd', '7') or 7)
    extensao     = get_param('backup_extensao', 'dump') or 'dump'
    pg_dump_path = get_param('backup_pg_dump_path', '') or None

    if not pasta:
        return jsonify({'sucesso': False, 'erro': 'Configure a pasta de destino antes de fazer backup.'}), 400

    try:
        db_config = backup_mod.extrair_db_config(current_app.config['SQLALCHEMY_DATABASE_URI'])
        resultado = backup_mod.executar_backup_completo(
            db_config, pasta, manter_qtd, pg_dump_path, extensao)
    except backup_mod.BackupError as e:
        registrar_log('falha ao gerar backup', str(e))
        return jsonify({'sucesso': False, 'erro': str(e)}), 500
    except Exception as e:
        registrar_log('falha ao gerar backup', f'Erro inesperado: {e}')
        return jsonify({'sucesso': False, 'erro': f'Erro inesperado: {e}'}), 500

    registrar_log('gerou backup manual', f"Arquivo: {resultado['arquivo']} ({resultado['tamanho_mb']}MB)")
    return jsonify({
        'sucesso':    True,
        'arquivo':    resultado['arquivo'],
        'tamanho_mb': resultado['tamanho_mb'],
    })


@bp.route('/backup/excluir', methods=['POST'])
@login_required
def backup_excluir():
    """Exclui um backup especifico pelo nome do arquivo (relativo a pasta configurada)."""
    from app import backup as backup_mod
    import os

    dados = request.get_json(silent=True) or {}
    nome  = dados.get('nome', '')

    if not nome:
        return jsonify({'sucesso': False, 'erro': 'Nome do arquivo não informado.'}), 400

    pasta = get_param('backup_pasta_destino', '')
    if not pasta:
        return jsonify({'sucesso': False, 'erro': 'Pasta de destino não configurada.'}), 400

    caminho_completo = os.path.join(pasta, nome)

    try:
        backup_mod.excluir_backup(caminho_completo, pasta)
    except backup_mod.BackupError as e:
        return jsonify({'sucesso': False, 'erro': str(e)}), 400

    registrar_log('excluiu backup', f'Arquivo: {nome}')
    return jsonify({'sucesso': True})