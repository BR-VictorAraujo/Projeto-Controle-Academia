from flask import Blueprint, render_template, redirect, url_for, request, flash, session, Response
from app import db
from app.models import Aluno
from datetime import datetime, date, timedelta
from dateutil.relativedelta import relativedelta
from app.routes.auth import login_required, registrar_log, get_param, paginar, OPCOES_POR_PAGINA

bp = Blueprint('financeiro', __name__)

def _redirect_financeiro():
    periodo = request.form.get('_periodo') or request.args.get('periodo', 'mes_atual')
    status  = request.form.get('_status')  or request.args.get('status',  'todos')
    inicio  = request.form.get('_inicio')  or request.args.get('inicio',  '')
    fim     = request.form.get('_fim')     or request.args.get('fim',     '')
    params  = f'periodo={periodo}&status={status}'
    if inicio and fim:
        params += f'&inicio={inicio}&fim={fim}'
    return redirect(f'/financeiro?{params}')

def verificar_diarias_vencidas():
    """Inativa automaticamente alunos de plano diário com vencimento anterior a hoje."""
    from app.models import Plano
    hoje = date.today()
    planos_diaria = [p.nome for p in Plano.query.filter_by(dias_validade=1, ativo=True).all()]
    if not planos_diaria:
        return
    alunos = Aluno.query.filter(
        Aluno.ativo == True,
        Aluno.plano.in_(planos_diaria),
        Aluno.vencimento < hoje
    ).all()
    for aluno in alunos:
        aluno.ativo = False
    if alunos:
        db.session.commit()


def corrigir_vencimentos_desatualizados():
    """
    Rotina automática: corrige vencimentos de alunos ativos cujo vencimento
    está no passado E que possuem ao menos um pagamento pago registrado.
    Alunos sem nenhum pagamento NÃO são alterados.
    """
    from app.models import Pagamento, Plano
    hoje = date.today()

    # Alunos ativos com vencimento no passado (exceto diárias)
    planos_diaria = [p.nome for p in Plano.query.filter_by(dias_validade=1, ativo=True).all()]
    q = Aluno.query.filter(Aluno.ativo == True, Aluno.vencimento < hoje)
    if planos_diaria:
        q = q.filter(~Aluno.plano.in_(planos_diaria))
    alunos = q.all()

    atualizados = 0
    for aluno in alunos:
        plano = Plano.query.filter_by(nome=aluno.plano, ativo=True).first()
        if not plano:
            continue

        # Só corrige se tiver ao menos um pagamento pago registrado
        tem_pagamento = Pagamento.query.filter(
            Pagamento.aluno_id == aluno.id,
            Pagamento.status == 'pago'
        ).first()
        if not tem_pagamento:
            continue

        # Calcula o vencimento correto a partir do vencimento cadastrado
        novo_venc = _calcular_novo_vencimento(aluno.vencimento, plano.dias_validade, hoje)

        # Só atualiza se o novo vencimento for diferente do atual
        if novo_venc != aluno.vencimento:
            aluno.vencimento = novo_venc
            atualizados += 1

    if atualizados:
        db.session.commit()

def _calcular_novo_vencimento(venc_atual, dias_plano, hoje):
    """Calcula o próximo vencimento mantendo o dia do mês."""
    if dias_plano == 1:
        return hoje + relativedelta(days=1)
    elif dias_plano <= 31:
        meses = 1
    elif dias_plano <= 92:
        meses = 3
    elif dias_plano <= 185:
        meses = 6
    else:
        meses = 12
    base = venc_atual if venc_atual else hoje
    novo = base + relativedelta(months=meses)
    while novo <= hoje:
        novo += relativedelta(months=meses)
    return novo

@bp.route('/financeiro')
@login_required
def financeiro():
    from app.models import Pagamento, Plano
    import calendar

    # Regra de negócio: inativa diárias vencidas
    verificar_diarias_vencidas()
    # Corrige vencimentos desatualizados automaticamente
    corrigir_vencimentos_desatualizados()

    hoje          = date.today()
    status_filtro = request.args.get('status', 'todos')
    periodo_ativo = request.args.get('periodo', 'mes_atual')

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
    elif periodo_ativo == 'todo_periodo':
        # Busca a data do primeiro pagamento PAGO registrado (por data_pagamento)
        from app.models import Pagamento as PagTodo
        primeiro_pag = db.session.query(db.func.min(PagTodo.data_pagamento)).filter(
            PagTodo.status == 'pago',
            PagTodo.data_pagamento != None
        ).scalar()
        primeiro_dia = primeiro_pag if primeiro_pag else date(hoje.year, 1, 1)
        ultimo_dia   = hoje
    elif periodo_ativo == 'personalizado':
        try:
            primeiro_dia = datetime.strptime(request.args.get('inicio', hoje.strftime('%Y-%m-%d')), '%Y-%m-%d').date()
            ultimo_dia   = datetime.strptime(request.args.get('fim',    hoje.strftime('%Y-%m-%d')), '%Y-%m-%d').date()
        except:
            primeiro_dia = date(hoje.year, hoje.month, 1)
            ultimo_dia   = date(hoje.year, hoje.month, calendar.monthrange(hoje.year, hoje.month)[1])
    else:
        periodo_ativo = 'mes_atual'
        primeiro_dia  = date(hoje.year, hoje.month, 1)
        ultimo_dia    = date(hoje.year, hoje.month, calendar.monthrange(hoje.year, hoje.month)[1])

    data_inicio = primeiro_dia.strftime('%Y-%m-%d')
    data_fim    = ultimo_dia.strftime('%Y-%m-%d')

    # Atualiza status de pagamentos vencidos (apenas planos não-diária)
    planos_diaria = [p.nome for p in Plano.query.filter_by(dias_validade=1, ativo=True).all()]

    inadimplentes = []
    pag_inad      = None
    pag_pagtos    = None
    if status_filtro == 'inadimplentes':
        # Para "todo o período", considera qualquer pagamento pago já registrado
        if periodo_ativo == 'todo_periodo':
            # No todo_periodo, inadimplente é quem tem vencimento passado e nunca pagou
            alunos_com_pagamento = db.session.query(Pagamento.aluno_id).filter(
                Pagamento.status == 'pago',
                Pagamento.data_pagamento != None
            ).scalar_subquery()
        else:
            alunos_com_pagamento = db.session.query(Pagamento.aluno_id).filter(
                Pagamento.status == 'pago',
                Pagamento.data_pagamento >= primeiro_dia,
                Pagamento.data_pagamento <= ultimo_dia
            ).scalar_subquery()
        # Inadimplentes: apenas alunos ativos, não-diária, com vencimento < hoje e sem pagamento no período
        query_inad = Aluno.query.filter(
            Aluno.ativo == True,
            Aluno.vencimento < hoje,
            ~Aluno.plano.in_(planos_diaria) if planos_diaria else True,
            ~Aluno.id.in_(alunos_com_pagamento)
        ).order_by(Aluno.nome)
        pag_inad      = paginar(query_inad)
        inadimplentes = pag_inad.items
        pagamentos    = []
    else:
        # Pagos: filtra por data_pagamento (quando o dinheiro entrou)
        # Pendentes: filtra por data_vencimento (quando vence)
        if status_filtro == 'pago':
            query = Pagamento.query.filter(
                Pagamento.status == 'pago',
                Pagamento.data_pagamento >= primeiro_dia,
                Pagamento.data_pagamento <= ultimo_dia
            )
        elif status_filtro == 'pendente':
            query = Pagamento.query.filter(
                Pagamento.status == 'pendente',
                Pagamento.data_vencimento >= primeiro_dia,
                Pagamento.data_vencimento <= ultimo_dia
            )
        else:
            # Todos: pagos por data_pagamento + pendentes por data_vencimento
            query = Pagamento.query.filter(
                db.or_(
                    db.and_(
                        Pagamento.status == 'pago',
                        Pagamento.data_pagamento >= primeiro_dia,
                        Pagamento.data_pagamento <= ultimo_dia
                    ),
                    db.and_(
                        Pagamento.status == 'pendente',
                        Pagamento.data_vencimento >= primeiro_dia,
                        Pagamento.data_vencimento <= ultimo_dia
                    )
                )
            )
        query = query.order_by(Pagamento.data_vencimento)
        pag_pagtos = paginar(query)
        pagamentos = pag_pagtos.items

    # Objeto de paginacao usado pelo template — unifica os dois casos
    # (inadimplentes ou pagamentos normais) num unico nome, ja que sao
    # mutuamente exclusivos na mesma renderizacao.
    pag = pag_inad if status_filtro == 'inadimplentes' else pag_pagtos

    # Totais: pagos por data_pagamento, pendentes por data_vencimento
    pagos_periodo     = Pagamento.query.filter(
        Pagamento.status == 'pago',
        Pagamento.data_pagamento >= primeiro_dia,
        Pagamento.data_pagamento <= ultimo_dia
    ).all()
    pendentes_periodo = Pagamento.query.filter(
        Pagamento.status == 'pendente',
        Pagamento.data_vencimento >= primeiro_dia,
        Pagamento.data_vencimento <= ultimo_dia
    ).all()
    todos_periodo        = pagos_periodo + pendentes_periodo
    total_recebido       = sum(float(p.valor) for p in pagos_periodo)
    total_pendente       = sum(float(p.valor) for p in pendentes_periodo)
    total_pagamentos_mes = len(pagos_periodo)
    total_registros      = len(todos_periodo)
    # Inadimplentes: alunos ativos não-diária sem pagamento pago no período
    _planos_diaria = [p.nome for p in Plano.query.filter_by(dias_validade=1, ativo=True).all()]
    _alunos_pagos  = {p.aluno_id for p in pagos_periodo}
    _q_inad = Aluno.query.filter(Aluno.ativo == True, Aluno.vencimento < hoje)
    if _planos_diaria:
        _q_inad = _q_inad.filter(~Aluno.plano.in_(_planos_diaria))
    if _alunos_pagos:
        _q_inad = _q_inad.filter(~Aluno.id.in_(list(_alunos_pagos)))
    total_inadimplentes = _q_inad.count()

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
                           pag=pag, opcoes_por_pagina=OPCOES_POR_PAGINA,
                           total_recebido=total_recebido, total_pendente=total_pendente,
                           total_inadimplentes=total_inadimplentes,
                           total_pagamentos_mes=total_pagamentos_mes,
                           total_registros=total_registros,
                           alunos_ativos=Aluno.query.filter_by(ativo=True).order_by(Aluno.nome).all(),
                           planos_valores={p.nome: float(p.valor) for p in Plano.query.all()},
                           planos_dias={p.nome: p.dias_validade for p in Plano.query.all()},
                           status_filtro=status_filtro, periodo_ativo=periodo_ativo,
                           data_inicio=data_inicio, data_fim=data_fim,
                           formas_ativas=formas_ativas, taxas=taxas,
                           hoje=hoje.strftime('%Y-%m-%d'))

@bp.route('/financeiro/registrar', methods=['POST'])
@login_required
def financeiro_registrar():
    from app.models import Pagamento, Plano
    aluno_id        = request.form.get('aluno_id')
    data_venc_str   = request.form.get('data_vencimento')
    data_pag_str    = request.form.get('data_pagamento')
    data_vencimento = datetime.strptime(data_venc_str, '%Y-%m-%d').date() if data_venc_str else date.today()
    data_pagamento  = datetime.strptime(data_pag_str,  '%Y-%m-%d').date() if data_pag_str  else None
    observacao      = request.form.get('observacao')
    dividido        = request.form.get('dividido') == '1'

    status_provisorio = 'pago' if data_pagamento else 'pendente'

    # CRITICO: conta pagamentos pagos com este mesmo vencimento ANTES de
    # inserir o(s) novo(s) registro(s) abaixo. Se contarmos depois do
    # db.session.add(), o autoflush do SQLAlchemy ja vai ter mandado o
    # INSERT para o banco assim que a query for executada — fazendo a
    # contagem incluir o proprio pagamento que acabamos de criar e,
    # erroneamente, concluir que "já existe pagamento com esse vencimento",
    # pulando a atualizacao de aluno.vencimento. Esse era o bug: pagamento
    # ficava registrado como pago, mas o vencimento do aluno nao avançava.
    pags_mesmo_venc_antes = 0
    if status_provisorio == 'pago':
        pags_mesmo_venc_antes = Pagamento.query.filter(
            Pagamento.aluno_id == int(aluno_id),
            Pagamento.status == 'pago',
            Pagamento.data_vencimento == data_vencimento
        ).count()

    if dividido:
        # Pagamento dividido: dois registros na mesma transação
        valor1  = float(request.form.get('valor1', 0))
        forma1  = request.form.get('forma1')
        banco1  = request.form.get('banco_pix1', '').strip()
        valor2  = float(request.form.get('valor2', 0))
        forma2  = request.form.get('forma2')
        banco2  = request.form.get('banco_pix2', '').strip()
        status  = status_provisorio

        db.session.add(Pagamento(
            aluno_id=aluno_id, valor=valor1, forma_pagamento=forma1,
            banco_pix=banco1 if forma1 == 'pix' else None,
            status=status, data_vencimento=data_vencimento,
            data_pagamento=data_pagamento,
            observacao=observacao, criado_por=session.get('usuario')
        ))
        db.session.add(Pagamento(
            aluno_id=aluno_id, valor=valor2, forma_pagamento=forma2,
            banco_pix=banco2 if forma2 == 'pix' else None,
            status=status, data_vencimento=data_vencimento,
            data_pagamento=data_pagamento,
            observacao=observacao, criado_por=session.get('usuario')
        ))
        valor_total = valor1 + valor2
    else:
        # Pagamento simples
        valor           = float(request.form.get('valor', 0))
        forma_pagamento = request.form.get('forma_pagamento')
        banco_pix       = request.form.get('banco_pix', '').strip()
        status          = status_provisorio
        valor_total     = valor

        db.session.add(Pagamento(
            aluno_id=aluno_id, valor=valor, forma_pagamento=forma_pagamento,
            banco_pix=banco_pix if forma_pagamento == 'pix' else None,
            status=status, data_vencimento=data_vencimento,
            data_pagamento=data_pagamento,
            observacao=observacao, criado_por=session.get('usuario')
        ))

    # Atualiza vencimento do aluno — apenas uma vez, independente de ser dividido
    if status == 'pago':
        aluno = Aluno.query.get(aluno_id)
        if aluno:
            plano = Plano.query.filter_by(nome=aluno.plano, ativo=True).first()
            if plano:
                hoje = date.today()
                if pags_mesmo_venc_antes == 0:
                    aluno.vencimento = _calcular_novo_vencimento(
                        aluno.vencimento, plano.dias_validade, hoje)
                if not aluno.ativo:
                    aluno.ativo = True

    db.session.commit()
    registrar_log('registrou pagamento', f'Aluno ID: {aluno_id} | Valor: R$ {valor_total}{"  (dividido)" if dividido else ""}')
    flash('Pagamento registrado com sucesso!', 'success')
    if status == 'pago':
        periodo = request.form.get('_periodo', 'mes_atual')
        inicio  = request.form.get('_inicio', '')
        fim     = request.form.get('_fim', '')
        params  = f'periodo={periodo}&status=pago'
        if inicio and fim:
            params += f'&inicio={inicio}&fim={fim}'
        return redirect(f'/financeiro?{params}')
    return _redirect_financeiro()

@bp.route('/financeiro/<int:id>/pagar', methods=['POST'])
@login_required
def financeiro_pagar(id):
    from app.models import Pagamento, Plano
    pag = Pagamento.query.get_or_404(id)
    data_pag_str        = request.form.get('data_pagamento')
    pag.status          = 'pago'
    pag.forma_pagamento = request.form.get('forma_pagamento')
    pag.banco_pix       = request.form.get('banco_pix', '').strip() if pag.forma_pagamento == 'pix' else None
    pag.data_pagamento  = datetime.strptime(data_pag_str, '%Y-%m-%d').date() if data_pag_str else date.today()
    obs = request.form.get('observacao')
    if obs:
        pag.observacao = obs

    aluno = pag.aluno
    plano = Plano.query.filter_by(nome=aluno.plano, ativo=True).first()
    if plano:
        hoje = date.today()
        base_pag = pag.data_pagamento or hoje
        # Proteção contra duplicidade: só atualiza vencimento se não houver
        # outro pagamento pago com a mesma data de vencimento
        from app.models import Pagamento as PagModel
        pags_mesmo_venc = PagModel.query.filter(
            PagModel.aluno_id == aluno.id,
            PagModel.status == 'pago',
            PagModel.data_vencimento == pag.data_vencimento,
            PagModel.id != id
        ).count()
        if pags_mesmo_venc == 0:
            aluno.vencimento = _calcular_novo_vencimento(
                aluno.vencimento, plano.dias_validade, hoje)
        # Reativa se estava inativo
        if not aluno.ativo:
            aluno.ativo = True

    db.session.commit()
    registrar_log('confirmou pagamento', f'Pagamento ID: {id} | Aluno: {pag.aluno.nome}')
    flash(f'Pagamento de {pag.aluno.nome} confirmado!', 'success')
    return _redirect_financeiro()

@bp.route('/financeiro/<int:id>/excluir')
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

# ── Relatório Diário ──────────────────────────────────────────────────────────


def _row(p, cols, tipo_pag=None):
    """Monta uma linha de dados baseada nas colunas selecionadas."""
    row = []
    for c in cols:
        if c == 'nome':           row.append(p.aluno.nome)
        elif c == 'data_pagamento': row.append(p.data_pagamento.strftime('%d/%m/%Y') if p.data_pagamento else '—')
        elif c == 'plano':        row.append(p.aluno.plano or '—')
        elif c == 'valor':        row.append(f'R$ {float(p.valor):.2f}')
        elif c == 'cpf':          row.append(p.aluno.documento if p.aluno.tipo_documento == 'cpf' else '—')
        elif c == 'telefone':     row.append(p.aluno.telefone or '—')
        elif c == 'banco':        row.append(p.banco_pix or '—')
        elif c == 'tipo':         row.append('Crédito' if p.forma_pagamento == 'credito' else 'Débito')
        else:                     row.append('—')
    return row

def _header(cols):
    labels = {'nome':'Nome','data_pagamento':'Data Pagamento','plano':'Plano','valor':'Valor',
              'cpf':'CPF','telefone':'Telefone','banco':'Banco','tipo':'Tipo','vencimento':'Vencimento'}
    return [labels.get(c, c.title()) for c in cols]

def _row_inad(a, cols, planos_v):
    row = []
    for c in cols:
        if c == 'nome':       row.append(a.nome)
        elif c == 'plano':    row.append(a.plano or '—')
        elif c == 'vencimento': row.append(a.vencimento.strftime('%d/%m/%Y') if a.vencimento else '—')
        elif c == 'valor':    row.append(f'R$ {planos_v.get(a.plano, 0):.2f}')
        elif c == 'cpf':      row.append(a.documento if a.tipo_documento == 'cpf' else '—')
        elif c == 'telefone': row.append(a.telefone or '—')
        else:                 row.append('—')
    return row

@bp.route('/financeiro/relatorio-diario')
@login_required
def financeiro_relatorio_diario():
    from app.models import Pagamento
    import io

    data_str       = request.args.get('data', '')
    hora_ini_str   = request.args.get('hora_ini', '')
    hora_fim_str   = request.args.get('hora_fim', '')
    formato        = request.args.get('formato', 'pdf')
    nome_ac        = get_param('nome_academia', 'Academia')
    secoes_diario  = request.args.get('secoes', 'd-dinheiro,d-pix,d-cartao').split(',')
    cols_dinheiro  = [c for c in request.args.get('cols_dinheiro', 'nome,data_pagamento,plano,valor').split(',') if c]
    cols_pix       = [c for c in request.args.get('cols_pix',      'nome,data_pagamento,plano,banco,valor').split(',') if c]
    cols_cartao    = [c for c in request.args.get('cols_cartao',   'nome,data_pagamento,plano,tipo,valor').split(',') if c]

    try:
        data_rel = datetime.strptime(data_str, '%Y-%m-%d').date() if data_str else date.today()
    except:
        data_rel = date.today()

    # Filtro por turno via criado_em (hora que o pagamento foi registrado)
    fuso = int(get_param('fuso_horario', '-3'))

    # Converte data local para UTC para comparar com criado_em (armazenado em UTC)
    inicio_dt = datetime(data_rel.year, data_rel.month, data_rel.day, 0, 0, 0) - timedelta(hours=fuso)
    fim_dt    = datetime(data_rel.year, data_rel.month, data_rel.day, 23, 59, 59) - timedelta(hours=fuso)

    # Aplica filtro de hora se informado
    turno_ativo = bool(hora_ini_str or hora_fim_str)
    if hora_ini_str:
        try:
            h, m = map(int, hora_ini_str.split(':'))
            inicio_dt = datetime(data_rel.year, data_rel.month, data_rel.day, h, m, 0) - timedelta(hours=fuso)
        except:
            pass
    if hora_fim_str:
        try:
            h, m = map(int, hora_fim_str.split(':'))
            fim_dt = datetime(data_rel.year, data_rel.month, data_rel.day, h, m, 59) - timedelta(hours=fuso)
        except:
            pass

    pagamentos = Pagamento.query.filter(
        Pagamento.data_pagamento == data_rel,
        Pagamento.status == 'pago',
        Pagamento.criado_em >= inicio_dt,
        Pagamento.criado_em <= fim_dt
    ).order_by(Pagamento.forma_pagamento, Pagamento.aluno_id).all()

    p_dinheiro = [p for p in pagamentos if p.forma_pagamento == 'dinheiro']
    p_pix      = [p for p in pagamentos if p.forma_pagamento == 'pix']
    p_credito  = [p for p in pagamentos if p.forma_pagamento == 'credito']
    p_debito   = [p for p in pagamentos if p.forma_pagamento == 'debito']
    p_cartao   = p_credito + p_debito

    total_dinheiro = sum(float(p.valor) for p in p_dinheiro)
    total_pix      = sum(float(p.valor) for p in p_pix)
    total_cartao   = sum(float(p.valor) for p in p_cartao)
    total_geral    = total_dinheiro + total_pix + total_cartao

    data_fmt   = data_rel.strftime('%d/%m/%Y')
    dia_semana = ['Segunda-feira','Terça-feira','Quarta-feira','Quinta-feira',
                  'Sexta-feira','Sábado','Domingo'][data_rel.weekday()]
    # Texto do turno para cabeçalho
    if turno_ativo:
        hora_ini_fmt = hora_ini_str or '00:00'
        hora_fim_fmt = hora_fim_str or '23:59'
        turno_fmt    = f'{hora_ini_fmt} às {hora_fim_fmt}'
    else:
        turno_fmt = None

    if formato == 'csv':
        import csv
        output = io.StringIO()
        w = csv.writer(output)
        w.writerow([f'RELATÓRIO DIÁRIO — {nome_ac.upper()}'])
        w.writerow([f'Data: {dia_semana}, {data_fmt}'])
        if turno_fmt: w.writerow([f'Turno: {turno_fmt}'])
        w.writerow([f'Emitido em: {date.today().strftime("%d/%m/%Y")}'])
        w.writerow([])
        w.writerow(['=== RESUMO DO DIA ==='])
        w.writerow(['Forma', 'Qtd Alunos', 'Total'])
        w.writerow(['Dinheiro', len(p_dinheiro), f'R$ {total_dinheiro:.2f}'])
        w.writerow(['PIX',      len(p_pix),      f'R$ {total_pix:.2f}'])
        w.writerow(['Cartão',   len(p_cartao),   f'R$ {total_cartao:.2f}'])
        w.writerow(['TOTAL GERAL', len(pagamentos), f'R$ {total_geral:.2f}'])
        w.writerow([])
        if 'd-dinheiro' in secoes_diario and p_dinheiro:
            w.writerow(['=== DINHEIRO ==='])
            w.writerow(['#'] + _header(cols_dinheiro))
            for i, p in enumerate(p_dinheiro, 1): w.writerow([i] + _row(p, cols_dinheiro))
            tot = [''] * len(cols_dinheiro)
            if 'valor' in cols_dinheiro: tot[cols_dinheiro.index('valor')] = f'R$ {total_dinheiro:.2f}'
            w.writerow(['TOTAL'] + tot)
            w.writerow([])
        if 'd-pix' in secoes_diario and p_pix:
            w.writerow(['=== PIX ==='])
            w.writerow(['#'] + _header(cols_pix))
            for i, p in enumerate(p_pix, 1): w.writerow([i] + _row(p, cols_pix))
            tot = [''] * len(cols_pix)
            if 'valor' in cols_pix: tot[cols_pix.index('valor')] = f'R$ {total_pix:.2f}'
            w.writerow(['TOTAL'] + tot)
            w.writerow([])
        if 'd-cartao' in secoes_diario and p_cartao:
            w.writerow(['=== CARTÃO ==='])
            w.writerow(['#'] + _header(cols_cartao))
            for i, p in enumerate(p_cartao, 1): w.writerow([i] + _row(p, cols_cartao))
            tot = [''] * len(cols_cartao)
            if 'valor' in cols_cartao: tot[cols_cartao.index('valor')] = f'R$ {total_cartao:.2f}'
            w.writerow(['TOTAL'] + tot)
        output.seek(0)
        return Response('\ufeff' + output.getvalue(), mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename=relatorio_diario_{data_rel}.csv'})

    # PDF
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    from reportlab.pdfgen import canvas as pdfcanvas

    LARANJA = colors.HexColor(get_param('cor_principal', '#FF6B00'))
    SIDEBAR = colors.HexColor('#1a1a2e')
    CINZA   = colors.HexColor('#6c757d')
    VERDE   = colors.HexColor('#2e7d32')
    pw, ph  = A4

    endereco_ac = get_param('endereco', '')
    telefone_ac = get_param('telefone', '')
    hora_emissao = datetime.now().strftime('%d/%m/%Y às %H:%M')

    class DiarCanvas(pdfcanvas.Canvas):
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
                self.rect(0, ph-1.6*cm, pw, 1.6*cm, fill=1, stroke=0)
                self.setFillColor(LARANJA)
                self.rect(0, ph-1.6*cm, 0.5*cm, 1.6*cm, fill=1, stroke=0)
                self.setFont('Helvetica-Bold', 11)
                self.setFillColor(colors.white)
                self.drawString(0.8*cm, ph-0.7*cm, nome_ac.upper())
                self.setFont('Helvetica', 8)
                self.setFillColor(colors.HexColor('#cccccc'))
                if endereco_ac:
                    self.drawString(0.8*cm, ph-1.0*cm, endereco_ac)
                info_linha = f'Relatório Diário  |  {dia_semana}, {data_fmt}'
                if turno_fmt:
                    info_linha += f'  |  Turno: {turno_fmt}'
                if telefone_ac:
                    info_linha += f'  |  Tel: {telefone_ac}'
                self.drawString(0.8*cm, ph-1.3*cm, info_linha)
                self.setFillColor(colors.HexColor('#aaaaaa'))
                self.drawRightString(pw-1*cm, ph-0.7*cm, f'Emitido em {hora_emissao}')
                self.drawRightString(pw-1*cm, ph-1.0*cm, f'Pág. {self._pageNumber} de {total}')
                # Rodapé
                self.setStrokeColor(colors.HexColor('#dee2e6'))
                self.setLineWidth(0.5)
                self.line(1*cm, 1.4*cm, pw-1*cm, 1.4*cm)
                self.setFont('Helvetica', 8)
                self.setFillColor(CINZA)
                self.drawString(1*cm, 1.0*cm, nome_ac + '  —  Uso interno')
                self.drawRightString(pw-1*cm, 1.0*cm, f'Página {self._pageNumber} de {total}')
                super().showPage()
            super().save()

    styles = getSampleStyleSheet()
    def E(name, **kw): return ParagraphStyle(name, parent=styles['Normal'], **kw)
    st_h1 = E('h1', fontSize=18, fontName='Helvetica-Bold', textColor=SIDEBAR, spaceAfter=10)
    st_h2 = E('h2', fontSize=12, fontName='Helvetica-Bold', textColor=SIDEBAR, spaceAfter=4, spaceBefore=14)
    st_body = E('body', fontSize=9, textColor=colors.HexColor('#333'), leading=13)

    def linha(): return HRFlowable(width='100%', thickness=0.5, color=colors.HexColor('#dee2e6'), spaceBefore=6, spaceAfter=6)

    def tabela(data, col_widths=None, totais=[]):
        t = Table(data, colWidths=col_widths, repeatRows=1)
        cmds = [
            ('BACKGROUND',(0,0),(-1,0),SIDEBAR),('TEXTCOLOR',(0,0),(-1,0),colors.white),
            ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),8),
            ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f8f9fa')]),
            ('GRID',(0,0),(-1,-1),0.3,colors.HexColor('#dee2e6')),
            ('PADDING',(0,0),(-1,-1),5),('VALIGN',(0,0),(-1,-1),'MIDDLE'),
        ]
        for r in totais:
            cmds += [('BACKGROUND',(0,r),(-1,r),colors.HexColor('#f0f4f8')),
                     ('FONTNAME',(0,r),(-1,r),'Helvetica-Bold')]
        t.setStyle(TableStyle(cmds))
        return t

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            leftMargin=1.5*cm, rightMargin=1.5*cm,
                            topMargin=1.8*cm, bottomMargin=2*cm)
    els = []

    els.append(Paragraph('Relatório Diário', st_h1))
    els.append(HRFlowable(width='100%', thickness=2, color=LARANJA, spaceBefore=4, spaceAfter=12))
    els.append(Paragraph(f'{dia_semana}, {data_fmt}', E('sub', fontSize=11, textColor=CINZA)))
    els.append(Spacer(1, 0.4*cm))

    els.append(Paragraph('Resumo do Dia', st_h2))
    els.append(linha())

    def card_resumo(titulo, qtd, total, cor):
        return Table([
            [Paragraph(titulo, E('ct', fontSize=9, textColor=CINZA))],
            [Paragraph(f'{qtd} aluno{"s" if qtd != 1 else ""}', E('cq', fontSize=11, fontName='Helvetica-Bold', textColor=cor))],
            [Paragraph(f'R$ {total:.2f}', E('cv', fontSize=14, fontName='Helvetica-Bold', textColor=cor))],
        ], colWidths=[(pw-3*cm)/4-0.3*cm],
           style=[('BOX',(0,0),(-1,-1),0.5,colors.HexColor('#dee2e6')),
                  ('PADDING',(0,0),(-1,-1),10),('BACKGROUND',(0,0),(-1,-1),colors.white)])

    cards = [
        card_resumo('💵 Dinheiro', len(p_dinheiro), total_dinheiro, VERDE),
        card_resumo('📱 PIX',      len(p_pix),      total_pix,      colors.HexColor('#1565c0')),
        card_resumo('💳 Cartão',   len(p_cartao),   total_cartao,   colors.HexColor('#6a1b9a')),
        card_resumo('📊 Total',    len(pagamentos), total_geral,    SIDEBAR),
    ]
    els.append(Table([cards], colWidths=[(pw-3*cm)/4]*4,
               style=[('LEFTPADDING',(0,0),(-1,-1),3),('RIGHTPADDING',(0,0),(-1,-1),3)]))
    els.append(Spacer(1, 0.5*cm))

    st_cell = E('cell', fontSize=8, textColor=colors.HexColor('#333'), leading=10)

    def _tbl_secao(titulo, registros, cols, total_val):
        if not registros: return
        els.append(Paragraph(titulo, st_h2))
        els.append(linha())
        # Paragraph nas células de texto: quebra linha automática em nomes longos
        data = [['#'] + _header(cols)]
        for i, p in enumerate(registros, 1):
            data.append([str(i)] + [Paragraph(str(v), st_cell) for v in _row(p, cols)])
        tot = [''] + [''] * len(cols)
        if 'valor' in cols: tot[1 + cols.index('valor')] = f'R$ {total_val:.2f}'
        tot[0] = ''
        data.append(tot)
        # Coluna nome com peso 3x; demais dividem o restante
        n = len(cols)
        larg_total = pw - 3*cm
        larg_num   = 0.8*cm
        if 'nome' in cols:
            larg_resto = (larg_total - larg_num) / (n + 2)   # nome vale 3 partes
            col_widths = [larg_num] + [larg_resto * 3 if c == 'nome' else larg_resto for c in cols]
        else:
            col_widths = [larg_num] + [(larg_total - larg_num) / n] * n
        els.append(tabela(data, col_widths=col_widths, totais=[len(data)-1]))
        els.append(Spacer(1, 0.4*cm))

    if 'd-dinheiro' in secoes_diario and p_dinheiro: _tbl_secao('💵 Relação de Dinheiro', p_dinheiro, cols_dinheiro, total_dinheiro)
    if 'd-pix'      in secoes_diario and p_pix:      _tbl_secao('📱 Relação de PIX',      p_pix,      cols_pix,      total_pix)
    if 'd-cartao'   in secoes_diario and p_cartao:   _tbl_secao('💳 Relação de Cartão',   p_cartao,   cols_cartao,   total_cartao)

    if not pagamentos:
        els.append(Paragraph('Nenhum pagamento registrado nesta data.', st_body))

    doc.build(els, canvasmaker=DiarCanvas)
    buffer.seek(0)
    return Response(buffer.getvalue(), mimetype='application/pdf',
        headers={'Content-Disposition': f'attachment; filename=relatorio_diario_{data_rel}.pdf'})

# ── Relatório Mensal ──────────────────────────────────────────────────────────

@bp.route('/financeiro/relatorio-mensal')
@login_required
def financeiro_relatorio_mensal():
    from app.models import Pagamento, Plano
    import calendar, io

    inicio_str    = request.args.get('inicio', '')
    fim_str       = request.args.get('fim', '')
    formato       = request.args.get('formato', 'pdf')
    secoes        = request.args.get('secoes', 'resumo,dinheiro,pix,cartao,inadimplentes').split(',')
    nome_ac       = get_param('nome_academia', 'Academia')
    hoje          = date.today()
    cols_dinheiro = [c for c in request.args.get('cols_dinheiro', 'nome,data_pagamento,plano,valor').split(',') if c]
    cols_pix      = [c for c in request.args.get('cols_pix',      'nome,data_pagamento,plano,banco,valor').split(',') if c]
    cols_cartao   = [c for c in request.args.get('cols_cartao',   'nome,data_pagamento,plano,tipo,valor').split(',') if c]
    cols_inad     = [c for c in request.args.get('cols_inad',     'nome,plano,vencimento,valor').split(',') if c]

    try:
        primeiro_dia = datetime.strptime(inicio_str, '%Y-%m-%d').date()
        ultimo_dia   = datetime.strptime(fim_str,    '%Y-%m-%d').date()
    except:
        flash('Datas inválidas.', 'danger')
        return redirect(url_for('financeiro.financeiro'))

    if primeiro_dia.month == ultimo_dia.month and primeiro_dia.year == ultimo_dia.year:
        meses_pt = ['Janeiro','Fevereiro','Março','Abril','Maio','Junho',
                    'Julho','Agosto','Setembro','Outubro','Novembro','Dezembro']
        nome_mes = meses_pt[primeiro_dia.month-1] + ' de ' + str(primeiro_dia.year)
    else:
        nome_mes = primeiro_dia.strftime('%d/%m/%Y') + ' a ' + ultimo_dia.strftime('%d/%m/%Y')

    todos_mes = Pagamento.query.filter(
        Pagamento.data_pagamento >= primeiro_dia,
        Pagamento.data_pagamento <= ultimo_dia,
        Pagamento.status == 'pago'
    ).order_by(Pagamento.data_pagamento, Pagamento.aluno_id).all()

    p_dinheiro = [p for p in todos_mes if p.forma_pagamento == 'dinheiro']
    p_pix      = [p for p in todos_mes if p.forma_pagamento == 'pix']
    p_credito  = [p for p in todos_mes if p.forma_pagamento == 'credito']
    p_debito   = [p for p in todos_mes if p.forma_pagamento == 'debito']
    p_cartao   = p_credito + p_debito

    total_dinheiro = sum(float(p.valor) for p in p_dinheiro)
    total_pix      = sum(float(p.valor) for p in p_pix)
    total_cartao   = sum(float(p.valor) for p in p_cartao)
    total_geral    = total_dinheiro + total_pix + total_cartao

    # Inadimplentes: alunos ativos, não-diária, com vencimento anterior ao fim do período
    # e sem pagamento pago no período
    planos_diaria = [p.nome for p in Plano.query.filter_by(dias_validade=1, ativo=True).all()]
    alunos_pagos  = {p.aluno_id for p in todos_mes}
    q_inad = Aluno.query.filter(
        Aluno.ativo == True,
        Aluno.vencimento < hoje,       # só considera inadimplente se já venceu
        Aluno.vencimento <= ultimo_dia # e se venceu dentro ou antes do período
    )
    if planos_diaria:
        q_inad = q_inad.filter(~Aluno.plano.in_(planos_diaria))
    if alunos_pagos:
        q_inad = q_inad.filter(~Aluno.id.in_(list(alunos_pagos)))
    inadimplentes = q_inad.order_by(Aluno.nome).all()

    if formato == 'csv':
        import csv
        output = io.StringIO()
        w = csv.writer(output)
        w.writerow([f'RELATÓRIO MENSAL — {nome_ac.upper()}'])
        w.writerow([f'Período: {nome_mes}'])
        w.writerow([f'Emitido em: {hoje.strftime("%d/%m/%Y")}'])
        w.writerow([])
        if 'resumo' in secoes:
            w.writerow(['=== RESUMO FINANCEIRO ==='])
            w.writerow(['Forma', 'Qtd Alunos', 'Total'])
            w.writerow(['Dinheiro', len(p_dinheiro), f'R$ {total_dinheiro:.2f}'])
            w.writerow(['PIX',      len(p_pix),      f'R$ {total_pix:.2f}'])
            w.writerow(['Cartão',   len(p_cartao),   f'R$ {total_cartao:.2f}'])
            w.writerow(['TOTAL',    len(todos_mes),  f'R$ {total_geral:.2f}'])
            w.writerow(['Inadimplentes', len(inadimplentes), ''])
            w.writerow([])
        if 'dinheiro' in secoes and p_dinheiro:
            w.writerow(['=== RELAÇÃO DINHEIRO ==='])
            w.writerow(['#'] + _header(cols_dinheiro))
            for i, p in enumerate(p_dinheiro, 1): w.writerow([i] + _row(p, cols_dinheiro))
            tot = [''] * len(cols_dinheiro)
            if 'valor' in cols_dinheiro: tot[cols_dinheiro.index('valor')] = f'R$ {total_dinheiro:.2f}'
            w.writerow(['TOTAL'] + tot)
            w.writerow([])
        if 'pix' in secoes and p_pix:
            w.writerow(['=== RELAÇÃO PIX ==='])
            w.writerow(['#'] + _header(cols_pix))
            for i, p in enumerate(p_pix, 1): w.writerow([i] + _row(p, cols_pix))
            tot = [''] * len(cols_pix)
            if 'valor' in cols_pix: tot[cols_pix.index('valor')] = f'R$ {total_pix:.2f}'
            w.writerow(['TOTAL'] + tot)
            w.writerow([])
        if 'cartao' in secoes and p_cartao:
            w.writerow(['=== RELAÇÃO CARTÃO ==='])
            w.writerow(['#'] + _header(cols_cartao))
            for i, p in enumerate(p_cartao, 1): w.writerow([i] + _row(p, cols_cartao))
            tot = [''] * len(cols_cartao)
            if 'valor' in cols_cartao: tot[cols_cartao.index('valor')] = f'R$ {total_cartao:.2f}'
            w.writerow(['TOTAL'] + tot)
            w.writerow([])
        if 'inadimplentes' in secoes and inadimplentes:
            planos_v = {p.nome: float(p.valor) for p in Plano.query.all()}
            w.writerow(['=== INADIMPLENTES ==='])
            w.writerow(['#'] + _header(cols_inad))
            for i, a in enumerate(inadimplentes, 1): w.writerow([i] + _row_inad(a, cols_inad, planos_v))
        output.seek(0)
        return Response('\ufeff' + output.getvalue(), mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename=relatorio_mensal_{primeiro_dia.strftime("%Y%m")}.csv'})

    # PDF
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    from reportlab.pdfgen import canvas as pdfcanvas

    LARANJA = colors.HexColor(get_param('cor_principal', '#FF6B00'))
    SIDEBAR = colors.HexColor('#1a1a2e')
    CINZA   = colors.HexColor('#6c757d')
    VERDE   = colors.HexColor('#2e7d32')
    pw, ph  = landscape(A4)

    endereco_ac  = get_param('endereco', '')
    telefone_ac  = get_param('telefone', '')
    hora_emissao = datetime.now().strftime('%d/%m/%Y às %H:%M')

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
                self.setFillColor(SIDEBAR)
                self.rect(0, ph-1.6*cm, pw, 1.6*cm, fill=1, stroke=0)
                self.setFillColor(LARANJA)
                self.rect(0, ph-1.6*cm, 0.5*cm, 1.6*cm, fill=1, stroke=0)
                self.setFont('Helvetica-Bold', 11)
                self.setFillColor(colors.white)
                self.drawString(0.8*cm, ph-0.7*cm, nome_ac.upper())
                self.setFont('Helvetica', 8)
                self.setFillColor(colors.HexColor('#cccccc'))
                if endereco_ac:
                    self.drawString(0.8*cm, ph-1.0*cm, endereco_ac)
                info_linha = f'Relatório Mensal  |  {nome_mes}'
                if telefone_ac:
                    info_linha += f'  |  Tel: {telefone_ac}'
                self.drawString(0.8*cm, ph-1.3*cm, info_linha)
                self.setFillColor(colors.HexColor('#aaaaaa'))
                self.drawRightString(pw-1*cm, ph-0.7*cm, f'Emitido em {hora_emissao}')
                self.drawRightString(pw-1*cm, ph-1.0*cm, f'Pág. {self._pageNumber} de {total}')
                self.setStrokeColor(colors.HexColor('#dee2e6'))
                self.setLineWidth(0.5)
                self.line(1*cm, 1.4*cm, pw-1*cm, 1.4*cm)
                self.setFont('Helvetica', 8)
                self.setFillColor(CINZA)
                self.drawString(1*cm, 1.0*cm, nome_ac + '  —  Uso interno')
                self.drawRightString(pw-1*cm, 1.0*cm, f'Página {self._pageNumber} de {total}')
                super().showPage()
            super().save()

    styles = getSampleStyleSheet()
    def E(name, **kw): return ParagraphStyle(name, parent=styles['Normal'], **kw)
    st_h1 = E('h1', fontSize=16, fontName='Helvetica-Bold', textColor=SIDEBAR, spaceAfter=4)
    st_h2 = E('h2', fontSize=12, fontName='Helvetica-Bold', textColor=SIDEBAR, spaceAfter=4, spaceBefore=14)
    st_body = E('body', fontSize=9, textColor=colors.HexColor('#333'), leading=13)

    def linha(): return HRFlowable(width='100%', thickness=0.5, color=colors.HexColor('#dee2e6'), spaceBefore=6, spaceAfter=6)

    def tabela(data, col_widths=None, totais=[]):
        t = Table(data, colWidths=col_widths, repeatRows=1)
        cmds = [
            ('BACKGROUND',(0,0),(-1,0),SIDEBAR),('TEXTCOLOR',(0,0),(-1,0),colors.white),
            ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),8),
            ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f8f9fa')]),
            ('GRID',(0,0),(-1,-1),0.3,colors.HexColor('#dee2e6')),
            ('PADDING',(0,0),(-1,-1),5),('VALIGN',(0,0),(-1,-1),'MIDDLE'),
        ]
        for r in totais:
            cmds += [('BACKGROUND',(0,r),(-1,r),colors.HexColor('#f0f4f8')),
                     ('FONTNAME',(0,r),(-1,r),'Helvetica-Bold')]
        t.setStyle(TableStyle(cmds))
        return t

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4),
                            leftMargin=1*cm, rightMargin=1*cm,
                            topMargin=1.6*cm, bottomMargin=1.8*cm)
    els = []

    els.append(Paragraph(f'Relatório Mensal — {nome_mes}', st_h1))
    els.append(HRFlowable(width='100%', thickness=2, color=LARANJA, spaceBefore=4, spaceAfter=12))
    els.append(Spacer(1, 0.2*cm))

    if 'resumo' in secoes:
        els.append(Paragraph('Resumo Financeiro', st_h2))
        els.append(linha())
        def card(titulo, qtd, total, cor):
            return Table([
                [Paragraph(titulo, E('ct', fontSize=8, textColor=CINZA))],
                [Paragraph(f'{qtd} aluno{"s" if qtd != 1 else ""}', E('cq', fontSize=10, fontName='Helvetica-Bold', textColor=cor))],
                [Paragraph(f'R$ {total:.2f}', E('cv', fontSize=14, fontName='Helvetica-Bold', textColor=cor))],
            ], colWidths=[(pw-2*cm)/4-0.3*cm],
               style=[('BOX',(0,0),(-1,-1),0.5,colors.HexColor('#dee2e6')),
                      ('PADDING',(0,0),(-1,-1),10),('BACKGROUND',(0,0),(-1,-1),colors.white)])
        cards = [
            card('💵 Dinheiro',    len(p_dinheiro), total_dinheiro, VERDE),
            card('📱 PIX',         len(p_pix),      total_pix,      colors.HexColor('#1565c0')),
            card('💳 Cartão',      len(p_cartao),   total_cartao,   colors.HexColor('#6a1b9a')),
            card('📊 Total Geral', len(todos_mes),  total_geral,    SIDEBAR),
        ]
        els.append(Table([cards], colWidths=[(pw-2*cm)/4]*4,
                   style=[('LEFTPADDING',(0,0),(-1,-1),3),('RIGHTPADDING',(0,0),(-1,-1),3)]))
        els.append(Spacer(1, 0.3*cm))
        resumo_data = [['Forma de Pagamento', 'Qtd. Alunos', 'Total'],
                       ['Dinheiro', str(len(p_dinheiro)), f'R$ {total_dinheiro:.2f}'],
                       ['PIX',      str(len(p_pix)),      f'R$ {total_pix:.2f}'],
                       ['Cartão',   str(len(p_cartao)),   f'R$ {total_cartao:.2f}'],
                       ['TOTAL GERAL', str(len(todos_mes)), f'R$ {total_geral:.2f}'],
                       ['Inadimplentes (mensalistas)', str(len(inadimplentes)), '—']]
        els.append(tabela(resumo_data, col_widths=[8*cm, 4*cm, 4*cm], totais=[4]))
        els.append(Spacer(1, 0.5*cm))

    st_cell = E('cell', fontSize=8, textColor=colors.HexColor('#333'), leading=10)

    def _larguras(cols, larg_total):
        """Coluna nome com peso 3x; demais dividem o restante igualmente."""
        larg_num = 0.8*cm
        n = len(cols)
        if 'nome' in cols:
            larg_resto = (larg_total - larg_num) / (n + 2)
            return [larg_num] + [larg_resto * 3 if c == 'nome' else larg_resto for c in cols]
        return [larg_num] + [(larg_total - larg_num) / n] * n

    def _tbl_mensal(titulo, registros, cols, total_val):
        if not registros: return
        els.append(Paragraph(titulo, st_h2))
        els.append(linha())
        # Paragraph nas células: quebra linha automática em nomes longos
        data = [['#'] + _header(cols)]
        for i, p in enumerate(registros, 1):
            data.append([str(i)] + [Paragraph(str(v), st_cell) for v in _row(p, cols)])
        tot = [''] + [''] * len(cols)
        if 'valor' in cols: tot[1 + cols.index('valor')] = f'R$ {total_val:.2f}'
        data.append(tot)
        els.append(tabela(data, col_widths=_larguras(cols, pw - 2*cm), totais=[len(data)-1]))
        els.append(Spacer(1, 0.4*cm))

    if 'dinheiro' in secoes and p_dinheiro: _tbl_mensal('💵 Relação de Dinheiro', p_dinheiro, cols_dinheiro, total_dinheiro)
    if 'pix'      in secoes and p_pix:      _tbl_mensal('📱 Relação de PIX',      p_pix,      cols_pix,      total_pix)
    if 'cartao'   in secoes and p_cartao:   _tbl_mensal('💳 Relação de Cartão',   p_cartao,   cols_cartao,   total_cartao)

    if 'inadimplentes' in secoes:
        els.append(Paragraph('⚠️ Inadimplentes', st_h2))
        els.append(linha())
        if inadimplentes:
            planos_v = {p.nome: float(p.valor) for p in Plano.query.all()}
            data = [['#'] + _header(cols_inad)]
            for i, a in enumerate(inadimplentes, 1):
                data.append([str(i)] + [Paragraph(str(v), st_cell) for v in _row_inad(a, cols_inad, planos_v)])
            tot = ['', f'Total: {len(inadimplentes)} aluno(s)'] + [''] * (len(cols_inad) - 1)
            if 'valor' in cols_inad:
                total_inadimp = sum(planos_v.get(a.plano, 0) for a in inadimplentes)
                tot[1 + cols_inad.index('valor')] = f'R$ {total_inadimp:.2f}'
            data.append(tot)
            els.append(tabela(data, col_widths=_larguras(cols_inad, pw - 2*cm), totais=[len(data)-1]))
        else:
            els.append(Paragraph('Nenhum inadimplente no período.', st_body))

    doc.build(els, canvasmaker=MensalCanvas)
    buffer.seek(0)
    return Response(buffer.getvalue(), mimetype='application/pdf',
        headers={'Content-Disposition': f'attachment; filename=relatorio_mensal_{primeiro_dia.strftime("%Y%m")}.pdf'})