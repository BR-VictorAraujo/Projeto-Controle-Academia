from flask import Blueprint, render_template, redirect, url_for, request, flash, Response
from app import db
from app.models import Aluno, LogAuditoria, RegistroAcesso
from datetime import datetime, date, timedelta
import json
from app.routes.auth import login_required, registrar_log, get_param, paginar, OPCOES_POR_PAGINA

bp = Blueprint('relatorios', __name__)

# ── Paginação manual (para listas filtradas em Python) ────────────────────────

class PaginacaoManual:
    """
    Objeto de paginação compatível com o macro _paginacao.html,
    usado quando a lista já está em memória (após filtro Python)
    e não é possível usar o .paginate() do SQLAlchemy diretamente.
    """
    def __init__(self, lista, page, per_page):
        self.total    = len(lista)
        self.per_page = per_page
        self.page     = max(1, page)
        self.pages    = max(1, (self.total + per_page - 1) // per_page)
        # Garante que page não ultrapasse o total de páginas
        if self.page > self.pages:
            self.page = self.pages
        self.has_prev = self.page > 1
        self.has_next = self.page < self.pages
        self.prev_num = self.page - 1
        self.next_num = self.page + 1
        start         = (self.page - 1) * per_page
        self.items    = lista[start : start + per_page]

# ── Helpers ───────────────────────────────────────────────────────────────────

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
    frio_val = 999999
    tem = False
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

# ── Rota principal ────────────────────────────────────────────────────────────

@bp.route('/relatorios')
@login_required
def relatorios():
    from app.models import Plano as PlanoModel, Pagamento
    hoje      = datetime.utcnow().date()
    sete_dias = hoje + timedelta(days=7)

    tipo_ativo    = request.args.get('tipo', '')
    filtro_inicio = request.args.get('inicio', '')
    filtro_fim    = request.args.get('fim', '')

    # Paginação
    pagina     = request.args.get('pagina',     1,  type=int)
    por_pagina = request.args.get('por_pagina', 20, type=int)
    if por_pagina not in OPCOES_POR_PAGINA:
        por_pagina = OPCOES_POR_PAGINA[0]

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

    # Aniversariantes
    mes_aniversario = request.args.get('mes_aniversario', '')
    aniversariantes_raw = []
    if mes_aniversario:
        try:
            mes_num = int(mes_aniversario)
            aniversariantes_raw = Aluno.query.filter(
                Aluno.ativo == True,
                db.extract('month', Aluno.data_nascimento) == mes_num
            ).order_by(db.extract('day', Aluno.data_nascimento)).all()
        except:
            pass
    pag_aniversariantes = PaginacaoManual(aniversariantes_raw, pagina, por_pagina)
    aniversariantes     = pag_aniversariantes.items

    filtro_aplicado = bool(filtro_inicio and filtro_fim)
    passagens_raw = []; por_aluno_raw = []; auditoria_raw = []
    heatmap = [[0]*24 for _ in range(7)]
    pico_quente = {'dia':0,'hora':0,'valor':0}; pico_frio = {'dia':0,'hora':0,'valor':0}
    total_passagens = 0
    fin_pagamentos = []; fin_resumo = {'recebido':0.0,'pendente':0.0}
    pag_fin = None

    if filtro_aplicado:
        dt_inicio  = datetime.strptime(filtro_inicio, '%Y-%m-%d').date()
        dt_fim     = datetime.strptime(filtro_fim,    '%Y-%m-%d').date()
        inicio_utc, fim_utc = _filtro_periodo(dt_inicio, dt_fim)

        q = RegistroAcesso.query.filter(RegistroAcesso.entrada_em >= inicio_utc, RegistroAcesso.entrada_em < fim_utc)
        if f_tipo_ac: q = q.filter(RegistroAcesso.tipo == f_tipo_ac)
        # Carrega tudo para heatmap e filtros Python
        passagens_raw = q.order_by(RegistroAcesso.entrada_em.desc()).all()
        if f_nome:  passagens_raw = [r for r in passagens_raw if f_nome.lower() in r.aluno.nome.lower()]
        if f_plano and tipo_ativo in ('passagens','por_aluno'):
            passagens_raw = [r for r in passagens_raw if r.aluno.plano == f_plano]
        total_passagens = len(passagens_raw)

        por_aluno_raw = _agrupar_por_aluno(passagens_raw)
        if f_status:
            por_aluno_raw = [i for i in por_aluno_raw if i['aluno'].ativo == (f_status == 'ativo')]
        if f_plano and tipo_ativo == 'por_aluno':
            por_aluno_raw = [i for i in por_aluno_raw if i['aluno'].plano == f_plano]
        if f_ordem == 'mais':    por_aluno_raw = sorted(por_aluno_raw, key=lambda x: x['total'], reverse=True)
        elif f_ordem == 'menos': por_aluno_raw = sorted(por_aluno_raw, key=lambda x: x['total'])

        qa = LogAuditoria.query.filter(LogAuditoria.criado_em >= inicio_utc, LogAuditoria.criado_em < fim_utc)
        if f_usuario: qa = qa.filter(LogAuditoria.usuario.ilike(f'%{f_usuario}%'))
        if f_acao:    qa = qa.filter(LogAuditoria.acao.ilike(f'%{f_acao}%'))
        auditoria_raw = qa.order_by(LogAuditoria.criado_em.desc()).limit(500).all()

        heatmap, pico_quente, pico_frio = _build_heatmap(passagens_raw)

        if tipo_ativo == 'financeiro':
            qpag = Pagamento.query.filter(
                db.or_(
                    db.and_(
                        Pagamento.status == 'pago',
                        Pagamento.data_pagamento >= dt_inicio,
                        Pagamento.data_pagamento <= dt_fim
                    ),
                    db.and_(
                        Pagamento.status == 'pendente',
                        Pagamento.data_vencimento >= dt_inicio,
                        Pagamento.data_vencimento <= dt_fim
                    )
                )
            )
            if f_pg_status:
                if f_pg_status == 'pago':
                    qpag = qpag.filter(
                        Pagamento.status == 'pago',
                        Pagamento.data_pagamento >= dt_inicio,
                        Pagamento.data_pagamento <= dt_fim
                    )
                elif f_pg_status == 'pendente':
                    qpag = qpag.filter(
                        Pagamento.status == 'pendente',
                        Pagamento.data_vencimento >= dt_inicio,
                        Pagamento.data_vencimento <= dt_fim
                    )
            if f_forma_pag: qpag = qpag.filter(Pagamento.forma_pagamento == f_forma_pag)
            if f_nome:  qpag = qpag.join(Aluno).filter(Aluno.nome.ilike(f'%{f_nome}%'))
            if f_plano: qpag = qpag.join(Aluno, isouter=True).filter(Aluno.plano == f_plano)
            qpag = qpag.order_by(Pagamento.data_vencimento)
            # fin_pagamentos usa paginar() do SQLAlchemy direto
            pag_fin = paginar(qpag)
            fin_pagamentos = pag_fin.items
            # Totais calculados sobre TODOS os registros do período (não só a página)
            for p in qpag.all():
                if p.status == 'pago': fin_resumo['recebido'] += float(p.valor)
                else:                  fin_resumo['pendente'] += float(p.valor)

    # Paginações manuais
    pag_passagens = PaginacaoManual(passagens_raw, pagina, por_pagina)
    pag_por_aluno = PaginacaoManual(por_aluno_raw, pagina, por_pagina)
    pag_auditoria = PaginacaoManual(auditoria_raw, pagina, por_pagina)

    # Vencimentos (filtro Python em situacao)
    qv = Aluno.query.filter_by(ativo=True)
    if f_plano and tipo_ativo == 'vencimentos': qv = qv.filter(Aluno.plano == f_plano)
    if f_nome  and tipo_ativo == 'vencimentos': qv = qv.filter(Aluno.nome.ilike(f'%{f_nome}%'))
    vencimentos_raw = qv.order_by(Aluno.vencimento).all()
    if f_situacao:
        def get_sit(a):
            if a.vencimento and a.vencimento < hoje:       return 'vencido'
            if a.vencimento and a.vencimento <= sete_dias: return 'vence_breve'
            return 'em_dia'
        vencimentos_raw = [a for a in vencimentos_raw if get_sit(a) == f_situacao]
    pag_vencimentos = PaginacaoManual(vencimentos_raw, pagina, por_pagina)

    financeiro = []
    for plano in PlanoModel.query.filter_by(ativo=True).all():
        total = Aluno.query.filter_by(plano=plano.nome, ativo=True).count()
        financeiro.append({'plano': plano.nome, 'valor_plano': float(plano.valor),
                           'total': total, 'receita': float(plano.valor) * total})

    planos_lista   = [p.nome for p in PlanoModel.query.filter_by(ativo=True).order_by(PlanoModel.nome).all()]
    usuarios_lista = [u[0] for u in db.session.query(LogAuditoria.usuario).distinct().order_by(LogAuditoria.usuario).all()]
    acoes_lista    = sorted(set(a[0].split(' ')[0] for a in db.session.query(LogAuditoria.acao).distinct().all() if a[0]))
    filtros_ativos = sum(1 for v in [f_nome,f_plano,f_tipo_ac,f_status,f_ordem,f_situacao,f_usuario,f_acao,f_pg_status,f_forma_pag] if v)

    return render_template('relatorios.html',
                           # Itens paginados (apenas a página atual)
                           passagens=pag_passagens.items,
                           por_aluno=pag_por_aluno.items,
                           auditoria=pag_auditoria.items,
                           vencimentos=pag_vencimentos.items,
                           aniversariantes=aniversariantes,
                           fin_pagamentos=fin_pagamentos,
                           # Objetos de paginação para o macro
                           pag_passagens=pag_passagens,
                           pag_por_aluno=pag_por_aluno,
                           pag_auditoria=pag_auditoria,
                           pag_vencimentos=pag_vencimentos,
                           pag_aniversariantes=pag_aniversariantes,
                           pag_fin=pag_fin,
                           opcoes_por_pagina=OPCOES_POR_PAGINA,
                           # Totais gerais (sobre todos os dados, não só a página)
                           total_passagens=total_passagens,
                           fin_resumo=fin_resumo,
                           financeiro=financeiro,
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
                           relatorio_ativo=tipo_ativo,
                           aniversariantes_raw=aniversariantes_raw,
                           mes_aniversario=mes_aniversario)

# ── Exportar CSV ──────────────────────────────────────────────────────────────

@bp.route('/relatorios/exportar/csv')
@login_required
def exportar_csv():
    import csv, io
    from app.models import Pagamento
    tipo          = request.args.get('tipo', 'passagens')
    filtro_inicio = request.args.get('inicio', '')
    filtro_fim    = request.args.get('fim', '')
    colunas_str   = request.args.get('colunas', '')
    hoje          = datetime.utcnow().date()

    if not filtro_inicio or not filtro_fim:
        flash('Informe o período para exportar.', 'warning')
        return redirect(url_for('relatorios.relatorios'))

    dt_inicio  = datetime.strptime(filtro_inicio, '%Y-%m-%d').date()
    dt_fim     = datetime.strptime(filtro_fim,    '%Y-%m-%d').date()
    inicio_utc, fim_utc = _filtro_periodo(dt_inicio, dt_fim)
    fuso = int(get_param('fuso_horario', '-3'))

    f_nome=request.args.get('f_nome','').strip(); f_plano=request.args.get('f_plano','')
    f_tipo_ac=request.args.get('f_tipo_ac',''); f_status=request.args.get('f_status','')
    f_ordem=request.args.get('f_ordem',''); f_situacao=request.args.get('f_situacao','')
    f_usuario=request.args.get('f_usuario','').strip(); f_acao=request.args.get('f_acao','')
    f_pg_status=request.args.get('f_pg_status',''); f_forma_pag=request.args.get('f_forma_pag','')

    output = io.StringIO()
    writer = csv.writer(output)

    def get_col_passagem(r, col):
        dt_local = r.entrada_em + timedelta(hours=fuso)
        m = {'data_hora':dt_local.strftime('%d/%m/%Y %H:%M'),'aluno':r.aluno.nome,
             'cpf':r.aluno.documento if r.aluno.tipo_documento=='cpf' else '—',
             'telefone':r.aluno.telefone or '—','plano':r.aluno.plano or '—',
             'tipo':r.tipo,'observacao':r.observacao or '—'}
        return m.get(col, '—')

    def get_col_por_aluno(item, col):
        m = {'aluno':item['aluno'].nome,
             'cpf':item['aluno'].documento if item['aluno'].tipo_documento=='cpf' else '—',
             'telefone':item['aluno'].telefone or '—',
             'endereco':item['aluno'].endereco or '—',
             'plano':item['aluno'].plano or '—',
             'total':str(item['total']),
             'primeiro':(item['primeiro']+timedelta(hours=fuso)).strftime('%d/%m/%Y %H:%M'),
             'ultimo':(item['ultimo']+timedelta(hours=fuso)).strftime('%d/%m/%Y %H:%M'),
             'status':'Ativo' if item['aluno'].ativo else 'Inativo'}
        return m.get(col, '—')

    def get_col_vencimento(a, col):
        sit = ('Vencido' if a.vencimento and a.vencimento < hoje else
               'Vence em breve' if a.vencimento and a.vencimento <= hoje+timedelta(days=7) else 'Em dia')
        m = {'aluno':a.nome,'cpf':a.documento if a.tipo_documento=='cpf' else '—',
             'telefone':a.telefone or '—','plano':a.plano or '—',
             'vencimento':a.vencimento.strftime('%d/%m/%Y') if a.vencimento else '—','situacao':sit}
        return m.get(col, '—')

    def get_col_auditoria(log, col):
        m = {'data_hora':log.criado_em.strftime('%d/%m/%Y %H:%M'),
             'usuario':log.usuario,'acao':log.acao,'detalhes':log.detalhes or '—','ip':log.ip or '—'}
        return m.get(col, '—')

    def get_col_financeiro(p, col):
        m = {'aluno':p.aluno.nome,'cpf':p.aluno.documento if p.aluno.tipo_documento=='cpf' else '—',
             'telefone':p.aluno.telefone or '—','plano':p.aluno.plano or '—',
             'valor':f'R$ {float(p.valor):.2f}','vencimento':p.data_vencimento.strftime('%d/%m/%Y'),
             'pagamento':p.data_pagamento.strftime('%d/%m/%Y') if p.data_pagamento else '—',
             'forma':p.forma_pagamento or '—','status':p.status.capitalize()}
        return m.get(col, '—')

    col_labels = {
        'data_hora':'Data/Hora','aluno':'Aluno','cpf':'CPF','telefone':'Telefone',
        'endereco':'Endereço','plano':'Plano','tipo':'Tipo','observacao':'Observação',
        'total':'Total Passagens','primeiro':'Primeira Entrada','ultimo':'Última Entrada',
        'status':'Status','vencimento':'Vencimento','situacao':'Situação','usuario':'Usuário',
        'acao':'Ação','detalhes':'Detalhes','ip':'IP',
        'valor':'Valor','pagamento':'Data Pagamento','forma':'Forma',
    }

    def parse_cols(default_list):
        return [c for c in colunas_str.split(',') if c] if colunas_str else default_list

    if tipo == 'passagens':
        cols = parse_cols(['data_hora','aluno','plano','tipo','observacao'])
        writer.writerow([col_labels.get(c,c) for c in cols])
        q = RegistroAcesso.query.filter(RegistroAcesso.entrada_em >= inicio_utc, RegistroAcesso.entrada_em < fim_utc)
        if f_tipo_ac: q = q.filter(RegistroAcesso.tipo == f_tipo_ac)
        rows = q.order_by(RegistroAcesso.entrada_em.desc()).all()
        if f_nome:  rows = [r for r in rows if f_nome.lower() in r.aluno.nome.lower()]
        if f_plano: rows = [r for r in rows if r.aluno.plano == f_plano]
        for r in rows: writer.writerow([get_col_passagem(r,c) for c in cols])

    elif tipo == 'por_aluno':
        cols = parse_cols(['aluno','plano','endereco','total','primeiro','ultimo','status'])
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
        for item in agrupado: writer.writerow([get_col_por_aluno(item,c) for c in cols])

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
        for a in vlist: writer.writerow([get_col_vencimento(a,c) for c in cols])

    elif tipo == 'auditoria':
        cols = parse_cols(['data_hora','usuario','acao','detalhes','ip'])
        writer.writerow([col_labels.get(c,c) for c in cols])
        qa = LogAuditoria.query.filter(LogAuditoria.criado_em >= inicio_utc, LogAuditoria.criado_em < fim_utc)
        if f_usuario: qa = qa.filter(LogAuditoria.usuario.ilike(f'%{f_usuario}%'))
        if f_acao:    qa = qa.filter(LogAuditoria.acao.ilike(f'%{f_acao}%'))
        for log in qa.order_by(LogAuditoria.criado_em.desc()).all():
            writer.writerow([get_col_auditoria(log,c) for c in cols])

    elif tipo == 'financeiro':
        cols = parse_cols(['aluno','plano','valor','vencimento','pagamento','forma','status'])
        writer.writerow([col_labels.get(c,c) for c in cols])
        qpag = Pagamento.query.filter(
            db.or_(
                db.and_(Pagamento.status == 'pago',
                        Pagamento.data_pagamento >= dt_inicio,
                        Pagamento.data_pagamento <= dt_fim),
                db.and_(Pagamento.status == 'pendente',
                        Pagamento.data_vencimento >= dt_inicio,
                        Pagamento.data_vencimento <= dt_fim)
            )
        )
        if f_pg_status: qpag = qpag.filter(Pagamento.status == f_pg_status)
        if f_forma_pag: qpag = qpag.filter(Pagamento.forma_pagamento == f_forma_pag)
        if f_nome:  qpag = qpag.join(Aluno).filter(Aluno.nome.ilike(f'%{f_nome}%'))
        if f_plano: qpag = qpag.join(Aluno, isouter=True).filter(Aluno.plano == f_plano)
        for p in qpag.order_by(Pagamento.data_vencimento).all():
            writer.writerow([get_col_financeiro(p,c) for c in cols])

    output.seek(0)
    return Response('\ufeff' + output.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment; filename=relatorio_{tipo}_{hoje}.csv'})

# ── Exportar PDF ──────────────────────────────────────────────────────────────

@bp.route('/relatorios/exportar/pdf')
@login_required
def exportar_pdf():
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
    from reportlab.lib.units import cm
    from reportlab.pdfgen import canvas as pdfcanvas
    from app.models import Pagamento
    import io, os

    tipo          = request.args.get('tipo', 'passagens')
    filtro_inicio = request.args.get('inicio', '')
    filtro_fim    = request.args.get('fim', '')
    colunas_str   = request.args.get('colunas', '')
    hoje          = datetime.utcnow().date()

    if not filtro_inicio or not filtro_fim:
        flash('Informe o período para exportar.', 'warning')
        return redirect(url_for('relatorios.relatorios'))

    dt_inicio  = datetime.strptime(filtro_inicio, '%Y-%m-%d').date()
    dt_fim     = datetime.strptime(filtro_fim,    '%Y-%m-%d').date()
    inicio_utc, fim_utc = _filtro_periodo(dt_inicio, dt_fim)
    fuso = int(get_param('fuso_horario', '-3'))

    f_nome=request.args.get('f_nome','').strip(); f_plano=request.args.get('f_plano','')
    f_tipo_ac=request.args.get('f_tipo_ac',''); f_status=request.args.get('f_status','')
    f_ordem=request.args.get('f_ordem',''); f_situacao=request.args.get('f_situacao','')
    f_usuario=request.args.get('f_usuario','').strip(); f_acao=request.args.get('f_acao','')
    f_pg_status=request.args.get('f_pg_status',''); f_forma_pag=request.args.get('f_forma_pag','')

    LARANJA      = colors.HexColor(get_param('cor_principal', '#FF6B00'))
    SIDEBAR      = colors.HexColor('#1a1a2e')
    CINZA        = colors.HexColor('#6c757d')
    nome_ac      = get_param('nome_academia', 'Academia')
    logo_url     = get_param('logo_url', '')
    endereco_ac  = get_param('endereco', '')
    telefone_ac  = get_param('telefone', '')
    hora_emissao = datetime.now().strftime('%d/%m/%Y às %H:%M')

    titulos_map = {'passagens':'Relatorio de Passagens','por_aluno':'Passagens por Aluno',
                   'vencimentos':'Relatorio de Vencimentos','auditoria':'Log de Auditoria',
                   'financeiro':'Relatorio Financeiro'}
    titulo_rel = titulos_map.get(tipo, 'Relatorio')

    col_labels = {
        'data_hora':'Data/Hora','aluno':'Aluno','cpf':'CPF','telefone':'Telefone',
        'endereco':'Endereco','plano':'Plano','tipo':'Tipo','observacao':'Observacao',
        'total':'Total','primeiro':'Primeira Entrada','ultimo':'Ultima Entrada','status':'Status',
        'vencimento':'Vencimento','situacao':'Situacao','usuario':'Usuario',
        'acao':'Acao','detalhes':'Detalhes','ip':'IP',
        'valor':'Valor','pagamento':'Data Pagamento','forma':'Forma',
    }

    def parse_cols(default_list):
        return [c for c in colunas_str.split(',') if c] if colunas_str else default_list

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
                self.setFillColor(SIDEBAR)
                self.rect(0, ph-1.6*cm, pw, 1.6*cm, fill=1, stroke=0)
                self.setFillColor(LARANJA)
                self.rect(0, ph-1.6*cm, 0.5*cm, 1.6*cm, fill=1, stroke=0)
                logo_x = 0.8*cm
                if logo_url:
                    logo_path = os.path.join('app', logo_url.lstrip('/'))
                    if os.path.exists(logo_path):
                        try:
                            self.drawImage(logo_path, logo_x, ph-1.4*cm, width=1*cm, height=1*cm,
                                           preserveAspectRatio=True, mask='auto')
                            logo_x = 2.0*cm
                        except: pass
                self.setFont('Helvetica-Bold', 11)
                self.setFillColor(colors.white)
                self.drawString(logo_x, ph-0.7*cm, nome_ac.upper())
                self.setFont('Helvetica', 8)
                self.setFillColor(colors.HexColor('#cccccc'))
                if endereco_ac:
                    self.drawString(logo_x, ph-1.0*cm, endereco_ac)
                info_linha = titulo_rel
                if telefone_ac:
                    info_linha += f'  |  Tel: {telefone_ac}'
                self.drawString(logo_x, ph-1.3*cm, info_linha)
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

    buffer = io.BytesIO()
    styles = getSampleStyleSheet()
    st_small = ParagraphStyle('small', fontSize=8, leading=10)
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4),
                            leftMargin=1*cm, rightMargin=1*cm,
                            topMargin=1.8*cm, bottomMargin=1.8*cm)
    elements = []

    def tbl(data):
        pw_total = landscape(A4)[0] - 2*cm
        n = len(data[0]) if data else 1
        cw = pw_total / n
        t = Table(data, colWidths=[cw]*n, repeatRows=1)
        t.setStyle(TableStyle([
            ('BACKGROUND',(0,0),(-1,0),SIDEBAR),('TEXTCOLOR',(0,0),(-1,0),colors.white),
            ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),8),
            ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f8f9fa')]),
            ('GRID',(0,0),(-1,-1),0.3,colors.HexColor('#dee2e6')),
            ('PADDING',(0,0),(-1,-1),5),('VALIGN',(0,0),(-1,-1),'MIDDLE'),
        ]))
        return t

    if tipo == 'passagens':
        cols = parse_cols(['data_hora','aluno','plano','tipo','observacao'])
        q = RegistroAcesso.query.filter(RegistroAcesso.entrada_em >= inicio_utc, RegistroAcesso.entrada_em < fim_utc)
        if f_tipo_ac: q = q.filter(RegistroAcesso.tipo == f_tipo_ac)
        rows = q.order_by(RegistroAcesso.entrada_em.desc()).all()
        if f_nome:  rows = [r for r in rows if f_nome.lower() in r.aluno.nome.lower()]
        if f_plano: rows = [r for r in rows if r.aluno.plano == f_plano]
        def gcp(r,c):
            dt_local = r.entrada_em + timedelta(hours=fuso)
            m = {'data_hora':dt_local.strftime('%d/%m/%Y %H:%M'),'aluno':r.aluno.nome,
                 'cpf':r.aluno.documento if r.aluno.tipo_documento=='cpf' else '—',
                 'telefone':r.aluno.telefone or '—','plano':r.aluno.plano or '—',
                 'tipo':r.tipo,'observacao':r.observacao or '—'}
            return m.get(c,'—')
        data = [[col_labels.get(c,c) for c in cols]] + [[gcp(r,c) for c in cols] for r in rows]

    elif tipo == 'por_aluno':
        cols = parse_cols(['aluno','plano','endereco','total','primeiro','ultimo','status'])
        q = RegistroAcesso.query.filter(RegistroAcesso.entrada_em >= inicio_utc, RegistroAcesso.entrada_em < fim_utc)
        if f_tipo_ac: q = q.filter(RegistroAcesso.tipo == f_tipo_ac)
        rows = q.order_by(RegistroAcesso.entrada_em.desc()).all()
        if f_nome:  rows = [r for r in rows if f_nome.lower() in r.aluno.nome.lower()]
        if f_plano: rows = [r for r in rows if r.aluno.plano == f_plano]
        agrupado = _agrupar_por_aluno(rows)
        if f_status:  agrupado = [i for i in agrupado if i['aluno'].ativo == (f_status == 'ativo')]
        if f_ordem == 'mais':    agrupado = sorted(agrupado, key=lambda x: x['total'], reverse=True)
        elif f_ordem == 'menos': agrupado = sorted(agrupado, key=lambda x: x['total'])
        def gcpa(item,c):
            m = {'aluno':item['aluno'].nome,
                 'cpf':item['aluno'].documento if item['aluno'].tipo_documento=='cpf' else '—',
                 'telefone':item['aluno'].telefone or '—',
                 'endereco':item['aluno'].endereco or '—',
                 'plano':item['aluno'].plano or '—','total':str(item['total']),
                 'primeiro':(item['primeiro']+timedelta(hours=fuso)).strftime('%d/%m/%Y %H:%M'),
                 'ultimo':(item['ultimo']+timedelta(hours=fuso)).strftime('%d/%m/%Y %H:%M'),
                 'status':'Ativo' if item['aluno'].ativo else 'Inativo'}
            return m.get(c,'—')
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
        def gcv(a,c):
            sit = ('Vencido' if a.vencimento and a.vencimento < hoje else
                   'Vence em breve' if a.vencimento and a.vencimento <= hoje+timedelta(days=7) else 'Em dia')
            m = {'aluno':a.nome,'cpf':a.documento if a.tipo_documento=='cpf' else '—',
                 'telefone':a.telefone or '—','plano':a.plano or '—',
                 'vencimento':a.vencimento.strftime('%d/%m/%Y') if a.vencimento else '—','situacao':sit}
            return m.get(c,'—')
        data = [[col_labels.get(c,c) for c in cols]] + [[gcv(a,c) for c in cols] for a in vlist]

    elif tipo == 'auditoria':
        cols = parse_cols(['data_hora','usuario','acao','detalhes','ip'])
        qa = LogAuditoria.query.filter(LogAuditoria.criado_em >= inicio_utc, LogAuditoria.criado_em < fim_utc)
        if f_usuario: qa = qa.filter(LogAuditoria.usuario.ilike(f'%{f_usuario}%'))
        if f_acao:    qa = qa.filter(LogAuditoria.acao.ilike(f'%{f_acao}%'))
        def gca(log,c):
            m = {'data_hora':log.criado_em.strftime('%d/%m/%Y %H:%M'),
                 'usuario':log.usuario,'acao':log.acao,
                 'detalhes':Paragraph(log.detalhes or '—',st_small),'ip':log.ip or '—'}
            return m.get(c,'—')
        data = [[col_labels.get(c,c) for c in cols]] + [[gca(l,c) for c in cols] for l in qa.order_by(LogAuditoria.criado_em.desc()).all()]

    elif tipo == 'financeiro':
        cols = parse_cols(['aluno','plano','valor','vencimento','pagamento','forma','status'])
        qpag = Pagamento.query.filter(
            db.or_(
                db.and_(Pagamento.status == 'pago',
                        Pagamento.data_pagamento >= dt_inicio,
                        Pagamento.data_pagamento <= dt_fim),
                db.and_(Pagamento.status == 'pendente',
                        Pagamento.data_vencimento >= dt_inicio,
                        Pagamento.data_vencimento <= dt_fim)
            )
        )
        if f_pg_status: qpag = qpag.filter(Pagamento.status == f_pg_status)
        if f_forma_pag: qpag = qpag.filter(Pagamento.forma_pagamento == f_forma_pag)
        if f_nome:  qpag = qpag.join(Aluno).filter(Aluno.nome.ilike(f'%{f_nome}%'))
        if f_plano: qpag = qpag.join(Aluno, isouter=True).filter(Aluno.plano == f_plano)
        def gcf(p,c):
            m = {'aluno':p.aluno.nome,'cpf':p.aluno.documento if p.aluno.tipo_documento=='cpf' else '—',
                 'telefone':p.aluno.telefone or '—','plano':p.aluno.plano or '—',
                 'valor':f'R$ {float(p.valor):.2f}','vencimento':p.data_vencimento.strftime('%d/%m/%Y'),
                 'pagamento':p.data_pagamento.strftime('%d/%m/%Y') if p.data_pagamento else '—',
                 'forma':p.forma_pagamento or '—','status':p.status.capitalize()}
            return m.get(c,'—')
        data = [[col_labels.get(c,c) for c in cols]] + [[gcf(p,c) for c in cols] for p in qpag.order_by(Pagamento.data_vencimento).all()]
    else:
        data = [['Sem dados']]

    st_h1  = ParagraphStyle('h1rel', fontSize=20, fontName='Helvetica-Bold', textColor=SIDEBAR, spaceAfter=0, leading=24)
    st_per = ParagraphStyle('perrel', fontSize=10, textColor=CINZA, spaceAfter=2)
    st_emi = ParagraphStyle('emirel', fontSize=9,  textColor=CINZA, spaceAfter=0)
    elements.append(Paragraph(titulo_rel, st_h1))
    elements.append(Spacer(1, 0.1*cm))
    elements.append(HRFlowable(width='100%', thickness=2, color=LARANJA, spaceBefore=0, spaceAfter=10))
    elements.append(Paragraph(f'Período: <b>{dt_inicio.strftime("%d/%m/%Y")}</b> a <b>{dt_fim.strftime("%d/%m/%Y")}</b>', st_per))
    elements.append(Paragraph(f'Emitido em: {hoje.strftime("%d/%m/%Y")}  |  {nome_ac}', st_emi))
    elements.append(Spacer(1, 0.5*cm))
    if len(data) > 1:
        elements.append(tbl(data))
    else:
        elements.append(Paragraph('Nenhum dado encontrado.', ParagraphStyle('info', fontSize=10, textColor=CINZA)))

    doc.build(elements, canvasmaker=AcadCanvas)
    buffer.seek(0)
    return Response(buffer.getvalue(), mimetype='application/pdf',
                    headers={'Content-Disposition': f'attachment; filename=relatorio_{tipo}_{hoje}.pdf'})

# ── Aniversariantes CSV/PDF ───────────────────────────────────────────────────

@bp.route('/relatorios/exportar/csv/aniversariantes')
@login_required
def exportar_csv_aniversariantes():
    import csv, io
    mes_str  = request.args.get('mes', '')
    colunas  = request.args.get('colunas', 'nome,data_nascimento,idade,telefone,email,plano').split(',')
    hoje     = datetime.utcnow().date()
    meses_pt = ['Janeiro','Fevereiro','Março','Abril','Maio','Junho',
                'Julho','Agosto','Setembro','Outubro','Novembro','Dezembro']
    try:
        mes_num  = int(mes_str)
        nome_mes = meses_pt[mes_num - 1]
    except:
        flash('Mês inválido.', 'danger')
        return redirect(url_for('relatorios.relatorios'))

    alunos = Aluno.query.filter(
        Aluno.ativo == True,
        db.extract('month', Aluno.data_nascimento) == mes_num
    ).order_by(db.extract('day', Aluno.data_nascimento)).all()

    col_labels = {'nome':'Nome','data_nascimento':'Data de Nascimento','idade':'Idade',
                  'telefone':'Telefone','email':'E-mail','plano':'Plano','cpf':'CPF'}

    def get_col(a, col):
        if col == 'nome':            return a.nome
        if col == 'data_nascimento': return a.data_nascimento.strftime('%d/%m/%Y') if a.data_nascimento else '—'
        if col == 'idade':
            if a.data_nascimento:
                return str(hoje.year - a.data_nascimento.year - (
                    (hoje.month, hoje.day) < (a.data_nascimento.month, a.data_nascimento.day)))
            return '—'
        if col == 'telefone': return a.telefone or '—'
        if col == 'email':    return a.email or '—'
        if col == 'plano':    return a.plano or '—'
        if col == 'cpf':      return a.documento if a.tipo_documento == 'cpf' else '—'
        return '—'

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([f'ANIVERSARIANTES — {nome_mes.upper()}'])
    writer.writerow([f'Emitido em: {hoje.strftime("%d/%m/%Y")}'])
    writer.writerow([])
    writer.writerow([col_labels.get(c,c) for c in colunas])
    for a in alunos: writer.writerow([get_col(a,c) for c in colunas])
    output.seek(0)
    return Response('\ufeff' + output.getvalue(), mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=aniversariantes_{nome_mes.lower()}_{hoje.year}.csv'})

@bp.route('/relatorios/exportar/pdf/aniversariantes')
@login_required
def exportar_pdf_aniversariantes():
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    from reportlab.pdfgen import canvas as pdfcanvas
    import io

    mes_str  = request.args.get('mes', '')
    colunas  = request.args.get('colunas', 'nome,data_nascimento,idade,telefone,email,plano').split(',')
    hoje     = datetime.utcnow().date()
    nome_ac  = get_param('nome_academia', 'Academia')
    meses_pt = ['Janeiro','Fevereiro','Março','Abril','Maio','Junho',
                'Julho','Agosto','Setembro','Outubro','Novembro','Dezembro']
    try:
        mes_num  = int(mes_str)
        nome_mes = meses_pt[mes_num - 1]
    except:
        flash('Mês inválido.', 'danger')
        return redirect(url_for('relatorios.relatorios'))

    alunos = Aluno.query.filter(
        Aluno.ativo == True,
        db.extract('month', Aluno.data_nascimento) == mes_num
    ).order_by(db.extract('day', Aluno.data_nascimento)).all()

    LARANJA = colors.HexColor(get_param('cor_principal', '#FF6B00'))
    SIDEBAR = colors.HexColor('#1a1a2e')
    CINZA   = colors.HexColor('#6c757d')

    col_labels = {'nome':'Nome','data_nascimento':'Nascimento','idade':'Idade',
                  'telefone':'Telefone','email':'E-mail','plano':'Plano','cpf':'CPF'}

    def get_col(a, col):
        if col == 'nome':            return a.nome
        if col == 'data_nascimento': return a.data_nascimento.strftime('%d/%m/%Y') if a.data_nascimento else '—'
        if col == 'idade':
            if a.data_nascimento:
                return str(hoje.year - a.data_nascimento.year - (
                    (hoje.month, hoje.day) < (a.data_nascimento.month, a.data_nascimento.day)))
            return '—'
        if col == 'telefone': return a.telefone or '—'
        if col == 'email':    return a.email or '—'
        if col == 'plano':    return a.plano or '—'
        if col == 'cpf':      return a.documento if a.tipo_documento == 'cpf' else '—'
        return '—'

    pw, ph = landscape(A4)
    endereco_ac_aniv  = get_param('endereco', '')
    telefone_ac_aniv  = get_param('telefone', '')
    hora_emissao_aniv = datetime.now().strftime('%d/%m/%Y às %H:%M')

    class AnivCanvas(pdfcanvas.Canvas):
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
                if endereco_ac_aniv:
                    self.drawString(0.8*cm, ph-1.0*cm, endereco_ac_aniv)
                info_linha = f'Aniversariantes de {nome_mes}'
                if telefone_ac_aniv:
                    info_linha += f'  |  Tel: {telefone_ac_aniv}'
                self.drawString(0.8*cm, ph-1.3*cm, info_linha)
                self.setFillColor(colors.HexColor('#aaaaaa'))
                self.drawRightString(pw-1*cm, ph-0.7*cm, f'Emitido em {hora_emissao_aniv}')
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
    st_h1  = ParagraphStyle('h1', fontSize=18, fontName='Helvetica-Bold', textColor=SIDEBAR, spaceAfter=4)
    st_body= ParagraphStyle('body', fontSize=9, textColor=CINZA)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4),
                            leftMargin=1*cm, rightMargin=1*cm,
                            topMargin=1.6*cm, bottomMargin=1.8*cm)
    els = []
    els.append(Paragraph(f'Aniversariantes — {nome_mes}', st_h1))
    els.append(HRFlowable(width='100%', thickness=2, color=LARANJA, spaceBefore=0, spaceAfter=10))
    els.append(Paragraph(f'{len(alunos)} aluno(s) fazem aniversário em {nome_mes}', st_body))
    els.append(Spacer(1, 0.4*cm))

    if alunos:
        headers = [col_labels.get(c,c) for c in colunas]
        data    = [headers] + [[get_col(a,c) for c in colunas] for a in alunos]
        n  = len(colunas)
        cw = (pw-2*cm)/n
        t  = Table(data, colWidths=[cw]*n, repeatRows=1)
        t.setStyle(TableStyle([
            ('BACKGROUND',(0,0),(-1,0),SIDEBAR),('TEXTCOLOR',(0,0),(-1,0),colors.white),
            ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),8),
            ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f8f9fa')]),
            ('GRID',(0,0),(-1,-1),0.3,colors.HexColor('#dee2e6')),
            ('PADDING',(0,0),(-1,-1),5),('VALIGN',(0,0),(-1,-1),'MIDDLE'),
        ]))
        els.append(t)
    else:
        els.append(Paragraph(f'Nenhum aluno ativo com aniversário em {nome_mes}.', st_body))

    doc.build(els, canvasmaker=AnivCanvas)
    buffer.seek(0)
    return Response(buffer.getvalue(), mimetype='application/pdf',
        headers={'Content-Disposition': f'attachment; filename=aniversariantes_{nome_mes.lower()}_{hoje.year}.pdf'})