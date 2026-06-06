print("Iniciando seed...")
from app import create_app, db
from app.models import Aluno, RegistroAcesso, Pagamento, Plano
from datetime import datetime, date, timedelta
import random

app = create_app()

nomes = [
    "Ana Silva", "Bruno Santos", "Carlos Oliveira", "Daniela Costa", "Eduardo Lima",
    "Fernanda Souza", "Gabriel Pereira", "Helena Rodrigues", "Igor Alves", "Juliana Martins",
    "Kevin Fernandes", "Larissa Gomes", "Marcos Ribeiro", "Natalia Carvalho", "Otávio Araujo",
    "Patricia Barbosa", "Quintino Cardoso", "Rafaela Nascimento", "Samuel Vieira", "Tatiane Melo",
    "Ubirajara Pinto", "Vanessa Castro", "Wagner Teixeira", "Xenofonte Dias", "Yasmin Moreira",
    "Zuleika Ferreira", "Alexandre Cunha", "Beatriz Monteiro", "Caio Freitas", "Diana Lopes",
    "Emerson Ramos", "Fabiana Nunes", "Gustavo Correia", "Heloisa Mendes", "Ivan Cavalcanti",
    "Joana Andrade", "Leonardo Moura", "Mariana Borges", "Nelson Farias", "Olivia Machado",
    "Paulo Nogueira", "Queila Rezende", "Roberto Campos", "Silvia Braga", "Thiago Assis",
    "Ursula Batista", "Vitor Peixoto", "Wanderleia Duarte", "Xiomara Leite", "Yago Amaral",
    "Adriana Coelho", "Bernardo Rocha", "Camila Azevedo", "Diogo Pinheiro", "Elisa Guimarães",
    "Felipe Medeiros", "Giovana Soares", "Henrique Fonseca", "Ingrid Tavares", "Jorge Esteves",
    "Katia Macedo", "Lucas Silveira", "Miriam Almeida", "Nathan Queiroz", "Ophelie Bastos",
    "Pedro Lacerda", "Quirino Vasconcelos", "Roberta Siqueira", "Sergio Pacheco", "Tania Valente",
    "Ulisses Marques", "Vera Godinho", "Wilson Tolentino", "Xenia Paiva", "Yolanda Coutinho",
    "Zacarias Primo", "Amanda Lobato", "Bráulio Régis", "Cintia Valente", "Dalton Mourão",
    "Edna Pompeu", "Fabricio Bulhões", "Graziela Luz", "Herbert Magno", "Isadora Prata",
    "Jacinto Leal", "Keila Studart", "Leandro Mota", "Maisa Uchoa", "Nilton Sabino",
    "Orlanda Frota", "Pompeu Girão", "Quintina Jucá", "Romeu Benevides", "Simone Alencar",
    "Tobias Façanha", "Ualace Gondim", "Valdete Câmara", "Widson Holanda", "Xisto Mesquita",
    "Yanka Belchior", "Zoraide Ponte"
]

planos_nomes = ['Mensal', 'Trimestral', 'Semestral', 'Anual']

def cpf_fake(n):
    return f"{n:011d}"

def gerar_alunos():
    print("🏋️ Gerando 100 alunos...")
    hoje = date.today()
    planos = {p.nome: p for p in Plano.query.all()}

    for i, nome in enumerate(nomes):
        plano_nome = random.choice(planos_nomes)
        plano      = planos.get(plano_nome)
        dias_val   = plano.dias_validade if plano else 30

        # 70% ativos, 30% inativos
        ativo = random.random() > 0.3

        # Vencimento variado — alguns vencidos, outros em dia, outros futuros
        offset = random.randint(-60, 60)
        vencimento = hoje + timedelta(days=offset)

        # Data de cadastro nos últimos 6 meses
        criado_em = datetime.utcnow() - timedelta(days=random.randint(0, 180))

        aluno = Aluno(
            nome=nome,
            tipo_documento='cpf',
            documento=cpf_fake(i + 1000),
            telefone=f"(85) 9{random.randint(1000,9999)}-{random.randint(1000,9999)}",
            email=f"{nome.split()[0].lower()}{i}@email.com",
            plano=plano_nome,
            vencimento=vencimento,
            ativo=ativo,
            criado_em=criado_em
        )
        db.session.add(aluno)

    db.session.commit()
    print("✅ 100 alunos criados!")

def gerar_acessos():
    print("🚪 Gerando acessos do mês...")
    hoje  = date.today()
    alunos = Aluno.query.filter_by(ativo=True).all()

    # Gera acessos para os últimos 30 dias
    for dia_offset in range(30):
        dia = hoje - timedelta(days=dia_offset)

        # Entre 5 e 20 acessos por dia
        qtd = random.randint(5, 20)
        amostra = random.sample(alunos, min(qtd, len(alunos)))

        for aluno in amostra:
            hora   = random.randint(6, 21)
            minuto = random.randint(0, 59)
            entrada_em = datetime(dia.year, dia.month, dia.day, hora, minuto)
            tipo = random.choice(['manual', 'manual', 'biometria'])

            acesso = RegistroAcesso(
                aluno_id=aluno.id,
                entrada_em=entrada_em,
                tipo=tipo
            )
            db.session.add(acesso)

    db.session.commit()
    print("✅ Acessos gerados!")

def gerar_pagamentos():
    print("💰 Gerando pagamentos...")
    hoje  = date.today()
    alunos = Aluno.query.all()
    planos = {p.nome: float(p.valor) for p in Plano.query.all()}

    for aluno in alunos:
        valor = planos.get(aluno.plano, 0)
        if valor == 0:
            continue

        # Vencimento do pagamento = vencimento do plano do mês atual
        primeiro_dia_mes = date(hoje.year, hoje.month, 1)
        data_vencimento  = primeiro_dia_mes + timedelta(days=random.randint(0, 10))

        # 60% pagaram, 40% não pagaram
        pagou = random.random() > 0.4

        if pagou:
            data_pagamento  = data_vencimento - timedelta(days=random.randint(0, 5))
            forma_pagamento = random.choice(['dinheiro', 'pix'])
            status          = 'pago'
        else:
            data_pagamento  = None
            forma_pagamento = 'dinheiro'
            status          = 'vencido' if data_vencimento < hoje else 'pendente'

        pag = Pagamento(
            aluno_id=aluno.id,
            valor=valor,
            forma_pagamento=forma_pagamento,
            status=status,
            data_vencimento=data_vencimento,
            data_pagamento=data_pagamento,
            observacao=f'Referência {hoje.strftime("%m/%Y")}',
            criado_por='seed'
        )
        db.session.add(pag)

    db.session.commit()
    print("✅ Pagamentos gerados!")

if __name__ == '__main__':
    with app.app_context():
        gerar_alunos()
        gerar_acessos()
        gerar_pagamentos()
        print("\n🎉 Seed concluído! Banco populado com sucesso!")