from flask import Blueprint, render_template, redirect, url_for, request, flash, session
from functools import wraps
from app import db
from app.models import Aluno, LogAuditoria, RegistroAcesso
from datetime import datetime, date, timedelta
import re

bp = Blueprint('auth', __name__)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers compartilhados — importados pelos outros módulos
# ─────────────────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logado'):
            flash('Faça login para acessar o sistema.', 'warning')
            return redirect(url_for('auth.login'))
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

def get_param(chave, padrao=''):
    from app.models import Configuracao
    c = Configuracao.query.filter_by(chave=chave).first()
    return c.valor if c else padrao

# Opções de itens por página aceitas em todo o sistema — usado tanto para
# validar o valor recebido via query string quanto para popular o seletor
# na interface (Alunos, Financeiro, e futuras telas que precisem paginar).
OPCOES_POR_PAGINA = [20, 50, 100]
POR_PAGINA_PADRAO = 20

def paginar(query, args=None, padrao=POR_PAGINA_PADRAO, opcoes=None):
    """
    Pagina uma query do SQLAlchemy de forma padronizada em todo o sistema.

    Lê 'pagina' e 'por_pagina' da query string (via request.args, ou do
    dict passado em `args` se a tela precisar combinar com outros filtros
    já extraídos manualmente). Usa LIMIT/OFFSET no banco — nunca carrega
    a query inteira na memória antes de paginar, o que é essencial para
    telas como Alunos e Financeiro que podem crescer para milhares de
    registros.

    Retorna um objeto com:
        .items        — lista de registros da página atual
        .page         — página atual (1-indexed)
        .per_page     — itens por página nesta consulta
        .total        — total de registros (sem paginação)
        .pages        — total de páginas
        .has_prev / .has_next, .prev_num / .next_num

    `opcoes`: lista de valores permitidos para por_pagina (default
    OPCOES_POR_PAGINA = [20, 50, 100]). Qualquer valor fora da lista cai
    no padrão — protege contra alguém forçar ?por_pagina=999999 na URL
    e arrastar a base inteira de uma vez.
    """
    opcoes = opcoes or OPCOES_POR_PAGINA
    origem = args if args is not None else request.args

    try:
        pagina = int(origem.get('pagina', 1))
    except (TypeError, ValueError):
        pagina = 1
    if pagina < 1:
        pagina = 1

    try:
        por_pagina = int(origem.get('por_pagina', padrao))
    except (TypeError, ValueError):
        por_pagina = padrao
    if por_pagina not in opcoes:
        por_pagina = padrao

    return query.paginate(page=pagina, per_page=por_pagina, error_out=False)

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

# ─────────────────────────────────────────────────────────────────────────────
# Rotas
# ─────────────────────────────────────────────────────────────────────────────

@bp.route('/')
def index():
    return redirect(url_for('auth.login'))

@bp.route('/login', methods=['GET', 'POST'])
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

@bp.route('/logout')
def logout():
    registrar_log('logout', f'Usuário: {session.get("usuario")}')
    session.clear()
    flash('Você saiu do sistema.', 'info')
    return redirect(url_for('auth.login'))

@bp.route('/dashboard')
@login_required
def dashboard():
    from app.routes.financeiro import corrigir_vencimentos_desatualizados
    # Usa fuso horário configurado para data local correta
    fuso        = int(get_param('fuso_horario', '-3'))
    hoje        = (datetime.utcnow() + timedelta(hours=fuso)).date()
    dias_alerta = int(get_param('dias_alerta_vencimento', '7'))
    alerta_dias = hoje + timedelta(days=dias_alerta)

    # Corrige vencimentos desatualizados ao abrir o dashboard também
    corrigir_vencimentos_desatualizados()

    total_alunos  = Aluno.query.count()
    alunos_ativos = Aluno.query.filter_by(ativo=True).count()
    # Vencimentos próximos: entre hoje e alerta_dias (excluindo já vencidos)
    vencendo      = Aluno.query.filter(
        Aluno.ativo == True,
        Aluno.vencimento >= hoje,
        Aluno.vencimento <= alerta_dias
    ).count()

    # Acessos hoje usando filtro de período com fuso
    inicio_utc = datetime(hoje.year, hoje.month, hoje.day) - timedelta(hours=fuso)
    fim_utc    = inicio_utc + timedelta(days=1)
    acessos_hoje = RegistroAcesso.query.filter(
        RegistroAcesso.entrada_em >= inicio_utc,
        RegistroAcesso.entrada_em <  fim_utc
    ).count()

    ultimos       = Aluno.query.order_by(Aluno.criado_em.desc()).limit(5).all()

    alertar_plano      = get_param('alertar_plano_vencimento', '1') == '1'
    alertas_vencimento = Aluno.query.filter(
        Aluno.ativo == True,
        Aluno.vencimento >= hoje,
        Aluno.vencimento <= alerta_dias
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