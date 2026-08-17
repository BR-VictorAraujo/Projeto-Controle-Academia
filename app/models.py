from datetime import datetime
from app import db

class Usuario(db.Model):
    __tablename__ = 'usuarios'
    id               = db.Column(db.Integer, primary_key=True)
    nome             = db.Column(db.String(100), nullable=False)
    usuario          = db.Column(db.String(50), unique=True, nullable=False)
    senha_hash       = db.Column(db.String(256), nullable=False)
    perfil           = db.Column(db.String(20), default='operador')
    ativo            = db.Column(db.Boolean, default=True)
    criado_em        = db.Column(db.DateTime, default=datetime.utcnow)
    ultimo_acesso    = db.Column(db.DateTime)
    senha_alterada_em = db.Column(db.DateTime, default=datetime.utcnow)

class Aluno(db.Model):
    __tablename__ = 'alunos'
    id              = db.Column(db.Integer, primary_key=True)
    nome            = db.Column(db.String(100), nullable=False)
    tipo_documento  = db.Column(db.String(20), nullable=False, default='cpf')
    documento       = db.Column(db.String(30), nullable=False)
    telefone        = db.Column(db.String(20))
    email           = db.Column(db.String(120))
    data_nascimento = db.Column(db.Date)
    plano           = db.Column(db.String(50))
    vencimento      = db.Column(db.Date)
    ativo           = db.Column(db.Boolean, default=True)
    foto            = db.Column(db.String(200))
    endereco        = db.Column(db.String(300))
    biometria_status  = db.Column(db.String(20), default='pendente')
    biometria_2_status= db.Column(db.String(20), default='pendente')
    criado_em       = db.Column(db.DateTime, default=datetime.utcnow)
    biometria       = db.relationship('Biometria', backref='aluno', uselist=False)
    acessos         = db.relationship('RegistroAcesso', backref='aluno', lazy=True, cascade='all, delete-orphan')
    documentos      = db.relationship('DocumentoAluno', backref='aluno', lazy=True, cascade='all, delete-orphan')
    pagamentos      = db.relationship('Pagamento', backref='aluno', lazy=True, cascade='all, delete-orphan')
    assinaturas_recorrentes = db.relationship('AssinaturaRecorrente', backref='aluno', lazy=True, cascade='all, delete-orphan')

class Biometria(db.Model):
    __tablename__ = 'biometrias'
    id           = db.Column(db.Integer, primary_key=True)
    aluno_id     = db.Column(db.Integer, db.ForeignKey('alunos.id'), nullable=False)
    template     = db.Column(db.LargeBinary)  # dados do leitor biométrico
    cadastrado_em = db.Column(db.DateTime, default=datetime.utcnow)
    atualizado_em = db.Column(db.DateTime, onupdate=datetime.utcnow)

class RegistroAcesso(db.Model):
    __tablename__ = 'registros_acesso'
    id         = db.Column(db.Integer, primary_key=True)
    aluno_id   = db.Column(db.Integer, db.ForeignKey('alunos.id'), nullable=False)
    entrada_em = db.Column(db.DateTime, default=datetime.utcnow)
    tipo       = db.Column(db.String(20), default='biometria')  # biometria ou manual
    observacao = db.Column(db.String(200))

class Configuracao(db.Model):
    __tablename__ = 'configuracoes'
    id    = db.Column(db.Integer, primary_key=True)
    chave = db.Column(db.String(50), unique=True, nullable=False)
    valor = db.Column(db.String(200))
    descricao = db.Column(db.String(200))

class LogAuditoria(db.Model):
    __tablename__ = 'log_auditoria'
    id         = db.Column(db.Integer, primary_key=True)
    usuario    = db.Column(db.String(100), nullable=False)
    acao       = db.Column(db.String(100), nullable=False)
    detalhes   = db.Column(db.String(500))
    ip         = db.Column(db.String(45))
    criado_em  = db.Column(db.DateTime, default=datetime.utcnow)

class Plano(db.Model):
    __tablename__ = 'planos'
    id          = db.Column(db.Integer, primary_key=True)
    nome        = db.Column(db.String(50), nullable=False)
    valor       = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    descricao   = db.Column(db.String(200))
    dias_validade = db.Column(db.Integer, nullable=False, default=30)
    ativo       = db.Column(db.Boolean, default=True)
    criado_em   = db.Column(db.DateTime, default=datetime.utcnow)
    # Adicionado — Módulo API para Pagamentos (cobrança recorrente Cielo)
    permite_recorrencia = db.Column(db.Boolean, nullable=False, default=False)

class DocumentoAluno(db.Model):
    __tablename__ = 'documentos_aluno'
    id           = db.Column(db.Integer, primary_key=True)
    aluno_id     = db.Column(db.Integer, db.ForeignKey('alunos.id'), nullable=False)
    tipo         = db.Column(db.String(100), nullable=False)
    nome_arquivo = db.Column(db.String(200), nullable=False)
    caminho      = db.Column(db.String(300), nullable=False)
    criado_em    = db.Column(db.DateTime, default=datetime.utcnow)

class Pagamento(db.Model):
    __tablename__ = 'pagamentos'
    id              = db.Column(db.Integer, primary_key=True)
    aluno_id        = db.Column(db.Integer, db.ForeignKey('alunos.id'), nullable=False)
    valor           = db.Column(db.Numeric(10, 2), nullable=False)
    forma_pagamento = db.Column(db.String(20), nullable=False)
    status          = db.Column(db.String(20), default='pendente')
    data_vencimento = db.Column(db.Date, nullable=False)
    data_pagamento  = db.Column(db.Date)
    referencia      = db.Column(db.String(50))
    observacao      = db.Column(db.String(200))
    banco_pix       = db.Column(db.String(100))
    criado_em       = db.Column(db.DateTime, default=datetime.utcnow)
    criado_por      = db.Column(db.String(100))
    # Adicionado — Módulo API para Pagamentos (cobrança recorrente Cielo)
    origem                   = db.Column(db.String(20), nullable=False, default='manual')  # 'manual' | 'recorrente_gateway'
    assinatura_recorrente_id = db.Column(db.Integer, db.ForeignKey('assinatura_recorrente.id'))
    payment_id_gateway       = db.Column(db.String(50))  # PaymentId da Cielo — usado para deduplicação de webhook


# ─────────────────────────────────────────────────────────────────────────────
# Módulo API para Pagamentos — Cobrança Recorrente via Cartão (Cielo)
# ─────────────────────────────────────────────────────────────────────────────

class AssinaturaRecorrente(db.Model):
    __tablename__ = 'assinatura_recorrente'
    id           = db.Column(db.Integer, primary_key=True)
    aluno_id     = db.Column(db.Integer, db.ForeignKey('alunos.id'), nullable=False)
    plano_id     = db.Column(db.Integer, db.ForeignKey('planos.id'), nullable=False)

    # Gateway responsável por esta assinatura ('cielo' ou 'asaas') — salvo
    # por linha, não é o gateway "ativo" no momento (ver decisão [ARQUITETO]
    # sobre convivência entre gateways).
    gateway = db.Column(db.String(20), nullable=False, default='cielo')

    # Identificadores Cielo (Link de Pagamento)
    link_pagamento_id    = db.Column(db.String(50))   # ID do link, gerado na adesão
    url_link_pagamento    = db.Column(db.String(300))  # URL do link — pra recepção copiar/reenviar
    recurrent_payment_id = db.Column(db.String(50), unique=True)  # ID da série recorrente, após confirmação

    # Status: 'link_enviado', 'ativa', 'bloqueada_inadimplencia',
    #         'aguardando_decisao_ciclo', 'cancelada', 'expirada_cartao'
    status = db.Column(db.String(30), nullable=False, default='link_enviado')

    valor_mensal   = db.Column(db.Numeric(10, 2), nullable=False)
    data_inicio    = db.Column(db.Date, nullable=False)
    data_fim_ciclo = db.Column(db.Date)  # NULL = Mensal (sem prazo de permanência)

    proxima_cobranca = db.Column(db.Date)

    # Controle de retentativa — contagem própria (não depende só da Cielo)
    tentativas_negadas   = db.Column(db.SmallInteger, nullable=False, default=0)
    data_ultima_negativa = db.Column(db.DateTime)

    criado_em     = db.Column(db.DateTime, default=datetime.utcnow)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    cancelado_em  = db.Column(db.DateTime)
    cancelado_por = db.Column(db.String(100))  # segue padrão de Pagamento.criado_por (texto, não FK)

    plano       = db.relationship('Plano')
    pagamentos  = db.relationship('Pagamento', backref='assinatura_recorrente', lazy=True)


class LogIntegracaoPagamento(db.Model):
    __tablename__ = 'log_integracao_pagamento'
    id        = db.Column(db.BigInteger, primary_key=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    # 'webhook_recebido', 'consulta_disparada', 'consulta_respondida',
    # 'pagamento_lancado', 'cancelamento_solicitado', 'erro_parsing', 'alerta_disparado'
    tipo_evento = db.Column(db.String(30), nullable=False)
    nivel       = db.Column(db.String(10), nullable=False, default='INFO')  # INFO | WARNING | ERROR

    assinatura_recorrente_id = db.Column(db.Integer, db.ForeignKey('assinatura_recorrente.id'))
    recurrent_payment_id     = db.Column(db.String(50))
    payment_id_gateway       = db.Column(db.String(50))

    payload_bruto = db.Column(db.JSON)  # JSONB no Postgres — nunca None em erro de parsing
    resultado     = db.Column(db.Text)

    ip_origem = db.Column(db.String(45))