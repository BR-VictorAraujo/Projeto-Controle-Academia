from flask import Blueprint, render_template, redirect, url_for, request, flash, session
from functools import wraps
from app import db
from app.models import Aluno, LogAuditoria, RegistroAcesso
from datetime import datetime, date, timedelta
import re
import json

def utc_para_local(dt):
    if dt is None:
        return None
    return dt - timedelta(hours=3)

auth = Blueprint('auth', __name__)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logado'):
            flash('Faça login para acessar o sistema.', 'warning')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function

def mobile_readonly(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user_agent = request.headers.get('User-Agent', '').lower()
        is_mobile  = any(x in user_agent for x in ['mobile', 'android', 'iphone', 'ipad', 'tablet'])
        if is_mobile:
            rotas_mobile = ['auth.dashboard', 'auth.relatorios', 'auth.login', 'auth.logout', 'auth.exportar_csv', 'auth.exportar_pdf']
            if request.endpoint not in rotas_mobile:
                flash('Esta funcionalidade não está disponível na versão mobile.', 'warning')
                return redirect(url_for('auth.dashboard'))
        return f(*args, **kwargs)
    return decorated_function

def registrar_log(acao, detalhes=None):
    log = LogAuditoria(
        usuario=session.get('usuario', 'sistema'),
        acao=acao,
        detalhes=detalhes,
        ip=request.remote_addr
    )
    db.session.add(log)
    db.session.commit()

def validar_documento(tipo, numero):
    numero_limpo = re.sub(r'[^a-zA-Z0-9]', '', numero)
    if tipo == 'cpf':
        if not re.match(r'^\d{11}$', numero_limpo):
            return False, 'CPF inválido — deve conter 11 dígitos.'
        if len(set(numero_limpo)) == 1:
            return False, 'CPF inválido.'
        def calcular_digito(cpf, peso):
            soma = sum(int(cpf[i]) * (peso - i) for i in range(peso - 1))
            resto = (soma * 10) % 11
            return 0 if resto >= 10 else resto
        if int(numero_limpo[9]) != calcular_digito(numero_limpo, 10):
            return False, 'CPF inválido.'
        if int(numero_limpo[10]) != calcular_digito(numero_limpo, 11):
            return False, 'CPF inválido.'
        return True, ''
    elif tipo == 'rg':
        if len(numero_limpo) < 5:
            return False, 'RG inválido — deve conter pelo menos 5 caracteres.'
        return True, ''
    elif tipo == 'passaporte':
        if len(numero_limpo) < 6:
            return False, 'Passaporte inválido — deve conter pelo menos 6 caracteres.'
        return True, ''
    return False, 'Tipo de documento inválido.'

def get_param(chave, padrao=''):
    from app.models import Configuracao
    c = Configuracao.query.filter_by(chave=chave).first()
    return c.valor if c else padrao

def validar_senha(senha):
    tamanho_min     = int(get_param('senha_tamanho_minimo', '6'))
    exigir_numero   = get_param('senha_exigir_numero',     '0') == '1'
    exigir_maiusc   = get_param('senha_exigir_maiuscula',  '0') == '1'
    exigir_especial = get_param('senha_exigir_especial',   '0') == '1'
    if len(senha) < tamanho_min:
        return False, f'A senha deve ter pelo menos {tamanho_min} caracteres.'
    if exigir_numero and not any(c.isdigit() for c in senha):
        return False, 'A senha deve conter pelo menos um número.'
    if exigir_maiusc and not any(c.isupper() for c in senha):
        return False, 'A senha deve conter pelo menos uma letra maiúscula.'
    if exigir_especial and not any(c in '!@#$%^&*()_+-=[]{}|;:,.<>?' for c in senha):
        return False, 'A senha deve conter pelo menos um caractere especial (!@#$%).'
    return True, ''

@auth.route('/')
def index():
    return redirect(url_for('auth.login'))

@auth.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('logado'):
        return redirect(url_for('auth.dashboard'))
    if request.method == 'POST':
        from werkzeug.security import check_password_hash
        from app.models import Usuario
        usuario_str = request.form.get('usuario')
        senha       = request.form.get('senha')
        usuario     = Usuario.query.filter_by(usuario=usuario_str, ativo=True).first()
        if usuario and check_password_hash(usuario.senha_hash, senha):
            usuario.ultimo_acesso = datetime.utcnow()
            db.session.commit()
            session['logado']     = True
            session['usuario']    = usuario.nome
            session['perfil']     = usuario.perfil
            session['usuario_id'] = usuario.id
            session.permanent     = True
            registrar_log('login', f'Usuário: {usuario_str}')
            return redirect(url_for('auth.dashboard'))
        else:
            flash('Usuário ou senha incorretos.', 'danger')
    return render_template('auth/login.html')

@auth.route('/dashboard')
@login_required
def dashboard():
    hoje        = datetime.utcnow().date()
    dias_alerta = int(get_param('dias_alerta_vencimento', '7'))
    alerta_dias = hoje + timedelta(days=dias_alerta)

    total_alunos  = Aluno.query.count()
    alunos_ativos = Aluno.query.filter_by(ativo=True).count()
    vencendo      = Aluno.query.filter(Aluno.ativo == True, Aluno.vencimento <= alerta_dias).count()
    acessos_hoje  = RegistroAcesso.query.filter(db.func.date(RegistroAcesso.entrada_em) == hoje).count()
    ultimos       = Aluno.query.order_by(Aluno.criado_em.desc()).limit(5).all()

    alertar_plano      = get_param('alertar_plano_vencimento', '1') == '1'
    alertas_vencimento = Aluno.query.filter(
        Aluno.ativo == True, Aluno.vencimento <= alerta_dias
    ).order_by(Aluno.vencimento).all() if alertar_plano else []

    alerta_senha  = None
    alertar_senha = get_param('alertar_senha_vencimento', '1') == '1'
    validade_dias = int(get_param('senha_validade_dias', '0'))
    aviso_dias    = int(get_param('senha_aviso_dias', '7'))
    if alertar_senha and validade_dias > 0 and session.get('usuario_id'):
        from app.models import Usuario
        usuario = Usuario.query.get(session.get('usuario_id'))
        if usuario and usuario.senha_alterada_em:
            expira_em      = usuario.senha_alterada_em.date() + timedelta(days=validade_dias)
            dias_restantes = (expira_em - hoje).days
            if dias_restantes <= aviso_dias:
                alerta_senha = dias_restantes

    return render_template('dashboard/index.html',
                           total_alunos=total_alunos, alunos_ativos=alunos_ativos,
                           vencendo=vencendo, acessos_hoje=acessos_hoje,
                           ultimos=ultimos, alertas_vencimento=alertas_vencimento,
                           alerta_senha=alerta_senha, dias_alerta=dias_alerta)

@auth.route('/alunos')
@login_required
def alunos():
    lista = Aluno.query.order_by(Aluno.nome).all()
    return render_template('alunos/lista.html', alunos=lista)

@auth.route('/alunos/novo', methods=['GET', 'POST'])
@login_required
def novo_aluno():
    from app.models import Plano
    planos = Plano.query.filter_by(ativo=True).order_by(Plano.nome).all()

    if request.method == 'POST':
        nome           = request.form.get('nome')
        tipo_documento = request.form.get('tipo_documento')
        documento      = request.form.get('documento')
        telefone       = request.form.get('telefone')
        email          = request.form.get('email')
        plano          = request.form.get('plano')
        ativo          = request.form.get('ativo') == '1'
        data_nasc_str  = request.form.get('data_nascimento')
        vencimento_str = request.form.get('vencimento')

        data_nascimento = datetime.strptime(data_nasc_str, '%Y-%m-%d').date() if data_nasc_str else None
        vencimento      = datetime.strptime(vencimento_str, '%Y-%m-%d').date() if vencimento_str else None

        valido, erro = validar_documento(tipo_documento, documento)
        if not valido:
            flash(erro, 'danger')
            return render_template('alunos/novo.html', planos=planos)

        permitir_dup = get_param('permitir_doc_duplicado', '0') == '1'
        if not permitir_dup and Aluno.query.filter_by(documento=documento, tipo_documento=tipo_documento).first():
            flash(f'{tipo_documento.upper()} já cadastrado no sistema.', 'danger')
            return render_template('alunos/novo.html', planos=planos)

        foto_path   = None
        foto_base64 = request.form.get('foto_base64')
        if foto_base64 and foto_base64.startswith('data:image'):
            import uuid, base64, os
            pasta    = os.path.join('app', 'static', 'fotos')
            os.makedirs(pasta, exist_ok=True)
            img_data = foto_base64.split(',')[1]
            filename = f'aluno_{uuid.uuid4().hex[:8]}.jpg'
            filepath = os.path.join(pasta, filename)
            with open(filepath, 'wb') as f:
                f.write(base64.b64decode(img_data))
            foto_path = f'/static/fotos/{filename}'

        aluno = Aluno(nome=nome, tipo_documento=tipo_documento, documento=documento,
                      telefone=telefone, email=email, plano=plano,
                      vencimento=vencimento, data_nascimento=data_nascimento,
                      ativo=ativo, foto=foto_path)
        db.session.add(aluno)
        db.session.commit()

        from app.models import DocumentoAluno
        import os, uuid
        for tipo_doc, arquivo in zip(request.form.getlist('doc_tipo[]'), request.files.getlist('doc_arquivo[]')):
            if arquivo and arquivo.filename and tipo_doc:
                ext = arquivo.filename.rsplit('.', 1)[-1].lower()
                if ext in ['pdf', 'jpg', 'jpeg', 'png']:
                    pasta    = os.path.join('app', 'static', 'documentos', str(aluno.id))
                    os.makedirs(pasta, exist_ok=True)
                    filename = f'{uuid.uuid4().hex[:8]}_{arquivo.filename}'
                    arquivo.save(os.path.join(pasta, filename))
                    db.session.add(DocumentoAluno(aluno_id=aluno.id, tipo=tipo_doc,
                        nome_arquivo=arquivo.filename,
                        caminho=f'/static/documentos/{aluno.id}/{filename}'))
        db.session.commit()

        registrar_log('cadastrou aluno', f'Nome: {nome} | {tipo_documento.upper()}: {documento}')
        flash('Aluno cadastrado com sucesso!', 'success')
        return redirect(url_for('auth.alunos'))

    return render_template('alunos/novo.html', planos=planos)

@auth.route('/alunos/<int:id>')
@login_required
def detalhes_aluno(id):
    from app.models import DocumentoAluno
    aluno      = Aluno.query.get_or_404(id)
    acessos    = RegistroAcesso.query.filter_by(aluno_id=id).order_by(RegistroAcesso.entrada_em.desc()).limit(20).all()
    documentos = DocumentoAluno.query.filter_by(aluno_id=id).order_by(DocumentoAluno.criado_em.desc()).all()
    return render_template('alunos/detalhes.html', aluno=aluno, acessos=acessos, documentos=documentos)

@auth.route('/alunos/<int:id>/editar', methods=['GET', 'POST'])
@login_required
def editar_aluno(id):
    from app.models import Plano, DocumentoAluno
    aluno      = Aluno.query.get_or_404(id)
    planos     = Plano.query.filter_by(ativo=True).order_by(Plano.nome).all()
    documentos = DocumentoAluno.query.filter_by(aluno_id=id).order_by(DocumentoAluno.criado_em.desc()).all()

    if request.method == 'POST':
        tipo_doc       = request.form.get('tipo_documento')
        novo_documento = request.form.get('documento')

        valido, erro = validar_documento(tipo_doc, novo_documento)
        if not valido:
            flash(erro, 'danger')
            return render_template('alunos/editar.html', aluno=aluno, planos=planos, documentos=documentos)

        permitir_dup  = get_param('permitir_doc_duplicado', '0') == '1'
        doc_existente = Aluno.query.filter_by(documento=novo_documento, tipo_documento=tipo_doc).first()
        if not permitir_dup and doc_existente and doc_existente.id != id:
            flash('Documento já cadastrado para outro aluno.', 'danger')
            return render_template('alunos/editar.html', aluno=aluno, planos=planos, documentos=documentos)

        data_nasc_str  = request.form.get('data_nascimento')
        vencimento_str = request.form.get('vencimento')

        aluno.nome            = request.form.get('nome')
        aluno.tipo_documento  = tipo_doc
        aluno.documento       = novo_documento
        aluno.telefone        = request.form.get('telefone')
        aluno.email           = request.form.get('email')
        aluno.plano           = request.form.get('plano')
        aluno.ativo           = request.form.get('ativo') == '1'
        aluno.data_nascimento = datetime.strptime(data_nasc_str, '%Y-%m-%d').date() if data_nasc_str else None
        aluno.vencimento      = datetime.strptime(vencimento_str, '%Y-%m-%d').date() if vencimento_str else None

        foto_base64 = request.form.get('foto_base64')
        if foto_base64 and foto_base64.startswith('data:image'):
            import uuid, base64, os
            pasta    = os.path.join('app', 'static', 'fotos')
            os.makedirs(pasta, exist_ok=True)
            img_data = foto_base64.split(',')[1]
            filename = f'aluno_{uuid.uuid4().hex[:8]}.jpg'
            with open(os.path.join(pasta, filename), 'wb') as f:
                f.write(base64.b64decode(img_data))
            aluno.foto = f'/static/fotos/{filename}'

        db.session.commit()
        registrar_log('editou aluno', f'ID: {id} | Nome: {aluno.nome}')
        flash('Aluno atualizado com sucesso!', 'success')
        return redirect(url_for('auth.alunos'))

    return render_template('alunos/editar.html', aluno=aluno, planos=planos, documentos=documentos)

@auth.route('/alunos/<int:id>/excluir')
@login_required
def excluir_aluno(id):
    aluno = Aluno.query.get_or_404(id)
    nome  = aluno.nome
    registrar_log('excluiu aluno', f'ID: {id} | Nome: {nome}')
    db.session.delete(aluno)
    db.session.commit()
    flash(f'Aluno {nome} excluído com sucesso.', 'success')
    return redirect(url_for('auth.alunos'))

@auth.route('/alunos/<int:id>/documentos/upload', methods=['POST'])
@login_required
def upload_documento(id):
    import os, uuid
    from app.models import DocumentoAluno
    aluno   = Aluno.query.get_or_404(id)
    tipo    = request.form.get('tipo')
    arquivo = request.files.get('arquivo')

    if not arquivo or not tipo:
        flash('Preencha o tipo e selecione um arquivo.', 'danger')
        return redirect(url_for('auth.editar_aluno', id=id))

    ext = arquivo.filename.rsplit('.', 1)[-1].lower()
    if ext not in ['pdf', 'jpg', 'jpeg', 'png']:
        flash('Formato inválido. Use PDF, JPG ou PNG.', 'danger')
        return redirect(url_for('auth.editar_aluno', id=id))

    pasta    = os.path.join('app', 'static', 'documentos', str(id))
    os.makedirs(pasta, exist_ok=True)
    filename = f'{uuid.uuid4().hex[:8]}_{arquivo.filename}'
    arquivo.save(os.path.join(pasta, filename))

    db.session.add(DocumentoAluno(aluno_id=id, tipo=tipo,
        nome_arquivo=arquivo.filename,
        caminho=f'/static/documentos/{id}/{filename}'))
    db.session.commit()
    registrar_log('upload documento', f'Aluno: {aluno.nome} | Tipo: {tipo} | Arquivo: {arquivo.filename}')
    flash('Documento enviado com sucesso!', 'success')
    return redirect(url_for('auth.editar_aluno', id=id))

@auth.route('/alunos/<int:id>/documentos/<int:doc_id>/excluir')
@login_required
def excluir_documento(id, doc_id):
    import os
    from app.models import DocumentoAluno
    doc = DocumentoAluno.query.get_or_404(doc_id)
    try:
        filepath = os.path.join('app', doc.caminho.lstrip('/'))
        if os.path.exists(filepath):
            os.remove(filepath)
    except:
        pass
    db.session.delete(doc)
    db.session.commit()
    registrar_log('excluiu documento', f'Aluno ID: {id} | Arquivo: {doc.nome_arquivo}')
    flash('Documento excluído com sucesso!', 'success')
    return redirect(url_for('auth.editar_aluno', id=id))

@auth.route('/acessos')
@login_required
def acessos():
    # Usa _filtro_periodo para respeitar o fuso horário configurado
    hoje_local   = (datetime.utcnow() + timedelta(hours=int(get_param('fuso_horario', '-3')))).date()
    inicio_utc, fim_utc = _filtro_periodo(hoje_local, hoje_local)
    acessos_hoje = RegistroAcesso.query.filter(
        RegistroAcesso.entrada_em >= inicio_utc,
        RegistroAcesso.entrada_em <  fim_utc
    ).order_by(RegistroAcesso.entrada_em.desc()).all()
    alunos_ativos   = Aluno.query.filter_by(ativo=True).order_by(Aluno.nome).all()
    total_hoje      = len(acessos_hoje)
    total_biometria = sum(1 for a in acessos_hoje if a.tipo == 'biometria')
    total_manual    = sum(1 for a in acessos_hoje if a.tipo == 'manual')
    return render_template('acessos.html', acessos_hoje=acessos_hoje, alunos_ativos=alunos_ativos,
                           total_hoje=total_hoje, total_biometria=total_biometria, total_manual=total_manual)


@auth.route('/acessos/biometria', methods=['POST'])
def acesso_biometria():
    """Recebe acesso registrado pelo app de biometria."""
    from flask import request, jsonify
    data     = request.get_json(silent=True) or {}
    aluno_id = data.get('aluno_id')
    if not aluno_id:
        return jsonify({'erro': 'aluno_id obrigatorio'}), 400
    try:
        aluno = Aluno.query.get(aluno_id)
        if not aluno:
            return jsonify({'erro': 'Aluno nao encontrado'}), 404
        hoje = datetime.utcnow().date()
        bloquear = get_param('bloquear_plano_vencido', '0') == '1'
        if bloquear and aluno.vencimento and aluno.vencimento < hoje:
            return jsonify({'erro': 'Plano vencido', 'bloqueado': True}), 403
        db.session.add(RegistroAcesso(aluno_id=aluno_id, tipo='biometria'))
        db.session.commit()
        registrar_log('registrou acesso', f'Aluno: {aluno.nome} | Tipo: biometria')
        return jsonify({
            'ok': True,
            'nome': aluno.nome,
            'plano': aluno.plano or '',
            'vencido': bool(aluno.vencimento and aluno.vencimento < hoje)
        }), 200
    except Exception as e:
        return jsonify({'erro': str(e)}), 500

@auth.route('/acessos/registrar', methods=['POST'])
@login_required
def registrar_acesso():
    aluno_id   = request.form.get('aluno_id')
    observacao = request.form.get('observacao')
    aluno      = Aluno.query.get_or_404(aluno_id)
    hoje       = datetime.utcnow().date()

    bloquear = get_param('bloquear_plano_vencido', '0') == '1'
    if bloquear and aluno.vencimento and aluno.vencimento < hoje:
        flash(f'Acesso bloqueado! O plano de {aluno.nome} está vencido desde {aluno.vencimento.strftime("%d/%m/%Y")}.', 'danger')
        return redirect(url_for('auth.acessos'))
    if not bloquear and aluno.vencimento and aluno.vencimento < hoje:
        flash(f'Atenção! O plano de {aluno.nome} está vencido desde {aluno.vencimento.strftime("%d/%m/%Y")}.', 'warning')

    db.session.add(RegistroAcesso(aluno_id=aluno_id, tipo='manual', observacao=observacao))
    db.session.commit()
    registrar_log('registrou acesso', f'Aluno: {aluno.nome} | Tipo: manual')
    flash(f'Acesso de {aluno.nome} registrado com sucesso!', 'success')
    return redirect(url_for('auth.acessos'))

@auth.route('/monitoramento')
@login_required
def monitoramento():
    hoje             = datetime.utcnow().date()
    acessos_recentes = RegistroAcesso.query.filter(
        db.func.date(RegistroAcesso.entrada_em) == hoje
    ).order_by(RegistroAcesso.entrada_em.desc()).limit(10).all()
    total_hoje      = RegistroAcesso.query.filter(db.func.date(RegistroAcesso.entrada_em) == hoje).count()
    total_biometria = RegistroAcesso.query.filter(db.func.date(RegistroAcesso.entrada_em) == hoje, RegistroAcesso.tipo == 'biometria').count()
    total_manual    = RegistroAcesso.query.filter(db.func.date(RegistroAcesso.entrada_em) == hoje, RegistroAcesso.tipo == 'manual').count()
    ultimo          = RegistroAcesso.query.order_by(RegistroAcesso.entrada_em.desc()).first()
    return render_template('monitoramento.html', acessos_recentes=acessos_recentes,
                           total_hoje=total_hoje, total_biometria=total_biometria,
                           total_manual=total_manual, ultimo_id=ultimo.id if ultimo else 0, hoje=hoje)

@auth.route('/monitoramento/ultimo')
@login_required
def monitoramento_ultimo():
    acesso = RegistroAcesso.query.order_by(RegistroAcesso.entrada_em.desc()).first()
    if not acesso:
        return json.dumps({'id': 0})
    fuso           = int(get_param('fuso_horario', '-3'))
    hoje           = datetime.utcnow().date()
    vencido        = False
    vencimento_str = '—'
    if acesso.aluno.vencimento:
        vencido        = acesso.aluno.vencimento < hoje
        vencimento_str = acesso.aluno.vencimento.strftime('%d/%m/%Y')
    hora_local = (acesso.entrada_em + timedelta(hours=fuso)).strftime('%H:%M')
    cadastro   = acesso.aluno.criado_em.strftime('%d/%m/%Y') if acesso.aluno.criado_em else '—'
    return json.dumps({
        'id'        : acesso.id,
        'nome'      : acesso.aluno.nome,
        'plano'     : acesso.aluno.plano or 'Sem plano',
        'hora'      : hora_local,
        'vencido'   : vencido,
        'vencimento': vencimento_str,
        'foto'      : acesso.aluno.foto or '',
        'cpf'       : acesso.aluno.documento or '—',
        'telefone'  : acesso.aluno.telefone or '—',
        'cadastro'  : cadastro,
        'tipo'      : acesso.tipo or 'manual',
    })

@auth.route('/monitoramento/recentes')
@login_required
def monitoramento_recentes():
    fuso    = int(get_param('fuso_horario', '-3'))
    hoje    = datetime.utcnow().date()
    acessos = RegistroAcesso.query.filter(
        db.func.date(RegistroAcesso.entrada_em) == hoje
    ).order_by(RegistroAcesso.entrada_em.desc()).limit(10).all()
    return json.dumps([{
        'nome'   : a.aluno.nome,
        'hora'   : (a.entrada_em + timedelta(hours=fuso)).strftime('%H:%M'),
        'plano'  : a.aluno.plano or 'Sem plano',
        'vencido': a.aluno.vencimento < hoje if a.aluno.vencimento else False
    } for a in acessos])

@auth.route('/monitoramento/contadores')
@login_required
def monitoramento_contadores():
    hoje = datetime.utcnow().date()
    return json.dumps({
        'total'    : RegistroAcesso.query.filter(db.func.date(RegistroAcesso.entrada_em) == hoje).count(),
        'biometria': RegistroAcesso.query.filter(db.func.date(RegistroAcesso.entrada_em) == hoje, RegistroAcesso.tipo == 'biometria').count(),
        'manual'   : RegistroAcesso.query.filter(db.func.date(RegistroAcesso.entrada_em) == hoje, RegistroAcesso.tipo == 'manual').count(),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de relatório
# ─────────────────────────────────────────────────────────────────────────────

def _filtro_periodo(dt_inicio, dt_fim):
    fuso = int(get_param('fuso_horario', '-3'))
    inicio_utc = datetime(dt_inicio.year, dt_inicio.month, dt_inicio.day) - timedelta(hours=fuso)
    fim_utc    = datetime(dt_fim.year,    dt_fim.month,    dt_fim.day)    - timedelta(hours=fuso) + timedelta(days=1)
    return inicio_utc, fim_utc


def _build_heatmap(registros):
    fuso    = int(get_param('fuso_horario', '-3'))
    heatmap = [[0] * 24 for _ in range(7)]
    for r in registros:
        dt_local = r.entrada_em + timedelta(hours=fuso)
        heatmap[dt_local.weekday()][dt_local.hour] += 1
    pico_quente = {'dia': 0, 'hora': 0, 'valor': 0}
    pico_frio   = {'dia': 0, 'hora': 0, 'valor': 0}
    frio_val    = 999999
    tem         = False
    for d in range(7):
        for h in range(24):
            v = heatmap[d][h]
            if v > 0:
                tem = True
                if v > pico_quente['valor']:
                    pico_quente = {'dia': d, 'hora': h, 'valor': v}
                if v < frio_val:
                    frio_val  = v
                    pico_frio = {'dia': d, 'hora': h, 'valor': v}
    if not tem:
        pico_frio = {'dia': 0, 'hora': 0, 'valor': 0}
    return heatmap, pico_quente, pico_frio


def _agrupar_por_aluno(registros):
    aluno_map = {}
    for reg in registros:
        aid = reg.aluno_id
        if aid not in aluno_map:
            aluno_map[aid] = {'aluno': reg.aluno, 'total': 0,
                              'primeiro': reg.entrada_em, 'ultimo': reg.entrada_em}
        aluno_map[aid]['total'] += 1
        if reg.entrada_em < aluno_map[aid]['primeiro']:
            aluno_map[aid]['primeiro'] = reg.entrada_em
        if reg.entrada_em > aluno_map[aid]['ultimo']:
            aluno_map[aid]['ultimo'] = reg.entrada_em
    return sorted(aluno_map.values(), key=lambda x: x['aluno'].nome)


# ─────────────────────────────────────────────────────────────────────────────
# Relatórios
# ─────────────────────────────────────────────────────────────────────────────

@auth.route('/relatorios')
@login_required
def relatorios():
    from app.models import Plano as PlanoModel, Pagamento
    hoje      = datetime.utcnow().date()
    sete_dias = hoje + timedelta(days=7)

    tipo_ativo    = request.args.get('tipo', '')
    filtro_inicio = request.args.get('inicio', '')
    filtro_fim    = request.args.get('fim', '')

    # ── Filtros avançados ────────────────────────────────────────────────────
    f_nome      = request.args.get('f_nome',     '').strip()
    f_plano     = request.args.get('f_plano',    '')
    f_tipo_ac   = request.args.get('f_tipo_ac',  '')   # passagens: manual/biometria
    f_status    = request.args.get('f_status',   '')   # por_aluno: ativo/inativo
    f_ordem     = request.args.get('f_ordem',    '')   # por_aluno: mais/menos
    f_situacao  = request.args.get('f_situacao', '')   # vencimentos: em_dia/vence_breve/vencido
    f_usuario   = request.args.get('f_usuario',  '').strip()
    f_acao      = request.args.get('f_acao',     '')
    f_pg_status = request.args.get('f_pg_status','')   # financeiro: pago/pendente/vencido
    f_forma_pag = request.args.get('f_forma_pag','')   # financeiro: dinheiro/pix

    filtro_aplicado = bool(filtro_inicio and filtro_fim)

    passagens       = []
    por_aluno       = []
    auditoria       = []
    heatmap         = [[0] * 24 for _ in range(7)]
    pico_quente     = {'dia': 0, 'hora': 0, 'valor': 0}
    pico_frio       = {'dia': 0, 'hora': 0, 'valor': 0}
    total_passagens = 0
    fin_pagamentos  = []
    fin_resumo      = {'recebido': 0.0, 'pendente': 0.0, 'vencido': 0.0}

    if filtro_aplicado:
        dt_inicio  = datetime.strptime(filtro_inicio, '%Y-%m-%d').date()
        dt_fim     = datetime.strptime(filtro_fim,    '%Y-%m-%d').date()
        inicio_utc, fim_utc = _filtro_periodo(dt_inicio, dt_fim)

        # ── Passagens ────────────────────────────────────────────────────────
        q = RegistroAcesso.query.filter(
            RegistroAcesso.entrada_em >= inicio_utc,
            RegistroAcesso.entrada_em <  fim_utc
        )
        if f_tipo_ac:
            q = q.filter(RegistroAcesso.tipo == f_tipo_ac)
        passagens_raw = q.order_by(RegistroAcesso.entrada_em.desc()).all()

        if f_nome:
            passagens_raw = [r for r in passagens_raw if f_nome.lower() in r.aluno.nome.lower()]
        if f_plano and tipo_ativo in ('passagens', 'por_aluno'):
            passagens_raw = [r for r in passagens_raw if r.aluno.plano == f_plano]

        passagens       = passagens_raw
        total_passagens = len(passagens)

        # ── Por Aluno ────────────────────────────────────────────────────────
        por_aluno_raw = _agrupar_por_aluno(passagens_raw)
        if f_status:
            ativo_flag    = f_status == 'ativo'
            por_aluno_raw = [i for i in por_aluno_raw if i['aluno'].ativo == ativo_flag]
        if f_plano and tipo_ativo == 'por_aluno':
            por_aluno_raw = [i for i in por_aluno_raw if i['aluno'].plano == f_plano]
        if f_ordem == 'mais':
            por_aluno_raw = sorted(por_aluno_raw, key=lambda x: x['total'], reverse=True)
        elif f_ordem == 'menos':
            por_aluno_raw = sorted(por_aluno_raw, key=lambda x: x['total'])
        por_aluno = por_aluno_raw

        # ── Auditoria ────────────────────────────────────────────────────────
        qa = LogAuditoria.query.filter(
            LogAuditoria.criado_em >= inicio_utc,
            LogAuditoria.criado_em <  fim_utc
        )
        if f_usuario:
            qa = qa.filter(LogAuditoria.usuario.ilike(f'%{f_usuario}%'))
        if f_acao:
            qa = qa.filter(LogAuditoria.acao.ilike(f'%{f_acao}%'))
        auditoria = qa.order_by(LogAuditoria.criado_em.desc()).limit(200).all()

        # ── Heatmap ──────────────────────────────────────────────────────────
        heatmap, pico_quente, pico_frio = _build_heatmap(passagens_raw)

        # ── Financeiro detalhado ─────────────────────────────────────────────
        if tipo_ativo == 'financeiro':
            # Atualiza status vencido
            for pag in Pagamento.query.filter_by(status='pendente').all():
                if pag.data_vencimento < hoje:
                    pag.status = 'vencido'
            db.session.commit()

            qpag = Pagamento.query.filter(
                Pagamento.data_vencimento >= dt_inicio,
                Pagamento.data_vencimento <= dt_fim
            )
            if f_pg_status:
                qpag = qpag.filter(Pagamento.status == f_pg_status)
            if f_forma_pag:
                qpag = qpag.filter(Pagamento.forma_pagamento == f_forma_pag)
            if f_nome:
                qpag = qpag.join(Aluno).filter(Aluno.nome.ilike(f'%{f_nome}%'))
            if f_plano:
                qpag = qpag.join(Aluno, isouter=True).filter(Aluno.plano == f_plano)
            fin_pagamentos = qpag.order_by(Pagamento.data_vencimento).all()
            for p in fin_pagamentos:
                if p.status == 'pago':
                    fin_resumo['recebido']  += float(p.valor)
                elif p.status == 'vencido':
                    fin_resumo['vencido']   += float(p.valor)
                else:
                    fin_resumo['pendente']  += float(p.valor)

    # ── Vencimentos (sem necessidade de período) ─────────────────────────────
    qv = Aluno.query.filter_by(ativo=True)
    if f_plano and tipo_ativo == 'vencimentos':
        qv = qv.filter(Aluno.plano == f_plano)
    if f_nome and tipo_ativo == 'vencimentos':
        qv = qv.filter(Aluno.nome.ilike(f'%{f_nome}%'))
    vencimentos_raw = qv.order_by(Aluno.vencimento).all()
    if f_situacao:
        def get_sit(a):
            if a.vencimento and a.vencimento < hoje:       return 'vencido'
            if a.vencimento and a.vencimento <= sete_dias: return 'vence_breve'
            return 'em_dia'
        vencimentos_raw = [a for a in vencimentos_raw if get_sit(a) == f_situacao]
    vencimentos = vencimentos_raw

    # ── Financeiro resumo por plano ──────────────────────────────────────────
    financeiro = []
    for plano in PlanoModel.query.filter_by(ativo=True).all():
        total = Aluno.query.filter_by(plano=plano.nome, ativo=True).count()
        financeiro.append({'plano': plano.nome, 'valor_plano': float(plano.valor),
                           'total': total, 'receita': float(plano.valor) * total})

    # ── Listas para dropdowns dos filtros ────────────────────────────────────
    planos_lista   = [p.nome for p in PlanoModel.query.filter_by(ativo=True).order_by(PlanoModel.nome).all()]
    usuarios_lista = [u[0] for u in db.session.query(LogAuditoria.usuario).distinct().order_by(LogAuditoria.usuario).all()]
    acoes_lista    = sorted(set(
        a[0].split(' ')[0] for a in db.session.query(LogAuditoria.acao).distinct().all() if a[0]
    ))

    filtros_ativos = sum(1 for v in [f_nome, f_plano, f_tipo_ac, f_status, f_ordem,
                                      f_situacao, f_usuario, f_acao, f_pg_status, f_forma_pag] if v)

    relatorio_ativo = tipo_ativo
    return render_template('relatorios.html',
                           passagens=passagens, por_aluno=por_aluno,
                           total_passagens=total_passagens,
                           auditoria=auditoria, vencimentos=vencimentos,
                           financeiro=financeiro,
                           fin_pagamentos=fin_pagamentos, fin_resumo=fin_resumo,
                           hoje=hoje, sete_dias=sete_dias,
                           tipo_ativo=tipo_ativo,
                           filtro_inicio=filtro_inicio, filtro_fim=filtro_fim,
                           filtro_aplicado=filtro_aplicado,
                           heatmap_json=json.dumps(heatmap),
                           nomes_dias=['Seg','Ter','Qua','Qui','Sex','Sáb','Dom'],
                           pico_quente=pico_quente, pico_frio=pico_frio,
                           planos_lista=planos_lista,
                           usuarios_lista=usuarios_lista,
                           acoes_lista=acoes_lista,
                           filtros_ativos=filtros_ativos,
                           f_nome=f_nome, f_plano=f_plano, f_tipo_ac=f_tipo_ac,
                           f_status=f_status, f_ordem=f_ordem, f_situacao=f_situacao,
                           f_usuario=f_usuario, f_acao=f_acao,
                           f_pg_status=f_pg_status, f_forma_pag=f_forma_pag,
                           relatorio_ativo=relatorio_ativo)


@auth.route('/relatorios/exportar/csv')
@login_required
def exportar_csv():
    import csv, io
    from flask import Response
    from app.models import Pagamento
    tipo          = request.args.get('tipo', 'passagens')
    filtro_inicio = request.args.get('inicio', '')
    filtro_fim    = request.args.get('fim', '')
    colunas_str   = request.args.get('colunas', '')
    hoje          = datetime.utcnow().date()

    if not filtro_inicio or not filtro_fim:
        flash('Informe o período para exportar.', 'warning')
        return redirect(url_for('auth.relatorios'))

    dt_inicio  = datetime.strptime(filtro_inicio, '%Y-%m-%d').date()
    dt_fim     = datetime.strptime(filtro_fim,    '%Y-%m-%d').date()
    inicio_utc, fim_utc = _filtro_periodo(dt_inicio, dt_fim)
    fuso = int(get_param('fuso_horario', '-3'))

    f_nome      = request.args.get('f_nome',     '').strip()
    f_plano     = request.args.get('f_plano',    '')
    f_tipo_ac   = request.args.get('f_tipo_ac',  '')
    f_status    = request.args.get('f_status',   '')
    f_ordem     = request.args.get('f_ordem',    '')
    f_situacao  = request.args.get('f_situacao', '')
    f_usuario   = request.args.get('f_usuario',  '').strip()
    f_acao      = request.args.get('f_acao',     '')
    f_pg_status = request.args.get('f_pg_status','')
    f_forma_pag = request.args.get('f_forma_pag','')

    output = io.StringIO()
    writer = csv.writer(output)

    # ── helpers de coluna ────────────────────────────────────────────────────
    def get_col_passagem(r, col):
        dt_local = r.entrada_em + timedelta(hours=fuso)
        m = {'data_hora': dt_local.strftime('%d/%m/%Y %H:%M'),
             'aluno': r.aluno.nome,
             'cpf': r.aluno.documento if r.aluno.tipo_documento=='cpf' else '—',
             'telefone': r.aluno.telefone or '—',
             'plano': r.aluno.plano or '—',
             'tipo': r.tipo,
             'observacao': r.observacao or '—'}
        return m.get(col, '—')

    def get_col_por_aluno(item, col):
        m = {'aluno': item['aluno'].nome,
             'cpf': item['aluno'].documento if item['aluno'].tipo_documento=='cpf' else '—',
             'telefone': item['aluno'].telefone or '—',
             'plano': item['aluno'].plano or '—',
             'total': str(item['total']),
             'primeiro': (item['primeiro']+timedelta(hours=fuso)).strftime('%d/%m/%Y %H:%M'),
             'ultimo':   (item['ultimo']  +timedelta(hours=fuso)).strftime('%d/%m/%Y %H:%M'),
             'status': 'Ativo' if item['aluno'].ativo else 'Inativo'}
        return m.get(col, '—')

    def get_col_vencimento(a, col):
        sit = ('Vencido' if a.vencimento and a.vencimento < hoje else
               'Vence em breve' if a.vencimento and a.vencimento <= hoje+timedelta(days=7) else 'Em dia')
        m = {'aluno': a.nome,
             'cpf': a.documento if a.tipo_documento=='cpf' else '—',
             'telefone': a.telefone or '—',
             'plano': a.plano or '—',
             'vencimento': a.vencimento.strftime('%d/%m/%Y') if a.vencimento else '—',
             'situacao': sit}
        return m.get(col, '—')

    def get_col_auditoria(log, col):
        m = {'data_hora': log.criado_em.strftime('%d/%m/%Y %H:%M'),
             'usuario': log.usuario, 'acao': log.acao,
             'detalhes': log.detalhes or '—', 'ip': log.ip or '—'}
        return m.get(col, '—')

    def get_col_financeiro(p, col):
        m = {'aluno': p.aluno.nome,
             'cpf': p.aluno.documento if p.aluno.tipo_documento=='cpf' else '—',
             'telefone': p.aluno.telefone or '—',
             'plano': p.aluno.plano or '—',
             'valor': f'R$ {float(p.valor):.2f}',
             'vencimento': p.data_vencimento.strftime('%d/%m/%Y'),
             'pagamento': p.data_pagamento.strftime('%d/%m/%Y') if p.data_pagamento else '—',
             'forma': p.forma_pagamento or '—',
             'status': p.status.capitalize()}
        return m.get(col, '—')

    col_labels = {
        'data_hora':'Data/Hora','aluno':'Aluno','cpf':'CPF','telefone':'Telefone',
        'plano':'Plano','tipo':'Tipo','observacao':'Observação','total':'Total Passagens',
        'primeiro':'Primeira Entrada','ultimo':'Última Entrada','status':'Status',
        'vencimento':'Vencimento','situacao':'Situação','usuario':'Usuário',
        'acao':'Ação','detalhes':'Detalhes','ip':'IP',
        'valor':'Valor','pagamento':'Data Pagamento','forma':'Forma',
    }

    def parse_cols(default_list):
        if colunas_str:
            return [c for c in colunas_str.split(',') if c]
        return default_list

    if tipo == 'passagens':
        cols = parse_cols(['data_hora','aluno','plano','tipo','observacao'])
        writer.writerow([col_labels.get(c,c) for c in cols])
        q = RegistroAcesso.query.filter(RegistroAcesso.entrada_em >= inicio_utc, RegistroAcesso.entrada_em < fim_utc)
        if f_tipo_ac: q = q.filter(RegistroAcesso.tipo == f_tipo_ac)
        rows = q.order_by(RegistroAcesso.entrada_em.desc()).all()
        if f_nome:  rows = [r for r in rows if f_nome.lower() in r.aluno.nome.lower()]
        if f_plano: rows = [r for r in rows if r.aluno.plano == f_plano]
        for r in rows:
            writer.writerow([get_col_passagem(r, c) for c in cols])

    elif tipo == 'por_aluno':
        cols = parse_cols(['aluno','plano','total','primeiro','ultimo','status'])
        writer.writerow([col_labels.get(c,c) for c in cols])
        q = RegistroAcesso.query.filter(RegistroAcesso.entrada_em >= inicio_utc, RegistroAcesso.entrada_em < fim_utc)
        if f_tipo_ac: q = q.filter(RegistroAcesso.tipo == f_tipo_ac)
        rows = q.order_by(RegistroAcesso.entrada_em.desc()).all()
        if f_nome:  rows = [r for r in rows if f_nome.lower() in r.aluno.nome.lower()]
        if f_plano: rows = [r for r in rows if r.aluno.plano == f_plano]
        agrupado = _agrupar_por_aluno(rows)
        if f_status:  agrupado = [i for i in agrupado if i['aluno'].ativo == (f_status == 'ativo')]
        if f_ordem == 'mais':    agrupado = sorted(agrupado, key=lambda x: x['total'], reverse=True)
        elif f_ordem == 'menos': agrupado = sorted(agrupado, key=lambda x: x['total'])
        for item in agrupado:
            writer.writerow([get_col_por_aluno(item, c) for c in cols])

    elif tipo == 'vencimentos':
        cols = parse_cols(['aluno','plano','vencimento','situacao'])
        writer.writerow([col_labels.get(c,c) for c in cols])
        qv = Aluno.query.filter_by(ativo=True)
        if f_plano: qv = qv.filter(Aluno.plano == f_plano)
        if f_nome:  qv = qv.filter(Aluno.nome.ilike(f'%{f_nome}%'))
        vlist = qv.order_by(Aluno.vencimento).all()
        if f_situacao:
            def gs(a):
                if a.vencimento and a.vencimento < hoje: return 'vencido'
                if a.vencimento and a.vencimento <= hoje+timedelta(days=7): return 'vence_breve'
                return 'em_dia'
            vlist = [a for a in vlist if gs(a) == f_situacao]
        for a in vlist:
            writer.writerow([get_col_vencimento(a, c) for c in cols])

    elif tipo == 'auditoria':
        cols = parse_cols(['data_hora','usuario','acao','detalhes','ip'])
        writer.writerow([col_labels.get(c,c) for c in cols])
        qa = LogAuditoria.query.filter(LogAuditoria.criado_em >= inicio_utc, LogAuditoria.criado_em < fim_utc)
        if f_usuario: qa = qa.filter(LogAuditoria.usuario.ilike(f'%{f_usuario}%'))
        if f_acao:    qa = qa.filter(LogAuditoria.acao.ilike(f'%{f_acao}%'))
        for log in qa.order_by(LogAuditoria.criado_em.desc()).all():
            writer.writerow([get_col_auditoria(log, c) for c in cols])

    elif tipo == 'financeiro':
        cols = parse_cols(['aluno','plano','valor','vencimento','pagamento','forma','status'])
        writer.writerow([col_labels.get(c,c) for c in cols])
        qpag = Pagamento.query.filter(Pagamento.data_vencimento >= dt_inicio, Pagamento.data_vencimento <= dt_fim)
        if f_pg_status: qpag = qpag.filter(Pagamento.status == f_pg_status)
        if f_forma_pag: qpag = qpag.filter(Pagamento.forma_pagamento == f_forma_pag)
        if f_nome:  qpag = qpag.join(Aluno).filter(Aluno.nome.ilike(f'%{f_nome}%'))
        if f_plano: qpag = qpag.join(Aluno, isouter=True).filter(Aluno.plano == f_plano)
        for p in qpag.order_by(Pagamento.data_vencimento).all():
            writer.writerow([get_col_financeiro(p, c) for c in cols])

    output.seek(0)
    return Response('\ufeff' + output.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment; filename=relatorio_{tipo}_{hoje}.csv'})


@auth.route('/relatorios/exportar/pdf')
@login_required
def exportar_pdf():
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
    from reportlab.lib.units import cm
    from reportlab.pdfgen import canvas as pdfcanvas
    from flask import Response
    from app.models import Pagamento
    import io, os

    tipo          = request.args.get('tipo', 'passagens')
    filtro_inicio = request.args.get('inicio', '')
    filtro_fim    = request.args.get('fim', '')
    colunas_str   = request.args.get('colunas', '')
    hoje          = datetime.utcnow().date()

    if not filtro_inicio or not filtro_fim:
        flash('Informe o período para exportar.', 'warning')
        return redirect(url_for('auth.relatorios'))

    dt_inicio  = datetime.strptime(filtro_inicio, '%Y-%m-%d').date()
    dt_fim     = datetime.strptime(filtro_fim,    '%Y-%m-%d').date()
    inicio_utc, fim_utc = _filtro_periodo(dt_inicio, dt_fim)
    fuso = int(get_param('fuso_horario', '-3'))

    f_nome      = request.args.get('f_nome',     '').strip()
    f_plano     = request.args.get('f_plano',    '')
    f_tipo_ac   = request.args.get('f_tipo_ac',  '')
    f_status    = request.args.get('f_status',   '')
    f_ordem     = request.args.get('f_ordem',    '')
    f_situacao  = request.args.get('f_situacao', '')
    f_usuario   = request.args.get('f_usuario',  '').strip()
    f_acao      = request.args.get('f_acao',     '')
    f_pg_status = request.args.get('f_pg_status','')
    f_forma_pag = request.args.get('f_forma_pag','')

    LARANJA = colors.HexColor(get_param('cor_principal', '#FF6B00'))
    SIDEBAR = colors.HexColor('#1a1a2e')
    CINZA   = colors.HexColor('#6c757d')
    nome_ac = get_param('nome_academia', 'Academia')
    logo_url = get_param('logo_url', '')

    titulos_map = {'passagens': 'Relatorio de Passagens', 'por_aluno': 'Passagens por Aluno',
               'vencimentos': 'Relatorio de Vencimentos', 'auditoria': 'Log de Auditoria',
               'financeiro': 'Relatorio Financeiro'}
    titulo_rel = titulos_map.get(tipo, 'Relatorio')

    col_labels = {
        'data_hora':'Data/Hora','aluno':'Aluno','cpf':'CPF','telefone':'Telefone',
        'plano':'Plano','tipo':'Tipo','observacao':'Observacao','total':'Total',
        'primeiro':'Primeira Entrada','ultimo':'Ultima Entrada','status':'Status',
        'vencimento':'Vencimento','situacao':'Situacao','usuario':'Usuario',
        'acao':'Acao','detalhes':'Detalhes','ip':'IP',
        'valor':'Valor','pagamento':'Data Pagamento','forma':'Forma',
    }

    def parse_cols(default_list):
        if colunas_str:
            return [c for c in colunas_str.split(',') if c]
        return default_list

    class AcadCanvas(pdfcanvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved = []
        def showPage(self):
            self._saved.append(dict(self.__dict__))
            self._startPage()
        def save(self):
            total = len(self._saved)
            for state in self._saved:
                self.__dict__.update(state)
                pw, ph = landscape(A4)
                # Faixa colorida no topo
                self.setFillColor(SIDEBAR)
                self.rect(0, ph - 1.4*cm, pw, 1.4*cm, fill=1, stroke=0)
                self.setFillColor(LARANJA)
                self.rect(0, ph - 1.4*cm, 0.5*cm, 1.4*cm, fill=1, stroke=0)
                # Logo se existir
                logo_x = 0.8*cm
                if logo_url:
                    logo_path = os.path.join('app', logo_url.lstrip('/'))
                    if os.path.exists(logo_path):
                        try:
                            self.drawImage(logo_path, logo_x, ph - 1.25*cm, width=1*cm, height=1*cm,
                                           preserveAspectRatio=True, mask='auto')
                            logo_x = 2.0*cm
                        except:
                            pass
                # Nome e título
                self.setFont('Helvetica-Bold', 11)
                self.setFillColor(colors.white)
                self.drawString(logo_x, ph - 0.85*cm, nome_ac.upper())
                self.setFont('Helvetica', 8)
                self.setFillColor(colors.HexColor('#aaaaaa'))
                self.drawRightString(pw - 1*cm, ph - 0.85*cm, f'Pag. {self._pageNumber} de {total}')
                # Rodapé
                self.setStrokeColor(colors.HexColor('#dee2e6'))
                self.setLineWidth(0.5)
                self.line(1*cm, 1.4*cm, pw - 1*cm, 1.4*cm)
                self.setFont('Helvetica', 8)
                self.setFillColor(CINZA)
                self.drawString(1*cm, 1.0*cm, nome_ac + '  —  Confidencial')
                super().showPage()
            super().save()

    buffer = io.BytesIO()
    doc    = SimpleDocTemplate(buffer, pagesize=landscape(A4),
                               leftMargin=1*cm, rightMargin=1*cm,
                               topMargin=1.8*cm, bottomMargin=1.8*cm)
    styles  = getSampleStyleSheet()
    st_small = ParagraphStyle('small', fontSize=8, leading=10)
    elements = []

    def tbl(data):
        pw_total = landscape(A4)[0] - 2*cm
        n = len(data[0]) if data else 1
        cw = pw_total / n
        t = Table(data, colWidths=[cw]*n, repeatRows=1)
        t.setStyle(TableStyle([
            ('BACKGROUND',    (0,0),(-1,0),  SIDEBAR),
            ('TEXTCOLOR',     (0,0),(-1,0),  colors.white),
            ('FONTNAME',      (0,0),(-1,0),  'Helvetica-Bold'),
            ('FONTSIZE',      (0,0),(-1,0),  8),
            ('FONTSIZE',      (0,1),(-1,-1), 8),
            ('ROWBACKGROUNDS',(0,1),(-1,-1), [colors.white, colors.HexColor('#f8f9fa')]),
            ('GRID',          (0,0),(-1,-1), 0.3, colors.HexColor('#dee2e6')),
            ('PADDING',       (0,0),(-1,-1), 5),
            ('VALIGN',        (0,0),(-1,-1), 'MIDDLE'),
        ]))
        return t

    if tipo == 'passagens':
        cols = parse_cols(['data_hora','aluno','plano','tipo','observacao'])
        q = RegistroAcesso.query.filter(RegistroAcesso.entrada_em >= inicio_utc, RegistroAcesso.entrada_em < fim_utc)
        if f_tipo_ac: q = q.filter(RegistroAcesso.tipo == f_tipo_ac)
        rows = q.order_by(RegistroAcesso.entrada_em.desc()).all()
        if f_nome:  rows = [r for r in rows if f_nome.lower() in r.aluno.nome.lower()]
        if f_plano: rows = [r for r in rows if r.aluno.plano == f_plano]
        def gcp(r, c):
            dt_local = r.entrada_em + timedelta(hours=fuso)
            m = {'data_hora': dt_local.strftime('%d/%m/%Y %H:%M'),'aluno': r.aluno.nome,
                 'cpf': r.aluno.documento if r.aluno.tipo_documento=='cpf' else '—',
                 'telefone': r.aluno.telefone or '—','plano': r.aluno.plano or '—',
                 'tipo': r.tipo,'observacao': r.observacao or '—'}
            return m.get(c, '—')
        data = [[col_labels.get(c,c) for c in cols]] + [[gcp(r,c) for c in cols] for r in rows]

    elif tipo == 'por_aluno':
        cols = parse_cols(['aluno','plano','total','primeiro','ultimo','status'])
        q = RegistroAcesso.query.filter(RegistroAcesso.entrada_em >= inicio_utc, RegistroAcesso.entrada_em < fim_utc)
        if f_tipo_ac: q = q.filter(RegistroAcesso.tipo == f_tipo_ac)
        rows = q.order_by(RegistroAcesso.entrada_em.desc()).all()
        if f_nome:  rows = [r for r in rows if f_nome.lower() in r.aluno.nome.lower()]
        if f_plano: rows = [r for r in rows if r.aluno.plano == f_plano]
        agrupado = _agrupar_por_aluno(rows)
        if f_status:  agrupado = [i for i in agrupado if i['aluno'].ativo == (f_status == 'ativo')]
        if f_ordem == 'mais':    agrupado = sorted(agrupado, key=lambda x: x['total'], reverse=True)
        elif f_ordem == 'menos': agrupado = sorted(agrupado, key=lambda x: x['total'])
        def gcpa(item, c):
            m = {'aluno': item['aluno'].nome,
                 'cpf': item['aluno'].documento if item['aluno'].tipo_documento=='cpf' else '—',
                 'telefone': item['aluno'].telefone or '—',
                 'plano': item['aluno'].plano or '—',
                 'total': str(item['total']),
                 'primeiro': (item['primeiro']+timedelta(hours=fuso)).strftime('%d/%m/%Y %H:%M'),
                 'ultimo':   (item['ultimo'  ]+timedelta(hours=fuso)).strftime('%d/%m/%Y %H:%M'),
                 'status': 'Ativo' if item['aluno'].ativo else 'Inativo'}
            return m.get(c, '—')
        data = [[col_labels.get(c,c) for c in cols]] + [[gcpa(i,c) for c in cols] for i in agrupado]

    elif tipo == 'vencimentos':
        cols = parse_cols(['aluno','plano','vencimento','situacao'])
        qv = Aluno.query.filter_by(ativo=True)
        if f_plano: qv = qv.filter(Aluno.plano == f_plano)
        if f_nome:  qv = qv.filter(Aluno.nome.ilike(f'%{f_nome}%'))
        vlist = qv.order_by(Aluno.vencimento).all()
        if f_situacao:
            def gs(a):
                if a.vencimento and a.vencimento < hoje: return 'vencido'
                if a.vencimento and a.vencimento <= hoje+timedelta(days=7): return 'vence_breve'
                return 'em_dia'
            vlist = [a for a in vlist if gs(a) == f_situacao]
        def gcv(a, c):
            sit = ('Vencido' if a.vencimento and a.vencimento < hoje else
                   'Vence em breve' if a.vencimento and a.vencimento <= hoje+timedelta(days=7) else 'Em dia')
            m = {'aluno': a.nome,'cpf': a.documento if a.tipo_documento=='cpf' else '—',
                 'telefone': a.telefone or '—','plano': a.plano or '—',
                 'vencimento': a.vencimento.strftime('%d/%m/%Y') if a.vencimento else '—',
                 'situacao': sit}
            return m.get(c, '—')
        data = [[col_labels.get(c,c) for c in cols]] + [[gcv(a,c) for c in cols] for a in vlist]

    elif tipo == 'auditoria':
        cols = parse_cols(['data_hora','usuario','acao','detalhes','ip'])
        qa = LogAuditoria.query.filter(LogAuditoria.criado_em >= inicio_utc, LogAuditoria.criado_em < fim_utc)
        if f_usuario: qa = qa.filter(LogAuditoria.usuario.ilike(f'%{f_usuario}%'))
        if f_acao:    qa = qa.filter(LogAuditoria.acao.ilike(f'%{f_acao}%'))
        def gca(log, c):
            m = {'data_hora': log.criado_em.strftime('%d/%m/%Y %H:%M'),
                 'usuario': log.usuario,'acao': log.acao,
                 'detalhes': Paragraph(log.detalhes or '—', st_small),'ip': log.ip or '—'}
            return m.get(c, '—')
        data = [[col_labels.get(c,c) for c in cols]] + [[gca(l,c) for c in cols] for l in qa.order_by(LogAuditoria.criado_em.desc()).all()]

    elif tipo == 'financeiro':
        cols = parse_cols(['aluno','plano','valor','vencimento','pagamento','forma','status'])
        qpag = Pagamento.query.filter(Pagamento.data_vencimento >= dt_inicio, Pagamento.data_vencimento <= dt_fim)
        if f_pg_status: qpag = qpag.filter(Pagamento.status == f_pg_status)
        if f_forma_pag: qpag = qpag.filter(Pagamento.forma_pagamento == f_forma_pag)
        if f_nome:  qpag = qpag.join(Aluno).filter(Aluno.nome.ilike(f'%{f_nome}%'))
        if f_plano: qpag = qpag.join(Aluno, isouter=True).filter(Aluno.plano == f_plano)
        def gcf(p, c):
            m = {'aluno': p.aluno.nome,
                 'cpf': p.aluno.documento if p.aluno.tipo_documento=='cpf' else '—',
                 'telefone': p.aluno.telefone or '—',
                 'plano': p.aluno.plano or '—',
                 'valor': f'R$ {float(p.valor):.2f}',
                 'vencimento': p.data_vencimento.strftime('%d/%m/%Y'),
                 'pagamento': p.data_pagamento.strftime('%d/%m/%Y') if p.data_pagamento else '—',
                 'forma': p.forma_pagamento or '—',
                 'status': p.status.capitalize()}
            return m.get(c, '—')
        pags = qpag.order_by(Pagamento.data_vencimento).all()
        data = [[col_labels.get(c,c) for c in cols]] + [[gcf(p,c) for c in cols] for p in pags]
    else:
        data = [['Sem dados']]

    # ── Bloco de título + período acima da tabela ──────────────────────────
    from reportlab.platypus import Table as RLTable, TableStyle as RLTS
    from reportlab.lib.enums import TA_LEFT

    pw_pg = landscape(A4)[0] - 2*cm

    st_h1  = ParagraphStyle('h1rel',  fontSize=20, fontName='Helvetica-Bold',
                             textColor=SIDEBAR, spaceAfter=0, leading=24)
    st_per = ParagraphStyle('perrel', fontSize=10, textColor=CINZA, spaceAfter=2)
    st_emi = ParagraphStyle('emirel', fontSize=9,  textColor=CINZA, spaceAfter=0)

    elements.append(Paragraph(titulo_rel, st_h1))
    elements.append(Spacer(1, 0.1*cm))
    elements.append(HRFlowable(width='100%', thickness=2, color=LARANJA,
                                spaceBefore=0, spaceAfter=10))
    elements.append(Paragraph(
        f'Período: <b>{dt_inicio.strftime("%d/%m/%Y")}</b> a <b>{dt_fim.strftime("%d/%m/%Y")}</b>',
        st_per))
    elements.append(Paragraph(
        f'Emitido em: {hoje.strftime("%d/%m/%Y")}  |  {nome_ac}',
        st_emi))
    elements.append(Spacer(1, 0.5*cm))

    if len(data) > 1:
        elements.append(tbl(data))
    else:
        elements.append(Paragraph('Nenhum dado encontrado para os filtros aplicados.',
                                   ParagraphStyle('info', fontSize=10, textColor=CINZA)))

    doc.build(elements, canvasmaker=AcadCanvas)
    buffer.seek(0)
    return Response(buffer.getvalue(), mimetype='application/pdf',
                    headers={'Content-Disposition': f'attachment; filename=relatorio_{tipo}_{hoje}.pdf'})


@auth.route('/configuracoes', methods=['GET', 'POST'])
@login_required
def configuracoes():
    import os
    from app.models import Configuracao

    def set_config(chave, valor):
        config = Configuracao.query.filter_by(chave=chave).first()
        if config: config.valor = valor
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
        return redirect(url_for('auth.configuracoes'))

    configs = {c.chave: c.valor for c in Configuracao.query.all()}

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

@auth.route('/usuarios')
@login_required
def usuarios():
    from app.models import Usuario
    return render_template('usuarios.html', usuarios=Usuario.query.order_by(Usuario.nome).all())

@auth.route('/usuarios/novo', methods=['POST'])
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
        return redirect(url_for('auth.usuarios'))
    valido, erro = validar_senha(senha)
    if not valido:
        flash(erro, 'danger')
        return redirect(url_for('auth.usuarios'))
    if Usuario.query.filter_by(usuario=usuario_str).first():
        flash('Esse nome de usuário já existe.', 'danger')
        return redirect(url_for('auth.usuarios'))

    db.session.add(Usuario(nome=nome, usuario=usuario_str,
                           senha_hash=generate_password_hash(senha),
                           perfil=perfil, ativo=True,
                           senha_alterada_em=datetime.utcnow()))
    db.session.commit()
    registrar_log('criou usuário', f'Usuário: {usuario_str} | Perfil: {perfil}')
    flash(f'Usuário {nome} criado com sucesso!', 'success')
    return redirect(url_for('auth.usuarios'))

@auth.route('/usuarios/<int:id>/editar', methods=['POST'])
@login_required
def editar_usuario(id):
    from app.models import Usuario
    from werkzeug.security import generate_password_hash
    usuario     = Usuario.query.get_or_404(id)
    usuario_str = request.form.get('usuario')
    existente   = Usuario.query.filter_by(usuario=usuario_str).first()
    if existente and existente.id != id:
        flash('Esse nome de usuário já existe.', 'danger')
        return redirect(url_for('auth.usuarios'))
    usuario.nome    = request.form.get('nome')
    usuario.usuario = usuario_str
    usuario.perfil  = request.form.get('perfil')
    usuario.ativo   = request.form.get('ativo') == '1'
    senha = request.form.get('senha')
    if senha:
        valido, erro = validar_senha(senha)
        if not valido:
            flash(erro, 'danger')
            return redirect(url_for('auth.usuarios'))
        usuario.senha_hash        = generate_password_hash(senha)
        usuario.senha_alterada_em = datetime.utcnow()
    db.session.commit()
    registrar_log('editou usuário', f'ID: {id} | Usuário: {usuario_str}')
    flash(f'Usuário {usuario.nome} atualizado com sucesso!', 'success')
    return redirect(url_for('auth.usuarios'))

@auth.route('/usuarios/<int:id>/toggle')
@login_required
def toggle_usuario(id):
    from app.models import Usuario
    usuario = Usuario.query.get_or_404(id)
    if usuario.usuario == 'admin':
        flash('O usuário admin não pode ser desativado.', 'danger')
        return redirect(url_for('auth.usuarios'))
    usuario.ativo = not usuario.ativo
    db.session.commit()
    status = 'ativado' if usuario.ativo else 'desativado'
    registrar_log(f'{status} usuário', f'Usuário: {usuario.usuario}')
    flash(f'Usuário {usuario.nome} {status} com sucesso!', 'success')
    return redirect(url_for('auth.usuarios'))

@auth.route('/parametros', methods=['GET', 'POST'])
@login_required
def parametros():
    from app.models import Configuracao

    def set_config(chave, valor):
        config = Configuracao.query.filter_by(chave=chave).first()
        if config: config.valor = valor
        else: db.session.add(Configuracao(chave=chave, valor=valor))

    if request.method == 'POST':
        for chave, padrao in [
            ('senha_tamanho_minimo','6'), ('senha_validade_dias','0'),
            ('senha_aviso_dias','7'), ('dias_alerta_vencimento','7'),
            ('timeout_sessao','60'), ('fuso_horario','-3')
        ]:
            set_config(chave, request.form.get(chave, padrao))
        for chave in ['senha_exigir_numero','senha_exigir_maiuscula','senha_exigir_especial',
                      'alertar_senha_vencimento','permitir_doc_duplicado',
                      'bloquear_plano_vencido','alertar_plano_vencimento',
                      'forma_dinheiro','forma_pix','forma_credito','forma_debito','forma_boleto']:
            set_config(chave, '1' if request.form.get(chave) else '0')
        for chave in ['taxa_credito', 'taxa_debito']:
            set_config(chave, request.form.get(chave, '0'))
        db.session.commit()
        registrar_log('alterou parametros', 'Parâmetros do sistema atualizados')
        flash('Parâmetros salvos com sucesso!', 'success')
        return redirect(url_for('auth.parametros'))

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

    from app.models import Plano
    planos_ativos = Plano.query.filter_by(ativo=True).order_by(Plano.nome).all()
    return render_template('parametros.html', params=Params(), planos_ativos=planos_ativos)

@auth.route('/planos')
@login_required
def planos():
    from app.models import Plano
    return render_template('planos.html', planos=Plano.query.order_by(Plano.id).all())

@auth.route('/planos/<int:id>/editar', methods=['POST'])
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
    return redirect(url_for('auth.planos'))

@auth.route('/financeiro')
@login_required
def financeiro():
    from app.models import Pagamento, Plano
    import calendar

    hoje          = date.today()
    status_filtro = request.args.get('status', 'todos')
    periodo_ativo = request.args.get('periodo', 'mes_atual')

    # ── Calcular período ──────────────────────────────────────────────────────
    def inicio_semana(d):
        return d - timedelta(days=d.weekday())

    if periodo_ativo == 'hoje':
        primeiro_dia = ultimo_dia = hoje
    elif periodo_ativo == 'ontem':
        primeiro_dia = ultimo_dia = hoje - timedelta(days=1)
    elif periodo_ativo == 'semana_atual':
        primeiro_dia = inicio_semana(hoje)
        ultimo_dia   = primeiro_dia + timedelta(days=6)
    elif periodo_ativo == 'semana_passada':
        primeiro_dia = inicio_semana(hoje) - timedelta(days=7)
        ultimo_dia   = primeiro_dia + timedelta(days=6)
    elif periodo_ativo == 'mes_passado':
        if hoje.month == 1:
            primeiro_dia = date(hoje.year - 1, 12, 1)
        else:
            primeiro_dia = date(hoje.year, hoje.month - 1, 1)
        ultimo_dia = date(primeiro_dia.year, primeiro_dia.month,
                          calendar.monthrange(primeiro_dia.year, primeiro_dia.month)[1])
    elif periodo_ativo == 'personalizado':
        try:
            primeiro_dia = datetime.strptime(request.args.get('inicio', hoje.strftime('%Y-%m-%d')), '%Y-%m-%d').date()
            ultimo_dia   = datetime.strptime(request.args.get('fim',    hoje.strftime('%Y-%m-%d')), '%Y-%m-%d').date()
        except:
            primeiro_dia = date(hoje.year, hoje.month, 1)
            ultimo_dia   = date(hoje.year, hoje.month, calendar.monthrange(hoje.year, hoje.month)[1])
    else:  # mes_atual (padrão)
        periodo_ativo = 'mes_atual'
        primeiro_dia  = date(hoje.year, hoje.month, 1)
        ultimo_dia    = date(hoje.year, hoje.month, calendar.monthrange(hoje.year, hoje.month)[1])

    data_inicio = primeiro_dia.strftime('%Y-%m-%d')
    data_fim    = ultimo_dia.strftime('%Y-%m-%d')
    mes_atual   = primeiro_dia.strftime('%Y-%m')

    # ── Atualiza status vencido ───────────────────────────────────────────────
    for pag in Pagamento.query.filter_by(status='pendente').all():
        if pag.data_vencimento < hoje:
            pag.status = 'vencido'
    db.session.commit()

    # ── Consultas ─────────────────────────────────────────────────────────────
    inadimplentes = []
    if status_filtro == 'inadimplentes':
        alunos_com_pagamento = db.session.query(Pagamento.aluno_id).filter(
            Pagamento.data_vencimento >= primeiro_dia,
            Pagamento.data_vencimento <= ultimo_dia,
            Pagamento.status == 'pago'
        ).subquery()
        inadimplentes = Aluno.query.filter(
            Aluno.ativo == True, ~Aluno.id.in_(alunos_com_pagamento)
        ).order_by(Aluno.nome).all()
        pagamentos = []
    else:
        query = Pagamento.query.filter(
            Pagamento.data_vencimento >= primeiro_dia,
            Pagamento.data_vencimento <= ultimo_dia
        )
        if status_filtro != 'todos':
            query = query.filter(Pagamento.status == status_filtro)
        pagamentos = query.order_by(Pagamento.data_vencimento).all()

    todos_periodo        = Pagamento.query.filter(Pagamento.data_vencimento >= primeiro_dia, Pagamento.data_vencimento <= ultimo_dia).all()
    total_recebido       = sum(float(p.valor) for p in todos_periodo if p.status == 'pago')
    total_pendente       = sum(float(p.valor) for p in todos_periodo if p.status in ['pendente', 'vencido'])
    total_inadimplentes  = len(set(p.aluno_id for p in todos_periodo if p.status == 'vencido'))
    total_pagamentos_mes = len([p for p in todos_periodo if p.status == 'pago'])
    total_registros      = len(todos_periodo)

    # Formas de pagamento ativas e taxas
    formas_ativas = {
        'dinheiro': get_param('forma_dinheiro', '1') == '1',
        'pix':      get_param('forma_pix',      '1') == '1',
        'credito':  get_param('forma_credito',  '0') == '1',
        'debito':   get_param('forma_debito',   '0') == '1',
        'boleto':   get_param('forma_boleto',   '0') == '1',
    }
    taxas = {
        'credito': float(get_param('taxa_credito', '0')),
        'debito':  float(get_param('taxa_debito',  '0')),
    }

    return render_template('financeiro.html',
                           pagamentos=pagamentos, inadimplentes=inadimplentes,
                           total_recebido=total_recebido, total_pendente=total_pendente,
                           total_inadimplentes=total_inadimplentes,
                           total_pagamentos_mes=total_pagamentos_mes,
                           total_registros=total_registros,
                           alunos_ativos=Aluno.query.filter_by(ativo=True).order_by(Aluno.nome).all(),
                           planos_valores={p.nome: float(p.valor) for p in Plano.query.all()},
                           mes_atual=mes_atual, status_filtro=status_filtro,
                           periodo_ativo=periodo_ativo,
                           data_inicio=data_inicio, data_fim=data_fim,
                           formas_ativas=formas_ativas, taxas=taxas,
                           hoje=hoje.strftime('%Y-%m-%d'))


def _redirect_financeiro():
    """Redireciona para /financeiro preservando o período e status ativos."""
    periodo = request.form.get('_periodo') or request.args.get('periodo', 'mes_atual')
    status  = request.form.get('_status')  or request.args.get('status',  'todos')
    inicio  = request.form.get('_inicio')  or request.args.get('inicio',  '')
    fim     = request.form.get('_fim')     or request.args.get('fim',     '')
    params  = f'periodo={periodo}&status={status}'
    if inicio and fim:
        params += f'&inicio={inicio}&fim={fim}'
    from flask import redirect
    return redirect(f'/financeiro?{params}')

@auth.route('/financeiro/registrar', methods=['POST'])
@login_required
def financeiro_registrar():
    from app.models import Pagamento
    aluno_id        = request.form.get('aluno_id')
    valor           = float(request.form.get('valor', 0))
    forma_pagamento = request.form.get('forma_pagamento')
    data_venc_str   = request.form.get('data_vencimento')
    data_pag_str    = request.form.get('data_pagamento')
    data_vencimento = datetime.strptime(data_venc_str, '%Y-%m-%d').date() if data_venc_str else date.today()
    data_pagamento  = datetime.strptime(data_pag_str,  '%Y-%m-%d').date() if data_pag_str  else None
    status = 'pago' if data_pagamento else ('vencido' if data_vencimento < date.today() else 'pendente')
    db.session.add(Pagamento(aluno_id=aluno_id, valor=valor, forma_pagamento=forma_pagamento,
                             status=status, data_vencimento=data_vencimento,
                             data_pagamento=data_pagamento,
                             observacao=request.form.get('observacao'),
                             criado_por=session.get('usuario')))
    db.session.commit()
    registrar_log('registrou pagamento', f'Aluno ID: {aluno_id} | Valor: R$ {valor}')
    flash('Pagamento registrado com sucesso!', 'success')
    return _redirect_financeiro()

@auth.route('/financeiro/<int:id>/pagar', methods=['POST'])
@login_required
def financeiro_pagar(id):
    from app.models import Pagamento
    pag = Pagamento.query.get_or_404(id)
    data_pag_str        = request.form.get('data_pagamento')
    pag.status          = 'pago'
    pag.forma_pagamento = request.form.get('forma_pagamento')
    pag.data_pagamento  = datetime.strptime(data_pag_str, '%Y-%m-%d').date() if data_pag_str else datetime.utcnow().date()
    obs = request.form.get('observacao')
    if obs: pag.observacao = obs
    db.session.commit()
    registrar_log('confirmou pagamento', f'Pagamento ID: {id} | Aluno: {pag.aluno.nome}')
    flash(f'Pagamento de {pag.aluno.nome} confirmado!', 'success')
    return _redirect_financeiro()

@auth.route('/financeiro/<int:id>/excluir')
@login_required
def financeiro_excluir(id):
    from app.models import Pagamento
    pag = Pagamento.query.get_or_404(id)
    nome = pag.aluno.nome
    db.session.delete(pag)
    db.session.commit()
    registrar_log('excluiu pagamento', f'Pagamento ID: {id} | Aluno: {nome}')
    flash('Pagamento excluído com sucesso!', 'success')
    return _redirect_financeiro()



@auth.route('/financeiro/relatorio-mensal')
@login_required
def financeiro_relatorio_mensal():
    from app.models import Pagamento, Plano
    from flask import Response
    import calendar, io

    inicio_str   = request.args.get('inicio', '')
    fim_str      = request.args.get('fim', '')
    mes          = request.args.get('mes', '')
    formato      = request.args.get('formato', 'pdf')
    secoes       = request.args.get('secoes', 'resumo,pagamentos,inadimplentes').split(',')
    colunas      = request.args.get('colunas', 'nome,plano,valor,vencimento,pagamento,forma,status').split(',')
    colunas_inad = request.args.get('colunas_inad', 'nome,plano,venc_plano,valor_plano').split(',')

    hoje    = date.today()
    nome_ac = get_param('nome_academia', 'Academia')

    if inicio_str and fim_str:
        try:
            primeiro_dia = datetime.strptime(inicio_str, '%Y-%m-%d').date()
            ultimo_dia   = datetime.strptime(fim_str,    '%Y-%m-%d').date()
        except:
            flash('Datas invalidas.', 'danger')
            return redirect(url_for('auth.financeiro'))
    elif mes:
        try:
            ano, m       = map(int, mes.split('-'))
            primeiro_dia = date(ano, m, 1)
            ultimo_dia   = date(ano, m, calendar.monthrange(ano, m)[1])
        except:
            flash('Mes invalido.', 'danger')
            return redirect(url_for('auth.financeiro'))
    else:
        flash('Informe o periodo do relatorio.', 'danger')
        return redirect(url_for('auth.financeiro'))

    meses_pt = ['Janeiro','Fevereiro','Marco','Abril','Maio','Junho',
                 'Julho','Agosto','Setembro','Outubro','Novembro','Dezembro']
    if primeiro_dia.month == ultimo_dia.month and primeiro_dia.year == ultimo_dia.year:
        nome_mes = meses_pt[primeiro_dia.month-1] + ' de ' + str(primeiro_dia.year)
    else:
        nome_mes = primeiro_dia.strftime('%d/%m/%Y') + ' a ' + ultimo_dia.strftime('%d/%m/%Y')

    # Atualiza vencidos
    for pag in Pagamento.query.filter_by(status='pendente').all():
        if pag.data_vencimento < hoje:
            pag.status = 'vencido'
    db.session.commit()

    # Dados
    todos_mes = Pagamento.query.filter(
        Pagamento.data_vencimento >= primeiro_dia,
        Pagamento.data_vencimento <= ultimo_dia
    ).order_by(Pagamento.data_vencimento).all()

    total_recebido = sum(float(p.valor) for p in todos_mes if p.status == 'pago')
    total_pendente = sum(float(p.valor) for p in todos_mes if p.status == 'pendente')
    total_vencido  = sum(float(p.valor) for p in todos_mes if p.status == 'vencido')
    total_geral    = total_recebido + total_pendente + total_vencido
    qtd_pago       = sum(1 for p in todos_mes if p.status == 'pago')
    qtd_pendente   = sum(1 for p in todos_mes if p.status == 'pendente')
    qtd_vencido    = sum(1 for p in todos_mes if p.status == 'vencido')
    qtd_dinheiro   = sum(1 for p in todos_mes if p.status == 'pago' and p.forma_pagamento == 'dinheiro')
    qtd_pix        = sum(1 for p in todos_mes if p.status == 'pago' and p.forma_pagamento == 'pix')
    qtd_credito    = sum(1 for p in todos_mes if p.status == 'pago' and p.forma_pagamento == 'credito')
    qtd_debito     = sum(1 for p in todos_mes if p.status == 'pago' and p.forma_pagamento == 'debito')
    qtd_boleto     = sum(1 for p in todos_mes if p.status == 'pago' and p.forma_pagamento == 'boleto')

    # Inadimplentes: ativos sem pagamento pago no mês
    alunos_pagos = {p.aluno_id for p in todos_mes if p.status == 'pago'}
    inadimplentes = Aluno.query.filter(
        Aluno.ativo == True,
        ~Aluno.id.in_(alunos_pagos)
    ).order_by(Aluno.nome).all()

    # Mapeamento de colunas
    col_map = {
        'nome'      : 'Nome',
        'cpf'       : 'CPF',
        'telefone'  : 'Telefone',
        'plano'     : 'Plano',
        'valor'     : 'Valor',
        'vencimento': 'Vencimento',
        'pagamento' : 'Data Pagamento',
        'forma'     : 'Forma',
        'status'    : 'Status',
        'observacao': 'Observação',
    }
    headers = [col_map[c] for c in colunas if c in col_map]

    def get_row(p):
        row = []
        for c in colunas:
            if c == 'nome':         row.append(p.aluno.nome)
            elif c == 'cpf':        row.append(p.aluno.documento if p.aluno.tipo_documento == 'cpf' else '—')
            elif c == 'telefone':   row.append(p.aluno.telefone or '—')
            elif c == 'plano':      row.append(p.aluno.plano or '—')
            elif c == 'valor':      row.append(f'R$ {float(p.valor):.2f}')
            elif c == 'vencimento': row.append(p.data_vencimento.strftime('%d/%m/%Y'))
            elif c == 'pagamento':  row.append(p.data_pagamento.strftime('%d/%m/%Y') if p.data_pagamento else '—')
            elif c == 'forma':      row.append(p.forma_pagamento or '—')
            elif c == 'status':     row.append(p.status.capitalize())
            elif c == 'observacao': row.append(p.observacao or '—')
        return row

    # ── CSV ────────────────────────────────────────────────────────────────
    if formato == 'csv':
        import csv
        output = io.StringIO()
        w = csv.writer(output)
        w.writerow([f'RELATÓRIO MENSAL — {nome_ac.upper()}'])
        w.writerow([f'Mês de referência: {nome_mes}'])
        w.writerow([f'Emitido em: {hoje.strftime("%d/%m/%Y")}'])
        w.writerow([])

        if 'resumo' in secoes:
            w.writerow(['=== RESUMO FINANCEIRO ==='])
            w.writerow(['Total recebido', f'R$ {total_recebido:.2f}'])
            w.writerow(['Total pendente', f'R$ {total_pendente:.2f}'])
            w.writerow(['Total vencido',  f'R$ {total_vencido:.2f}'])
            w.writerow(['Total geral',    f'R$ {total_geral:.2f}'])
            w.writerow([])
            w.writerow(['Pagamentos recebidos', qtd_pago])
            w.writerow(['Pagamentos pendentes', qtd_pendente])
            w.writerow(['Pagamentos vencidos',  qtd_vencido])
            if qtd_dinheiro: w.writerow(['Pago em Dinheiro',        qtd_dinheiro])
            if qtd_pix:      w.writerow(['Pago via PIX',             qtd_pix])
            if qtd_credito:  w.writerow(['Pago com Cartao Credito',  qtd_credito])
            if qtd_debito:   w.writerow(['Pago com Cartao Debito',   qtd_debito])
            if qtd_boleto:   w.writerow(['Pago via Boleto',          qtd_boleto])
            w.writerow(['Inadimplentes',    len(inadimplentes)])
            w.writerow([])

        if 'pagamentos' in secoes:
            w.writerow(['=== PAGAMENTOS DO MÊS ==='])
            w.writerow(headers)
            for p in todos_mes:
                w.writerow(get_row(p))
            w.writerow([])

        if 'inadimplentes' in secoes and inadimplentes:
            planos_v_csv = {p.nome: float(p.valor) for p in Plano.query.all()}
            col_inad_map_csv = {'nome': 'Nome','cpf': 'CPF','telefone': 'Telefone',
                                'plano': 'Plano','venc_plano': 'Venc. Plano','valor_plano': 'Valor Plano'}
            cols_i_csv = [c for c in colunas_inad if c in col_inad_map_csv] or ['nome','plano','venc_plano','valor_plano']
            w.writerow(['=== INADIMPLENTES ==='])
            w.writerow([col_inad_map_csv[c] for c in cols_i_csv])
            for a in inadimplentes:
                row = []
                for c in cols_i_csv:
                    if c == 'nome':        row.append(a.nome)
                    elif c == 'cpf':       row.append(a.documento if a.tipo_documento == 'cpf' else '—')
                    elif c == 'telefone':  row.append(a.telefone or '—')
                    elif c == 'plano':     row.append(a.plano or '—')
                    elif c == 'venc_plano': row.append(a.vencimento.strftime('%d/%m/%Y') if a.vencimento else '—')
                    elif c == 'valor_plano': row.append(f'R$ {planos_v_csv.get(a.plano, 0):.2f}')
                w.writerow(row)

        output.seek(0)
        return Response(
            '\ufeff' + output.getvalue(), mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename=relatorio_mensal_{mes}.csv'}
        )

    # ── PDF ────────────────────────────────────────────────────────────────
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                     Table, TableStyle, HRFlowable)
    from reportlab.pdfgen import canvas as pdfcanvas

    LARANJA = colors.HexColor(get_param('cor_principal', '#FF6B00'))
    SIDEBAR = colors.HexColor('#1a1a2e')
    CINZA   = colors.HexColor('#6c757d')
    VERDE   = colors.HexColor('#2e7d32')
    VERM    = colors.HexColor('#c62828')
    AMAREL  = colors.HexColor('#e65100')

    pw, ph  = landscape(A4)

    class MensalCanvas(pdfcanvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved = []
        def showPage(self):
            self._saved.append(dict(self.__dict__))
            self._startPage()
        def save(self):
            total = len(self._saved)
            for state in self._saved:
                self.__dict__.update(state)
                # Cabeçalho
                self.setFillColor(SIDEBAR)
                self.rect(0, ph - 1.2*cm, pw, 1.2*cm, fill=1, stroke=0)
                self.setFillColor(LARANJA)
                self.rect(0, ph - 1.2*cm, 0.5*cm, 1.2*cm, fill=1, stroke=0)
                self.setFont('Helvetica-Bold', 10)
                self.setFillColor(colors.white)
                self.drawString(0.8*cm, ph - 0.78*cm, nome_ac.upper())
                self.setFont('Helvetica', 9)
                self.drawString(0.8*cm, ph - 1.0*cm,
                    f'Relatório Mensal  |  {nome_mes}  |  Emitido em {hoje.strftime("%d/%m/%Y")}')
                self.setFont('Helvetica', 8)
                self.setFillColor(colors.HexColor('#aaaaaa'))
                self.drawRightString(pw - 1*cm, ph - 0.68*cm, 'Uso interno — Financeiro')
                # Rodapé
                self.setStrokeColor(colors.HexColor('#dee2e6'))
                self.setLineWidth(0.5)
                self.line(1*cm, 1.4*cm, pw - 1*cm, 1.4*cm)
                self.setFont('Helvetica', 8)
                self.setFillColor(CINZA)
                self.drawString(1*cm, 1.0*cm, 'Confidencial')
                self.drawRightString(pw - 1*cm, 1.0*cm,
                    f'Página {self._pageNumber} de {total}')
                super().showPage()
            super().save()

    styles = getSampleStyleSheet()
    def E(name, **kw):
        return ParagraphStyle(name, parent=styles['Normal'], **kw)

    st_h1   = E('h1',  fontSize=16, fontName='Helvetica-Bold', textColor=SIDEBAR,   spaceAfter=4)
    st_h2   = E('h2',  fontSize=11, fontName='Helvetica-Bold', textColor=SIDEBAR,   spaceAfter=6)
    st_body = E('body',fontSize=9,  textColor=colors.HexColor('#333'), leading=13)
    st_sm   = E('sm',  fontSize=8,  textColor=CINZA, leading=11)

    buffer   = io.BytesIO()
    doc      = SimpleDocTemplate(buffer, pagesize=landscape(A4),
                                  leftMargin=1*cm, rightMargin=1*cm,
                                  topMargin=1.6*cm, bottomMargin=1.8*cm)
    els = []

    def linha_sep():
        return HRFlowable(width='100%', thickness=0.5, color=colors.HexColor('#dee2e6'),
                          spaceBefore=8, spaceAfter=8)

    def tbl_style(data, col_widths=None, totais_rows=None):
        t = Table(data, colWidths=col_widths, repeatRows=1)
        cmds = [
            ('BACKGROUND',    (0,0),(-1,0),  SIDEBAR),
            ('TEXTCOLOR',     (0,0),(-1,0),  colors.white),
            ('FONTNAME',      (0,0),(-1,0),  'Helvetica-Bold'),
            ('FONTSIZE',      (0,0),(-1,0),  8),
            ('FONTSIZE',      (0,1),(-1,-1), 8),
            ('ROWBACKGROUNDS',(0,1),(-1,-1), [colors.white, colors.HexColor('#f8f9fa')]),
            ('GRID',          (0,0),(-1,-1), 0.3, colors.HexColor('#dee2e6')),
            ('PADDING',       (0,0),(-1,-1), 5),
            ('VALIGN',        (0,0),(-1,-1), 'MIDDLE'),
        ]
        if totais_rows:
            for r in totais_rows:
                cmds += [
                    ('BACKGROUND', (0,r),(-1,r), colors.HexColor('#f0f4f8')),
                    ('FONTNAME',   (0,r),(-1,r), 'Helvetica-Bold'),
                ]
        t.setStyle(TableStyle(cmds))
        return t

    # ── Seção: Resumo ────────────────────────────────────────────────────
    if 'resumo' in secoes:
        els.append(Paragraph('Resumo Financeiro', st_h1))
        els.append(linha_sep())

        # Cards de resumo em tabela 2 linhas × 3 cols
        def card(titulo, valor, cor):
            return Table([
                [Paragraph(titulo, E('ct', fontSize=8, textColor=CINZA))],
                [Paragraph(valor,  E('cv', fontSize=16, fontName='Helvetica-Bold', textColor=cor))],
            ], colWidths=[(pw - 2*cm) / 3 - 0.3*cm],
               style=[('BACKGROUND',(0,0),(-1,-1),colors.white),
                      ('BOX',(0,0),(-1,-1),0.5,colors.HexColor('#dee2e6')),
                      ('TOPPADDING',(0,0),(-1,-1),10),
                      ('BOTTOMPADDING',(0,0),(-1,-1),10),
                      ('LEFTPADDING',(0,0),(-1,-1),14),
                      ('RIGHTPADDING',(0,0),(-1,-1),14)])

        row1 = Table([[
            card('Total Recebido',  f'R$ {total_recebido:.2f}', VERDE),
            card('Total Pendente',  f'R$ {total_pendente:.2f}', AMAREL),
            card('Total Vencido',   f'R$ {total_vencido:.2f}',  VERM),
        ]], colWidths=[(pw-2*cm)/3]*3,
            style=[('LEFTPADDING',(0,0),(-1,-1),3),('RIGHTPADDING',(0,0),(-1,-1),3)])
        els.append(row1)
        els.append(Spacer(1, 0.3*cm))

        row2 = Table([[
            card('Total Geral',      f'R$ {total_geral:.2f}',    SIDEBAR),
            card('Pagos (Dinheiro)', str(qtd_dinheiro),          CINZA),
            card('Pagos (PIX)',      str(qtd_pix),               CINZA),
        ]], colWidths=[(pw-2*cm)/3]*3,
            style=[('LEFTPADDING',(0,0),(-1,-1),3),('RIGHTPADDING',(0,0),(-1,-1),3)])
        els.append(row2)
        els.append(Spacer(1, 0.3*cm))

        # Row3 — cartão crédito, débito e boleto (só se houver)
        cards_extra = []
        if qtd_credito > 0: cards_extra.append(card('Pagos (Credito)', str(qtd_credito), CINZA))
        if qtd_debito  > 0: cards_extra.append(card('Pagos (Debito)',  str(qtd_debito),  CINZA))
        if qtd_boleto  > 0: cards_extra.append(card('Pagos (Boleto)',  str(qtd_boleto),  CINZA))
        if cards_extra:
            # Completa com células vazias para manter 3 colunas
            while len(cards_extra) < 3:
                cards_extra.append(Spacer(1, 1))
            row3 = Table([cards_extra], colWidths=[(pw-2*cm)/3]*3,
                         style=[('LEFTPADDING',(0,0),(-1,-1),3),('RIGHTPADDING',(0,0),(-1,-1),3)])
            els.append(row3)
            els.append(Spacer(1, 0.3*cm))

        # Tabela de contagens
        data_cont = [['Forma de pagamento / Status', 'Quantidade']]
        data_cont += [['Pagamentos recebidos', str(qtd_pago)],
                      ['Pagamentos pendentes', str(qtd_pendente)],
                      ['Pagamentos vencidos',  str(qtd_vencido)],
                      ['Inadimplentes (sem pagamento)', str(len(inadimplentes))]]
        # Formas ativas com pelo menos 1 pagamento
        formas_labels = {'dinheiro': 'Dinheiro', 'pix': 'PIX',
                         'credito': 'Cartao de Credito', 'debito': 'Cartao de Debito', 'boleto': 'Boleto'}
        for forma_key, forma_label in formas_labels.items():
            qtd_f = sum(1 for p in todos_mes if p.status == 'pago' and p.forma_pagamento == forma_key)
            if qtd_f > 0:
                data_cont.append([f'Pagos via {forma_label}', str(qtd_f)])
        els.append(tbl_style(data_cont, col_widths=[10*cm, 4*cm]))
        els.append(Spacer(1, 0.5*cm))

    # ── Seção: Pagamentos ─────────────────────────────────────────────────
    if 'pagamentos' in secoes:
        els.append(Paragraph(f'Pagamentos do Mês — {nome_mes}', st_h1))
        els.append(linha_sep())
        data = [headers]
        for p in todos_mes:
            data.append(get_row(p))
        # Totais
        data.append([''] * len(headers))
        if 'valor' in colunas:
            idx_v = colunas.index('valor')
            tot_row = [''] * len(headers)
            tot_row[0] = 'Total recebido'
            tot_row[idx_v] = f'R$ {total_recebido:.2f}'
            data.append(tot_row)
        if len(data) > 1:
            n_colunas = len(headers)
            cw = (pw - 2*cm) / n_colunas
            els.append(tbl_style(data, col_widths=[cw]*n_colunas,
                                  totais_rows=[len(data)-1]))
        else:
            els.append(Paragraph('Nenhum pagamento registrado no período selecionado.', st_body))
        els.append(Spacer(1, 0.5*cm))

    # ── Seção: Inadimplentes ──────────────────────────────────────────────
    if 'inadimplentes' in secoes:
        els.append(Paragraph(f'Inadimplentes — {nome_mes}', st_h1))
        els.append(linha_sep())
        if inadimplentes:
            planos_v = {p.nome: float(p.valor) for p in Plano.query.all()}
            col_inad_map = {
                'nome'      : 'Nome',
                'cpf'       : 'CPF',
                'telefone'  : 'Telefone',
                'plano'     : 'Plano',
                'venc_plano': 'Vencimento do Plano',
                'valor_plano': 'Valor do Plano',
            }
            # Se colunas_inad veio vazio usa padrão
            cols_i = [c for c in colunas_inad if c in col_inad_map] or ['nome', 'plano', 'venc_plano', 'valor_plano']
            headers_i = [col_inad_map[c] for c in cols_i]
            data_in = [headers_i]
            for a in inadimplentes:
                row = []
                for c in cols_i:
                    if c == 'nome':       row.append(a.nome)
                    elif c == 'cpf':      row.append(a.documento if a.tipo_documento == 'cpf' else '—')
                    elif c == 'telefone': row.append(a.telefone or '—')
                    elif c == 'plano':    row.append(a.plano or '—')
                    elif c == 'venc_plano': row.append(a.vencimento.strftime('%d/%m/%Y') if a.vencimento else '—')
                    elif c == 'valor_plano': row.append(f'R$ {planos_v.get(a.plano, 0):.2f}')
                data_in.append(row)
            # Linha de total
            total_inadimp = sum(planos_v.get(a.plano, 0) for a in inadimplentes)
            tot_row = [''] * len(cols_i)
            tot_row[0] = f'Total: {len(inadimplentes)} aluno(s)'
            if 'valor_plano' in cols_i:
                tot_row[cols_i.index('valor_plano')] = f'R$ {total_inadimp:.2f}'
            data_in.append(tot_row)
            n_i = len(cols_i)
            cw_i = (pw - 2*cm) / n_i
            els.append(tbl_style(data_in, col_widths=[cw_i]*n_i,
                                  totais_rows=[len(data_in)-1]))
        else:
            els.append(Paragraph('Nenhum inadimplente no período.', st_body))
        els.append(Spacer(1, 0.5*cm))

    doc.build(els, canvasmaker=MensalCanvas)
    buffer.seek(0)
    return Response(buffer.getvalue(), mimetype='application/pdf',
        headers={'Content-Disposition': f'attachment; filename=relatorio_mensal_{mes}.pdf'})


@auth.route('/logout')
def logout():
    registrar_log('logout', f'Usuário: {session.get("usuario")}')
    session.clear()
    flash('Você saiu do sistema.', 'info')
    return redirect(url_for('auth.login'))