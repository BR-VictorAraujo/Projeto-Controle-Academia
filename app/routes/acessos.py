from flask import Blueprint, render_template, redirect, url_for, request, flash, session
from flask import jsonify
from app import db
from app.models import Aluno, RegistroAcesso
from datetime import datetime, date, timedelta
import json
from app.routes.auth import login_required, registrar_log, get_param

bp = Blueprint('acessos', __name__)

def _filtro_periodo(dt_inicio, dt_fim):
    fuso = int(get_param('fuso_horario', '-3'))
    inicio_utc = datetime(dt_inicio.year, dt_inicio.month, dt_inicio.day) - timedelta(hours=fuso)
    fim_utc    = datetime(dt_fim.year,    dt_fim.month,    dt_fim.day)    - timedelta(hours=fuso) + timedelta(days=1)
    return inicio_utc, fim_utc

def _hoje_local():
    """Retorna a data de hoje no fuso horário configurado."""
    fuso = int(get_param('fuso_horario', '-3'))
    return (datetime.utcnow() + timedelta(hours=fuso)).date()

def verificar_diaria_aluno(aluno):
    """
    Verifica se o aluno é de plano diário e se o plano venceu.
    Se venceu, inativa o aluno e retorna True (bloqueado).
    """
    from app.models import Plano
    if not aluno or not aluno.ativo:
        return True  # já inativo, bloqueia
    plano = Plano.query.filter_by(nome=aluno.plano, ativo=True).first()
    if plano and plano.dias_validade == 1:
        hoje = date.today()
        if aluno.vencimento and aluno.vencimento < hoje:
            aluno.ativo = False
            db.session.commit()
            return True  # inativou e bloqueia
    return False  # não é diária ou ainda válido

@bp.route('/acessos')
@login_required
def acessos():
    hoje_local = _hoje_local()
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

@bp.route('/acessos/biometria', methods=['POST'])
def acesso_biometria():
    # Sem @login_required — chamado internamente pelo app biométrico via localhost
    data     = request.get_json(silent=True) or {}
    aluno_id = data.get('aluno_id')
    if not aluno_id:
        return jsonify({'erro': 'aluno_id obrigatorio'}), 400
    try:
        aluno = Aluno.query.get(aluno_id)
        if not aluno:
            return jsonify({'erro': 'Aluno nao encontrado'}), 404

        # Verifica e inativa diária vencida
        if verificar_diaria_aluno(aluno):
            return jsonify({'erro': 'Plano diário vencido', 'bloqueado': True, 'inativado': True}), 403

        hoje = date.today()
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

@bp.route('/acessos/registrar', methods=['POST'])
@login_required
def registrar_acesso():
    aluno_id   = request.form.get('aluno_id')
    observacao = request.form.get('observacao')
    aluno      = Aluno.query.get_or_404(aluno_id)
    hoje       = date.today()

    # Verifica e inativa diária vencida
    if verificar_diaria_aluno(aluno):
        flash(f'Acesso bloqueado! O plano diário de {aluno.nome} venceu e o aluno foi inativado automaticamente.', 'danger')
        return redirect(url_for('acessos.acessos'))

    bloquear = get_param('bloquear_plano_vencido', '0') == '1'
    if bloquear and aluno.vencimento and aluno.vencimento < hoje:
        flash(f'Acesso bloqueado! O plano de {aluno.nome} está vencido desde {aluno.vencimento.strftime("%d/%m/%Y")}.', 'danger')
        return redirect(url_for('acessos.acessos'))
    if not bloquear and aluno.vencimento and aluno.vencimento < hoje:
        flash(f'Atenção! O plano de {aluno.nome} está vencido desde {aluno.vencimento.strftime("%d/%m/%Y")}.', 'warning')

    db.session.add(RegistroAcesso(aluno_id=aluno_id, tipo='manual', observacao=observacao))
    db.session.commit()
    registrar_log('registrou acesso', f'Aluno: {aluno.nome} | Tipo: manual')
    flash(f'Acesso de {aluno.nome} registrado com sucesso!', 'success')
    return redirect(url_for('acessos.acessos'))

@bp.route('/monitoramento')
@login_required
def monitoramento():
    hoje_local       = _hoje_local()
    inicio_utc, fim_utc = _filtro_periodo(hoje_local, hoje_local)
    acessos_recentes = RegistroAcesso.query.filter(
        RegistroAcesso.entrada_em >= inicio_utc,
        RegistroAcesso.entrada_em <  fim_utc
    ).order_by(RegistroAcesso.entrada_em.desc()).limit(10).all()
    # Usa uma única query para contar totais
    todos_hoje      = RegistroAcesso.query.filter(
        RegistroAcesso.entrada_em >= inicio_utc,
        RegistroAcesso.entrada_em <  fim_utc
    ).all()
    total_hoje      = len(todos_hoje)
    total_biometria = sum(1 for a in todos_hoje if a.tipo == 'biometria')
    total_manual    = sum(1 for a in todos_hoje if a.tipo == 'manual')
    ultimo          = RegistroAcesso.query.order_by(RegistroAcesso.entrada_em.desc()).first()
    return render_template('monitoramento.html', acessos_recentes=acessos_recentes,
                           total_hoje=total_hoje, total_biometria=total_biometria,
                           total_manual=total_manual, ultimo_id=ultimo.id if ultimo else 0,
                           hoje=hoje_local)

@bp.route('/monitoramento/ultimo')
@login_required
def monitoramento_ultimo():
    acesso = RegistroAcesso.query.order_by(RegistroAcesso.entrada_em.desc()).first()
    if not acesso:
        return json.dumps({'id': 0})
    fuso           = int(get_param('fuso_horario', '-3'))
    hoje           = _hoje_local()
    vencido        = False
    vencimento_str = '—'
    if acesso.aluno.vencimento:
        vencido        = acesso.aluno.vencimento < hoje
        vencimento_str = acesso.aluno.vencimento.strftime('%d/%m/%Y')
    hora_local = (acesso.entrada_em + timedelta(hours=fuso)).strftime('%H:%M')
    cadastro   = acesso.aluno.criado_em.strftime('%d/%m/%Y') if acesso.aluno.criado_em else '—'
    return json.dumps({
        'id': acesso.id, 'nome': acesso.aluno.nome,
        'plano': acesso.aluno.plano or 'Sem plano',
        'hora': hora_local, 'vencido': vencido, 'vencimento': vencimento_str,
        'foto': acesso.aluno.foto or '', 'cpf': acesso.aluno.documento or '—',
        'telefone': acesso.aluno.telefone or '—', 'cadastro': cadastro,
        'tipo': acesso.tipo or 'manual',
    })

@bp.route('/monitoramento/recentes')
@login_required
def monitoramento_recentes():
    fuso           = int(get_param('fuso_horario', '-3'))
    hoje           = _hoje_local()
    inicio_utc, fim_utc = _filtro_periodo(hoje, hoje)
    acessos = RegistroAcesso.query.filter(
        RegistroAcesso.entrada_em >= inicio_utc,
        RegistroAcesso.entrada_em <  fim_utc
    ).order_by(RegistroAcesso.entrada_em.desc()).limit(10).all()
    return json.dumps([{
        'nome': a.aluno.nome,
        'hora': (a.entrada_em + timedelta(hours=fuso)).strftime('%H:%M'),
        'plano': a.aluno.plano or 'Sem plano',
        'vencido': a.aluno.vencimento < hoje if a.aluno.vencimento else False
    } for a in acessos])

@bp.route('/monitoramento/contadores')
@login_required
def monitoramento_contadores():
    hoje           = _hoje_local()
    inicio_utc, fim_utc = _filtro_periodo(hoje, hoje)
    todos = RegistroAcesso.query.filter(
        RegistroAcesso.entrada_em >= inicio_utc,
        RegistroAcesso.entrada_em <  fim_utc
    ).all()
    return json.dumps({
        'total':     len(todos),
        'biometria': sum(1 for a in todos if a.tipo == 'biometria'),
        'manual':    sum(1 for a in todos if a.tipo == 'manual'),
    })