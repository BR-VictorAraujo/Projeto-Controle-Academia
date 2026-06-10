from flask import Blueprint, render_template, redirect, url_for, request, flash
from app import db
from app.models import Aluno, RegistroAcesso
from datetime import datetime
from app.routes.auth import login_required, registrar_log, get_param, validar_documento

bp = Blueprint('alunos', __name__)

@bp.route('/alunos')
@login_required
def alunos():
    lista = Aluno.query.order_by(Aluno.nome).all()
    return render_template('alunos/lista.html', alunos=lista)

@bp.route('/alunos/novo', methods=['GET', 'POST'])
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
        endereco       = request.form.get('endereco', '')
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
            try:
                import uuid, base64, os
                pasta    = os.path.join('app', 'static', 'fotos')
                os.makedirs(pasta, exist_ok=True)
                img_data = foto_base64.split(',')[1]
                filename = f'aluno_{uuid.uuid4().hex[:8]}.jpg'
                with open(os.path.join(pasta, filename), 'wb') as f:
                    f.write(base64.b64decode(img_data))
                foto_path = f'/static/fotos/{filename}'
            except Exception:
                foto_path = None  # ignora erro de foto, não impede o cadastro

        aluno = Aluno(nome=nome, tipo_documento=tipo_documento, documento=documento,
                      telefone=telefone, email=email, plano=plano,
                      vencimento=vencimento, data_nascimento=data_nascimento,
                      ativo=ativo, foto=foto_path, endereco=endereco)
        db.session.add(aluno)
        db.session.commit()

        from app.models import DocumentoAluno
        import os, uuid
        MAX_UPLOAD_MB = 10
        for tipo_doc, arquivo in zip(request.form.getlist('doc_tipo[]'), request.files.getlist('doc_arquivo[]')):
            if arquivo and arquivo.filename and tipo_doc:
                ext = arquivo.filename.rsplit('.', 1)[-1].lower()
                if ext in ['pdf', 'jpg', 'jpeg', 'png']:
                    arquivo.seek(0, 2)
                    tamanho_mb = arquivo.tell() / (1024 * 1024)
                    arquivo.seek(0)
                    if tamanho_mb > MAX_UPLOAD_MB:
                        flash(f'Arquivo {arquivo.filename} muito grande (máx {MAX_UPLOAD_MB}MB).', 'warning')
                        continue
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
        return redirect(url_for('alunos.alunos'))

    return render_template('alunos/novo.html', planos=planos)

@bp.route('/alunos/<int:id>')
@login_required
def detalhes_aluno(id):
    from app.models import DocumentoAluno
    aluno      = Aluno.query.get_or_404(id)
    acessos    = RegistroAcesso.query.filter_by(aluno_id=id).order_by(RegistroAcesso.entrada_em.desc()).limit(20).all()
    documentos = DocumentoAluno.query.filter_by(aluno_id=id).order_by(DocumentoAluno.criado_em.desc()).all()
    return render_template('alunos/detalhes.html', aluno=aluno, acessos=acessos, documentos=documentos)

@bp.route('/alunos/<int:id>/editar', methods=['GET', 'POST'])
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
        aluno.endereco        = request.form.get('endereco', '')
        aluno.data_nascimento = datetime.strptime(data_nasc_str, '%Y-%m-%d').date() if data_nasc_str else None

        aluno.vencimento = datetime.strptime(vencimento_str, '%Y-%m-%d').date() if vencimento_str else None

        foto_base64 = request.form.get('foto_base64')
        if foto_base64 and foto_base64.startswith('data:image'):
            try:
                import uuid, base64, os
                # Remove foto antiga do disco
                if aluno.foto:
                    foto_antiga = os.path.join('app', aluno.foto.lstrip('/'))
                    if os.path.exists(foto_antiga):
                        os.remove(foto_antiga)
                pasta    = os.path.join('app', 'static', 'fotos')
                os.makedirs(pasta, exist_ok=True)
                img_data = foto_base64.split(',')[1]
                filename = f'aluno_{uuid.uuid4().hex[:8]}.jpg'
                with open(os.path.join(pasta, filename), 'wb') as f:
                    f.write(base64.b64decode(img_data))
                aluno.foto = f'/static/fotos/{filename}'
            except Exception:
                pass  # ignora erro de foto, não impede a edição

        db.session.commit()
        registrar_log('editou aluno', f'ID: {id} | Nome: {aluno.nome}')
        flash('Aluno atualizado com sucesso!', 'success')
        return redirect(url_for('alunos.alunos'))

    return render_template('alunos/editar.html', aluno=aluno, planos=planos, documentos=documentos)

@bp.route('/alunos/<int:id>/excluir')
@login_required
def excluir_aluno(id):
    import os
    from app.models import DocumentoAluno
    aluno = Aluno.query.get_or_404(id)
    nome  = aluno.nome

    # Remove foto do disco
    if aluno.foto:
        try:
            foto_path = os.path.join('app', aluno.foto.lstrip('/'))
            if os.path.exists(foto_path):
                os.remove(foto_path)
        except Exception:
            pass

    # Remove documentos do disco
    documentos = DocumentoAluno.query.filter_by(aluno_id=id).all()
    for doc in documentos:
        try:
            doc_path = os.path.join('app', doc.caminho.lstrip('/'))
            if os.path.exists(doc_path):
                os.remove(doc_path)
        except Exception:
            pass

    registrar_log('excluiu aluno', f'ID: {id} | Nome: {nome}')
    db.session.delete(aluno)
    db.session.commit()
    flash(f'Aluno {nome} excluído com sucesso.', 'success')
    return redirect(url_for('alunos.alunos'))

@bp.route('/alunos/<int:id>/documentos/upload', methods=['POST'])
@login_required
def upload_documento(id):
    import os, uuid
    from app.models import DocumentoAluno
    aluno   = Aluno.query.get_or_404(id)
    tipo    = request.form.get('tipo')
    arquivo = request.files.get('arquivo')

    if not arquivo or not tipo:
        flash('Preencha o tipo e selecione um arquivo.', 'danger')
        return redirect(url_for('alunos.editar_aluno', id=id))

    ext = arquivo.filename.rsplit('.', 1)[-1].lower()
    if ext not in ['pdf', 'jpg', 'jpeg', 'png']:
        flash('Formato inválido. Use PDF, JPG ou PNG.', 'danger')
        return redirect(url_for('alunos.editar_aluno', id=id))

    # Valida tamanho máximo 10MB
    arquivo.seek(0, 2)
    tamanho_mb = arquivo.tell() / (1024 * 1024)
    arquivo.seek(0)
    if tamanho_mb > 10:
        flash('Arquivo muito grande. Tamanho máximo: 10MB.', 'danger')
        return redirect(url_for('alunos.editar_aluno', id=id))

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
    return redirect(url_for('alunos.editar_aluno', id=id))

@bp.route('/alunos/<int:id>/documentos/<int:doc_id>/excluir')
@login_required
def excluir_documento(id, doc_id):
    import os
    from app.models import DocumentoAluno
    doc = DocumentoAluno.query.get_or_404(doc_id)
    try:
        filepath = os.path.join('app', doc.caminho.lstrip('/'))
        if os.path.exists(filepath):
            os.remove(filepath)
    except Exception:
        pass
    db.session.delete(doc)
    db.session.commit()
    registrar_log('excluiu documento', f'Aluno ID: {id} | Arquivo: {doc.nome_arquivo}')
    flash('Documento excluído com sucesso!', 'success')
    return redirect(url_for('alunos.editar_aluno', id=id))